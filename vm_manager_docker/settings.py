"""First-run settings. Personal values are written only to runtime storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from vm_manager.transport import ManagerError, load_config


class SetupError(ValueError):
    pass


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    hostname: str = Field(min_length=1, max_length=253)
    username: str = Field(min_length=1, max_length=128)
    port: int = Field(default=22, ge=1, le=65535)
    vm_root: str = Field(default="C:\\VMs", min_length=3, max_length=240)
    vboxmanage: str = Field(default="C:\\Program Files\\Oracle\\VirtualBox\\VBoxManage.exe", min_length=3, max_length=240)
    sevenzip: str = Field(default="C:\\Program Files\\7-Zip\\7z.exe", min_length=3, max_length=240)
    private_key: SecretStr
    public_key: SecretStr | None = None
    known_hosts: SecretStr | None = None


def config_text(settings: SetupRequest):
    values = {key: getattr(settings, key).strip() if isinstance(getattr(settings, key), str) else getattr(settings, key)
              for key in ("hostname", "username", "port", "vm_root", "vboxmanage", "sevenzip")}
    values["connect_timeout"] = 10
    for key in ("vm_root", "vboxmanage", "sevenzip"):
        if not re.match(r"^[A-Za-z]:[\\/]", values[key]) or any(ord(char) < 32 for char in values[key]):
            raise SetupError(f"{key} must be an absolute Windows drive path")
    lines = ["[host]"]
    lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()]
    lines += ["", "[defaults]", "memory = 4096", "cpus = 2", "disk = 60", "ssh_port_start = 2222", 'ssh_access = "tunnel"', ""]
    return "\n".join(lines)


def write_private(path: Path, value: str):
    """Atomic replacement with owner-only permissions, even for an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_setup(settings: SetupRequest, config_path: Path, data_dir: Path):
    text = config_text(settings)
    data_dir.mkdir(parents=True, exist_ok=True)
    # Validate with the backend's actual config parser before persisting anything.
    with tempfile.TemporaryDirectory(prefix=".setup-check-", dir=data_dir) as directory:
        candidate = Path(directory) / "config.toml"
        candidate.write_text(text, encoding="utf-8")
        try:
            load_config(candidate)
        except ManagerError as exc:
            raise SetupError("Invalid host name, SSH account or configuration value") from exc
        key_path = Path(directory) / "identity"
        private = settings.private_key.get_secret_value().replace("\r\n", "\n").strip() + "\n"
        write_private(key_path, private)
        try:
            derived = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(key_path)],
                                     capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError("Unable to validate the SSH private key") from exc
        if derived.returncode != 0:
            raise SetupError("Provide a valid SSH private key usable without an interactive passphrase")
        public = derived.stdout.strip()
        if settings.public_key and settings.public_key.get_secret_value().strip():
            supplied = settings.public_key.get_secret_value().strip()
            if "\n" in supplied or supplied.split()[:2] != public.split()[:2]:
                raise SetupError("The public key does not match the private key")
            public = supplied
    ssh_dir = data_dir / "ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    write_private(ssh_dir / "id_ed25519", private)
    write_private(ssh_dir / "id_ed25519.pub", public + "\n")
    if settings.known_hosts:
        write_private(ssh_dir / "known_hosts", settings.known_hosts.get_secret_value().strip() + "\n")
    elif not (ssh_dir / "known_hosts").exists():
        write_private(ssh_dir / "known_hosts", "")
    # The config is the commit point; before it exists, the app remains unconfigured.
    write_private(config_path, text)
