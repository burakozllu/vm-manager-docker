# VM Manager Docker

Web dashboard, REST API and HTTP command-line client for managing a Windows VirtualBox host over SSH.

- **Dashboard:** <http://127.0.0.1:8080>
- **API explorer:** <http://127.0.0.1:8080/docs>
- **OpenAPI schema:** <http://127.0.0.1:8080/openapi.json>

The container controls VirtualBox on the Windows PC over SSH. VM execution and VM disks remain on that PC. This project bundles a wheel of the existing VM Manager CLI and PowerShell backend.

## Start and stop

From this project directory:

```bash
docker compose up -d --build --wait
docker compose ps
docker compose logs -f app
docker compose stop
docker compose start
```

`restart: unless-stopped` restarts the service after Docker/host restarts. The dashboard/API listen on **this machine's loopback interface only**, without a login requirement. The Windows host still needs to be reachable for VM operations.

## First launch

The application starts without a host address, account or key. Open the dashboard and complete **First-run setup**:

1. Enter your Windows host name/address and SSH username/port.
2. Choose the Windows folder for VM storage and, if necessary, adjust the VirtualBox/7-Zip executable paths.
3. Select your SSH private key file. An optional matching public key and known-hosts file may also be uploaded.
4. Save the configuration. The dashboard opens immediately, and future launches reuse those settings.

The Windows PC must already have OpenSSH, VirtualBox and 7-Zip installed, with your public key authorized for its SSH account. Use an SSH key that can authenticate non-interactively. When no public-key file is provided, the app derives one from the private key.

Terminal setup is also available:

```bash
./vmctl setup
```

Personal values are stored only in `/data/config.toml` and `/data/ssh/` inside the persistent Docker volume. They are not baked into the image or placed in the shared source. An incomplete/missing config opens setup instead of failing startup. VM endpoints return HTTP 409 until setup is complete.

## Dashboard

- Live inventory and host CPU/RAM diagnostics.
- Start, graceful stop, restart, pause/resume and save VM state.
- Deploy Kali with key-based SSH, create virtual hardware from an ISO, or import VirtualBox/OVA appliances.
- Upload a local ISO/appliance and transfer it to Windows with checksum verification.
- View/copy guest SSH connection commands and configure tunnel/LAN forwards.
- View saved usernames/passwords with each VM's **Credentials** button; record known passwords for existing/imported guests.
- Change resources, eject installer ISOs, manage snapshots and Windows sign-in autostart tasks.
- Unregister VMs, optionally deleting their files.
- Persistent background-job history with results and errors.
- Browser SSH terminals for Windows PowerShell/CMD and running VMs, with retro CRT themes.

ISO deployment boots an installer; finish the guest OS installation in its VirtualBox console on Windows and enable its SSH server. `deploy-kali` performs automatic SSH provisioning. Its Activity entry shows a labeled username/password card and a **Copy password** button. An open job dialog refreshes automatically when deployment finishes.

## HTTP CLI

`vmctl` uses only Python's standard library, so it runs directly on the Linux host:

```bash
./vmctl list
./vmctl info Kali
./vmctl start Kali
./vmctl ssh-info Kali
./vmctl credentials Kali
./vmctl snapshot list Kali
./vmctl autostart Kali status
./vmctl --no-wait deploy-kali Kali-Lab
./vmctl jobs
./vmctl job JOB_ID
./vmctl upload ~/Downloads/installer.iso
```

Commands wait for their background job by default and print JSON results. `--no-wait` prints the submitted job immediately. Interrupting the client stops waiting; it does not cancel the server job. Select another local port with `--url http://127.0.0.1:PORT` or `VM_MANAGER_URL`.

`ssh-info` supplies a native SSH command for your local terminal. The dashboard's **Terminals** page opens interactive sessions directly in the browser.

## Browser terminals and retro appearance

Open **Terminals** in the sidebar for Windows **PowerShell** or **CMD**. Open a guest with the inventory's **Terminal** button, or select a running VM on the Terminals page. If its SSH username is not recorded, enter it in the guest-user field.

Sessions use your configured SSH identity. SSH can also prompt interactively for a guest password, and a guest's SSH server must be running. Tabs support terminal resizing, reconnect, selection copy and paste. Closing a tab or reloading the page ends that SSH session. Terminal input/output is streamed only and is not written to jobs or transcript files.

