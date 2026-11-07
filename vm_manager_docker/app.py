from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import threading
import time
from typing import Annotated, Literal
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backend import Backend, COMMANDS, CommandError
from .jobs import JobStore, QueueFull, Worker
from .settings import SetupError, SetupRequest, save_setup
from .terminals import close_terminals, register_terminal_routes
from vm_manager.transport import ManagerError


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(min_length=1, max_length=40)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    wait: int = Field(default=120, ge=5, le=600)
    memory: int | None = Field(default=None, ge=512, le=1048576)
    cpus: int | None = Field(default=None, ge=1, le=256)
    user: str | None = None
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    access: Literal["tunnel", "lan"] | None = None
    delete_disks: bool = False
    controller: str = "SATA"
    port: int = Field(default=1, ge=0, le=29)


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["iso", "kali", "import"]
    name: str = Field(min_length=1, max_length=64)
    iso: str | None = None
    source: str | None = None
    sha256: str | None = None
    version: str | None = None
    os_type: str = "Debian_64"
    firmware: Literal["bios", "efi"] = "bios"
    memory: int | None = Field(default=None, ge=512, le=1048576)
    cpus: int | None = Field(default=None, ge=1, le=256)
    disk: int | None = Field(default=None, ge=10, le=2097152)
    user: str | None = None
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    access: Literal["tunnel", "lan"] = "tunnel"
    start: bool = True

    @model_validator(mode="after")
    def required_source(self):
        if self.kind == "iso" and not self.iso:
            raise ValueError("iso is required for ISO deployment")
        if self.kind == "import" and not self.source:
            raise ValueError("source is required for appliance import")
        if self.kind == "kali" and not self.start:
            raise ValueError("Kali SSH provisioning requires start=true")
        return self

    def argv(self):
        command = {"iso": "create", "kali": "deploy-kali", "import": "import"}[self.kind]
        argv = [command, self.name]
        keys = ["memory", "cpus", "user", "ssh_port", "access"]
        keys += {"iso": ["iso", "sha256", "os_type", "firmware", "disk"],
                 "kali": ["version"], "import": ["source", "sha256"]}[self.kind]
        for key in keys:
            value = getattr(self, key)
            if value is not None:
                argv += ["--" + key.replace("_", "-"), str(value)]
        if not self.start:
            argv.append("--no-start")
        return argv


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "restore", "delete"]
    snapshot: str = Field(min_length=1, max_length=200)


class AutostartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["enable", "disable", "status"]


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vm_uuid: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=4096)


