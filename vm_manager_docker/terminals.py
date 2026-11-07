"""Interactive SSH over WebSockets. Terminal traffic is never persisted."""
from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
from typing import Literal
import uuid

from fastapi import WebSocket, WebSocketDisconnect
from vm_manager.transport import Remote, ssh_command


def build_command(backend, target, name=None, shell="powershell", user=None):
    if target == "host":
        command = Remote(backend.config).ssh_base()
        command = ["BatchMode=no" if argument == "BatchMode=yes" else argument for argument in command]
        command.insert(1, "-tt")
        if shell not in ("powershell", "cmd"):
            raise ValueError("Unsupported Windows shell")
        command.append("powershell.exe -NoLogo -NoProfile" if shell == "powershell" else "cmd.exe")
        return command
    if target != "vm" or not name:
        raise ValueError("Choose a VM for this terminal")
    info = backend.run(["info", name])
    if info["state"] != "running":
        raise ValueError("Start the VM before opening its terminal")
    command = ssh_command(backend.config, info, user)
    command[1:1] = ["-tt", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
                   "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    return command


class PTYSession:
    def __init__(self, command, cols=100, rows=30):
        self.master, slave = pty.openpty()
        self.process = None
        try:
            self.resize(cols, rows)
            # No preexec_fn: the API is multi-threaded. A small exec helper owns
            # the controlling TTY so SSH can also prompt for guest passwords.
            self.process = subprocess.Popen(
                [sys.executable, "-m", "vm_manager_docker.terminal_child", *command],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            os.set_blocking(self.master, False)
        except Exception:
            os.close(self.master)
            self.master = None
            raise
        finally:
            os.close(slave)

    def resize(self, cols, rows):
        if not 2 <= cols <= 500 or not 2 <= rows <= 200:
            raise ValueError("Terminal size must be 2–500 columns and 2–200 rows")
        if self.master is not None:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def read(self):
        if self.master is None:
            return b""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        descriptor = self.master

        def ready():
            if future.done():
                return
            try:
                data = os.read(descriptor, 65536)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if not future.done():
                future.set_result(data)

        loop.add_reader(descriptor, ready)
        try:
            return await future
        finally:
            loop.remove_reader(descriptor)

    async def write(self, data):
        loop = asyncio.get_running_loop()
        while data and self.master is not None:
            try:
                count = os.write(self.master, data)
                data = data[count:]
            except BlockingIOError:
                future = loop.create_future()
                descriptor = self.master

                def writable():
                    if not future.done():
                        future.set_result(None)

                loop.add_writer(descriptor, writable)
                try:
                    await future
                finally:
                    loop.remove_writer(descriptor)

    def close(self):
        if self.master is not None:
            os.close(self.master)
            self.master = None
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.process.wait(timeout=2)


async def close_terminals(app):
    sessions = [session for session in app.state.terminal_sessions.values() if session is not None]
    await asyncio.gather(*(asyncio.to_thread(session.close) for session in sessions), return_exceptions=True)


def register_terminal_routes(app):
    app.state.terminal_sessions = {}

    @app.websocket("/api/terminals/ws")
    async def terminal(websocket: WebSocket, target: Literal["host", "vm"] = "host", name: str | None = None,
                       shell: Literal["powershell", "cmd"] = "powershell", user: str | None = None,
                       cols: int = 100, rows: int = 30):
        origin = websocket.headers.get("origin")
        scheme = "https" if websocket.url.scheme == "wss" else "http"
        if origin and origin.rstrip("/") != f"{scheme}://{websocket.headers.get('host', '')}":
            await websocket.close(code=1008, reason="Cross-origin terminals are not allowed")
            return
        if app.state.backend is None:
            await websocket.close(code=1008, reason="Complete first-run setup")
            return
        await websocket.accept()
        if len(app.state.terminal_sessions) >= int(os.environ.get("VM_MANAGER_MAX_TERMINALS", "8")):
            await websocket.send_json({"type": "error", "message": "Too many active terminals; close an existing tab"})
            await websocket.close(code=1013)
            return
        session_id = str(uuid.uuid4())
        app.state.terminal_sessions[session_id] = None
        session = None
        tasks = []
        try:
            await websocket.send_json({"type": "status", "state": "connecting"})
            command = await asyncio.to_thread(build_command, app.state.backend, target, name, shell, user)
            session = PTYSession(command, cols, rows)
            app.state.terminal_sessions[session_id] = session
            await websocket.send_json({"type": "status", "state": "connected", "id": session_id})

            async def output():
                while data := await session.read():
                    await websocket.send_bytes(data)
                code = await asyncio.to_thread(session.process.wait, 2)
                await websocket.send_json({"type": "exit", "code": code})
                await websocket.close()

            async def incoming():
                while True:
                    message = await websocket.receive_json()
                    if not isinstance(message, dict):
                        raise ValueError("Invalid terminal message")
                    if message.get("type") == "resize":
                        session.resize(int(message["cols"]), int(message["rows"]))
                    elif message.get("type") == "input" and isinstance(message.get("data"), str):
                        data = message["data"].encode("latin1" if message.get("encoding") == "binary" else "utf-8")
                        if len(data) > 65536:
                            raise ValueError("Terminal input message is too large")
                        await session.write(data)
                    else:
                        raise ValueError("Invalid terminal message")

            tasks = [asyncio.create_task(output()), asyncio.create_task(incoming())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, ConnectionError):
            pass
        except Exception as exc:
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
                await websocket.close(code=1011)
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            app.state.terminal_sessions.pop(session_id, None)
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                if session is not None:
                    # A disconnected WebSocket may cancel its handler while it is
                    # unwinding. Let process-group cleanup complete regardless.
                    await asyncio.shield(asyncio.to_thread(session.close))