Display choices include **retro amber**, **retro green**, **ice monochrome**, and **modern**, with toggleable CRT scanlines/glow. The styling is inspired by [cool-retro-term](https://github.com/Swordfish90/cool-retro-term); this is a browser-native xterm.js terminal rather than the native Qt application. See `THIRD_PARTY_NOTICES.md` for component licenses.

The streaming endpoint is WebSocket `/api/terminals/ws?target=host&shell=powershell` or `?target=vm&name=VM_NAME`. It requires completed setup and same-origin browser access. The default maximum is eight concurrent sessions (`VM_MANAGER_MAX_TERMINALS`).

The original direct SSH CLI is also installed **inside** the container:

```bash
docker compose exec app vm-manager --json list
docker compose exec app vm-manager start Kali
```

## VM passwords

Use **Virtual machines → Credentials** for a current VM's saved login/sudo password. Use **Activity → View credentials** for the initial credentials from a successful Kali deployment. The password is shown as a normal, selectable field with a copy button; expanding raw JSON is unnecessary.

```bash
./vmctl credentials Kali
curl -s http://127.0.0.1:8080/api/vms/Kali/credentials
```

Generated credentials are persisted automatically when a deployment succeeds. A startup migration imports passwords from existing deployment jobs, including jobs older than the recent-50 Activity list. Historical job credentials remain available after a VM is removed; the current per-VM record is removed when its VM is unregistered. A new ISO/appliance VM reusing a previous name does not inherit the old VM's password record.

For an existing/imported guest, open **Credentials → Record or update a known password**. This records the supplied username/password; it does not change the account inside the guest. The API equivalent is `PUT /api/vms/{name}/credentials` with `vm_uuid`, `username` and `password`. The `GET` endpoint accepts `?vm_uuid=UUID` to ensure a stored password belongs to the expected machine identity. Passwords changed independently inside a guest must also be updated in the saved record.

The database stores these records in the `vm_credentials` table of `/data/jobs.sqlite3`, alongside job history in the Docker data volume. Password values are plaintext in this local database, as they are in deployment job results.

## REST API examples

### List machines

```bash
curl -s http://127.0.0.1:8080/api/vms
```

Read endpoints return `{"data": ..., "stale": false}`. During a long operation, a cached result may have `stale: true`. If no cached result exists, the endpoint returns HTTP 503 until the operation finishes.

### Start a VM

```bash
curl -s -X POST http://127.0.0.1:8080/api/vms/Kali/actions/start \
  -H 'Content-Type: application/json' -d '{}'
```

Mutation endpoints return **HTTP 202** with a job object:

```json
{"id":"JOB_ID","argv":["start","Kali"],"status":"queued"}
```

Poll until `succeeded`, `failed` or `interrupted`:

```bash
curl -s http://127.0.0.1:8080/api/jobs/JOB_ID
```

Completed jobs contain `result` or `error`, plus timestamps. Lifecycle actions include `start`, `stop`, `restart`, `pause`, `resume`, `save`, `configure`, `ssh-configure`, `eject-iso` and `remove`.

### Call an existing CLI command

```bash
curl -s -X POST http://127.0.0.1:8080/api/commands \
  -H 'Content-Type: application/json' \
  -d '{"argv":["snapshot","create","Kali","clean-state"]}'
```

The API accepts argument arrays and uses the original CLI parser; commands are never passed through a shell. Server config/key paths cannot be overridden by requests. Use `/api/uploads` for file transfers and `ssh-info` for guest connection details.

### Deploy Kali

```bash
curl -s -X POST http://127.0.0.1:8080/api/vms \
  -H 'Content-Type: application/json' \
  -d '{"kind":"kali","name":"Kali-Lab","memory":8192,"cpus":4,"access":"tunnel"}'
```

### Create a guest from an ISO

```bash
curl -s -X POST http://127.0.0.1:8080/api/vms \
  -H 'Content-Type: application/json' \
  -d '{"kind":"iso","name":"Debian-Lab","iso":"C:\\Images\\installer.iso","os_type":"Debian_64","memory":4096,"cpus":2,"disk":60,"user":"guest-user","start":true}'
```

For an appliance, use `kind: "import"` and `source` with a Windows path or HTTPS URL. Supported formats are `.ova`, `.ovf`, `.vbox`, and `.7z` containing a VirtualBox VM. Optional `sha256` checks the image. Memory is in **MiB**, disk size in **GiB**. New VMs default to loopback SSH forwards reached through the Windows jump host.

### Upload a local image

```bash
curl -s -X POST http://127.0.0.1:8080/api/uploads \
  -F 'file=@/path/to/installer.iso'
```

The file is received into the Docker data volume, then transferred to Windows by a job. The completed result supplies its Windows path. The temporary container-side upload is removed when the job finishes. Default maximum size is 16 GiB, configurable with `VM_MANAGER_MAX_UPLOAD_BYTES` in Compose.

### Configure resources and sign-in startup

```bash
curl -s -X POST http://127.0.0.1:8080/api/vms/Kali/actions/configure \
  -H 'Content-Type: application/json' -d '{"memory":8192,"cpus":4}'

curl -s -X POST http://127.0.0.1:8080/api/vms/Kali/autostart \
  -H 'Content-Type: application/json' -d '{"mode":"enable"}'
```

Resource changes and snapshot restoration require a powered-off VM. Autostart is triggered by the Windows user's sign-in, not unattended system boot.

### Endpoint overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Container health; no remote SSH call |
| GET / POST | `/api/setup` | Setup status / initial host and SSH identity |
| GET | `/api/config` | Configured target and defaults |
| GET | `/api/host` | Windows/VirtualBox diagnostics |
| GET | `/api/os-types` | VirtualBox OS identifiers |
| GET / POST | `/api/vms` | Inventory / deployment |
| GET | `/api/vms/{name}` | VM information |
| GET | `/api/vms/{name}/ssh` | SSH command, optional `?user=USER` |
| GET / PUT | `/api/vms/{name}/credentials` | Read saved credentials / record a known password |
| POST | `/api/vms/{name}/actions/{action}` | Lifecycle / resources / forwarding / removal |
| GET / POST | `/api/vms/{name}/snapshots` | List / create / restore / delete snapshots |
| POST | `/api/vms/{name}/autostart` | Enable / disable / query sign-in startup |
| POST | `/api/commands` | Execute a non-interactive original CLI command |
| POST | `/api/uploads` | Receive and transfer a local file |
| GET | `/api/jobs` | Recent 50 jobs |
| GET | `/api/jobs/{id}` | Full operation status/result |

## Configuration, credentials and persistence

The project ships only `config.example.toml`, with empty personal fields. The actual `/data/config.toml` is created by setup. Configuration and SSH key files have owner-only file permissions; the app never returns the private key through its API. Setup is first-run only and cannot overwrite an already configured installation.

For advanced changes, edit the runtime config in the persistent volume and restart the service. Environment variables `VM_MANAGER_CONFIG`, `VM_MANAGER_DATA` and `VM_MANAGER_PUBLIC_KEY` support custom runtime paths. The container runs as a non-root user, with generic UID/GID defaults of 1000. Set `LOCAL_UID` and `LOCAL_GID` in an ignored `.env` file before building when different values are needed.

The named volume `vm-manager-docker_app-data` persists host settings, SSH identity/known hosts, job history, VM credential records and temporary uploads. Generated guest passwords remain in their deployment job results in this local volume. `docker compose down` keeps the volume; `docker compose down -v` deletes it.

`.gitignore` excludes local configs, `.env` files, `.local/`, databases, key files, logs, screenshots, caches and development environments. The Docker build uses a source-only allowlist and excludes Python bytecode. Keep installation-specific notes or exports under ignored `.local/`; share source files rather than runtime-volume contents. Loopback addresses and standard product/guest defaults in the code are generic application settings.

Jobs run sequentially in one worker. Queued jobs resume after a restart. A job that was running is marked `interrupted` rather than replayed automatically, since its remote action may already have happened. Inspect the VM before retrying. Run one service replica/one Uvicorn worker with this queue implementation.

## Project layout and backend updates

```text
compose.yaml               Container, local port and runtime mounts
Dockerfile                 Python/API/SSH-client image
config.example.toml        Empty-personal-fields reference template
requirements.lock          Pinned runtime dependencies
vendor/*.whl               Bundled existing VM Manager backend
vm_manager_docker/app.py    REST API and static dashboard serving
vm_manager_docker/jobs.py   Persistent SQLite job queue
vm_manager_docker/settings.py First-run validation and private runtime settings
vm_manager_docker/terminals.py WebSocket/PTY SSH terminal sessions
frontend/terminal.js        Browser terminal source and CRT themes
vm_manager_docker/client.py HTTP CLI client
vm_manager_docker/static/   HTML/CSS/JavaScript dashboard
tests/                     API/queue tests and browser smoke script
```

To update the bundled CLI, follow `vendor/README.md`. Its generic source is inside the wheel; a rebuild does not need a separate backend directory.

## Development checks

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]' ./vendor/*.whl
.venv/bin/python -m unittest discover -s tests -v
node --check vm_manager_docker/static/app.js
```

To rebuild the locally bundled terminal assets (Node.js required only for development):

```bash
npm ci
npm run build:terminal
```

Read-only browser verification against the running container:

```bash
.venv/bin/playwright install chromium
.venv/bin/python tests/browser_smoke.py
.venv/bin/python tests/browser_credentials.py
.venv/bin/python tests/browser_setup.py
.venv/bin/python tests/browser_terminals.py
```

When the Windows host is offline, `tests/browser_smoke.py --fixture` exercises the browser against explicit browser-only inventory fixtures. The application itself continues to report the real host connection error.

`browser_credentials.py` always uses browser-only credential fixtures. It tests a dialog left open during job completion, password display/copy, and recording a known password without altering real VMs or their passwords.

Screenshots are written to `test-results/`. Live verification results are recorded in `VERIFICATION.md`.

`browser_terminals.py` uses browser-only fixtures by default. With a configured Windows host and a running Linux SSH guest, `browser_terminals.py --live` verifies real PowerShell, CMD and guest terminal input/output using harmless marker commands; it does not save terminal transcripts or screenshots.