def create_app(*, config_path=None, data_dir=None, backend=None):
    data_dir = Path(data_dir or os.environ.get("VM_MANAGER_DATA", "/data"))
    config_path = Path(config_path or os.environ.get("VM_MANAGER_CONFIG", str(data_dir / "config.toml")))
    public_key = Path(os.environ.get("VM_MANAGER_PUBLIC_KEY", str(data_dir / "ssh/id_ed25519.pub")))
    cache = {}
    cache_lock = threading.Lock()
    setup_lock = asyncio.Lock()

    def invalidate():
        with cache_lock:
            # Retain old values to serve while a subsequent job is busy.
            for key in cache:
                cache[key]["expires"] = 0

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        app.state.backend = backend
        app.state.worker = None
        if (app.state.backend is None and config_path.is_file()
                and (data_dir / "ssh/id_ed25519").is_file() and public_key.is_file()):
            try:
                app.state.backend = Backend(config_path, public_key, uploads)
            except ManagerError:
                # A template/incomplete config should open setup, not crash startup.
                pass
        app.state.store = JobStore(data_dir / "jobs.sqlite3")
        app.state.store.recover()
        if app.state.backend is not None:
            start_worker()
        try:
            yield
        finally:
            await close_terminals(app)
            if app.state.worker is not None:
                app.state.worker.stop()

    def start_worker():
        app.state.worker = Worker(app.state.store, app.state.backend, invalidate)
        app.state.worker.start()

    app = FastAPI(title="VM Manager Docker", version="0.1.0", lifespan=lifespan,
                  description="Control remote VirtualBox VMs. Mutations return persistent background jobs; poll /api/jobs/{id}.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    register_terminal_routes(app)

    @app.middleware("http")
    async def browser_origin_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.headers.get('host', '')}":
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        if (request.url.path.startswith("/api/") and request.url.path not in ("/api/health", "/api/config", "/api/setup")
                and app.state.backend is None):
            return JSONResponse({"detail": "Complete first-run setup in the dashboard or run vmctl setup"}, status_code=409,
                                headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(CommandError)
    async def command_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(QueueFull)
    async def queue_full(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation responses must not echo an uploaded private key or password.
        return JSONResponse({"detail": [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                                        for error in exc.errors()]}, status_code=422)

    @app.get("/api/setup", tags=["Setup"])
    async def setup_status():
        return {"configured": app.state.backend is not None}

    @app.post("/api/setup", tags=["Setup"])
    async def setup(request: SetupRequest):
        async with setup_lock:
            if app.state.backend is not None:
                raise HTTPException(409, "This installation is already configured")
            try:
                await asyncio.to_thread(save_setup, request, config_path, data_dir)
                app.state.backend = Backend(config_path, public_key, data_dir / "uploads")
            except (SetupError, ManagerError) as exc:
                raise HTTPException(422, str(exc)) from exc
            start_worker()
        return {"configured": True}

    def submit(argv, *, upload=False):
        if not upload:
            app.state.backend.validate(argv)
        job = app.state.store.submit(argv)
        app.state.worker.wake()
        return job

    async def read(argv, *, ttl=10):
        key = tuple(argv)
        with cache_lock:
            saved = cache.get(key)
            if saved and (saved["expires"] > time.monotonic() or app.state.store.busy()):
                return {"data": saved["data"], "stale": saved["expires"] <= time.monotonic()}
        if app.state.store.busy():
            raise HTTPException(503, "A VM operation is running; status will refresh when it finishes")
        try:
            result = await asyncio.to_thread(app.state.backend.run, argv)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        with cache_lock:
            cache[key] = {"data": result, "expires": time.monotonic() + ttl}
        return {"data": result, "stale": False}

    @app.get("/api/health", tags=["Service"])
    async def health():
        return {"status": "ok", "service": "vm-manager-docker"}

    @app.get("/api/config", tags=["Service"])
    async def config():
        if app.state.backend is None:
            return {"configured": False}
        current = app.state.backend.config
        return {"configured": True, "host": {key: current["host"][key] for key in ("hostname", "username", "port", "vm_root")},
                "defaults": current["defaults"], "commands": COMMANDS}

    @app.get("/api/host", tags=["Host"])
    async def host():
        return await read(["doctor"], ttl=30)

    @app.get("/api/os-types", tags=["Host"])
    async def os_types():
        return await read(["os-types"], ttl=300)

    @app.get("/api/vms", tags=["VMs"])
    async def list_vms():
        return await read(["list"])

    @app.post("/api/vms", status_code=202, tags=["VMs"])
    async def deploy(request: DeploymentRequest):
        return submit(request.argv())

    @app.get("/api/vms/{name}", tags=["VMs"])
    async def vm_info(name: str):
        return await read(["info", name])

    @app.get("/api/vms/{name}/ssh", tags=["VMs"])
    async def ssh_info(name: str, user: str | None = None):
        argv = ["ssh-info", name]
        if user:
            argv += ["--user", user]
        return await read(argv)

    @app.get("/api/vms/{name}/credentials", tags=["Credentials"])
    async def credentials(name: str, vm_uuid: str | None = None):
        """Return the saved login password; this endpoint does not contact the guest."""
        saved = app.state.store.credentials(name, vm_uuid)
        if saved is None:
            raise HTTPException(404, "No password recorded for this VM. Record its existing credentials or deploy Kali to generate them.")
        return saved

    @app.put("/api/vms/{name}/credentials", tags=["Credentials"])
    async def record_credentials(name: str, request: CredentialsRequest):
        """Record a known password; this does not change the account inside the VM."""
        return app.state.store.record_credentials(name, request.vm_uuid, request.username, request.password)

    @app.post("/api/vms/{name}/actions/{action}", status_code=202, tags=["VMs"])
    async def action(name: str, action: Literal["start", "stop", "restart", "pause", "resume", "save", "configure", "ssh-configure", "eject-iso", "remove"], request: ActionRequest):
        argv = [action, name]
        if action in ("stop", "restart"):
            argv += ["--wait", str(request.wait)]
        if action == "stop" and request.force:
            argv.append("--force")
        keys = {"configure": ("memory", "cpus"), "ssh-configure": ("user", "ssh_port", "access"),
                "eject-iso": ("controller", "port")}.get(action, ())
        for key in keys:
            value = getattr(request, key)
            if value is not None:
                argv += ["--" + key.replace("_", "-"), str(value)]
        if action == "remove" and request.delete_disks:
            argv.append("--delete-disks")
        if action == "configure" and not (request.memory or request.cpus):
            raise HTTPException(422, "Specify memory and/or cpus")
        return submit(argv)

    @app.get("/api/vms/{name}/snapshots", tags=["Snapshots"])
    async def list_snapshots(name: str):
        return await read(["snapshot", "list", name])

    @app.post("/api/vms/{name}/snapshots", status_code=202, tags=["Snapshots"])
    async def snapshots(name: str, request: SnapshotRequest):
        return submit(["snapshot", request.action, name, request.snapshot])

    @app.post("/api/vms/{name}/autostart", status_code=202, tags=["VMs"])
    async def autostart(name: str, request: AutostartRequest):
        return submit(["autostart", name, request.mode])

    @app.post("/api/commands", status_code=202, tags=["CLI"])
    async def commands(request: CommandRequest):
        """Run an existing VM Manager command as an argument array, never through a shell."""
        return submit(request.argv)

    @app.get("/api/jobs", tags=["Jobs"])
    async def jobs():
        return app.state.store.list()

    @app.get("/api/jobs/{job_id}", tags=["Jobs"])
    async def job(job_id: str):
        found = app.state.store.get(job_id)
        if found is None:
            raise HTTPException(404, "Job not found")
        return found

    @app.post("/api/uploads", status_code=202, tags=["Uploads"])
    async def upload(file: Annotated[UploadFile, File(description="Local ISO/appliance to transfer to Windows")]):
        filename = Path(file.filename or "upload.bin").name
        # Preserve the extension for host-side create/import detection.
        import re
        filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)[:180]
        path = data_dir / "uploads" / f"{uuid.uuid4().hex}-{filename}"
        size = 0
        maximum = int(os.environ.get("VM_MANAGER_MAX_UPLOAD_BYTES", 16 * 1024**3))
        try:
            with path.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, f"Upload exceeds {maximum} bytes")
                    stream.write(chunk)
            return submit(["upload", str(path)], upload=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static / "index.html")

    return app


app = create_app()
