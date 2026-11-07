# Packaged backend

This directory contains the generic `virtualbox-vm-manager` wheel. It includes the Python CLI and PowerShell backend without installation settings, SSH keys or personal data, so this Docker project builds independently of a separate backend source directory.

To refresh the bundled backend after changing that project, run from this project:

```bash
uv build --wheel --out-dir ./vendor ../vm-manager
docker compose up -d --build
```

When its package version changes, remove superseded wheels so that only one backend wheel is installed.
