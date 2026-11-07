# Verification

This document contains generic verification results only. Installation-specific values and historical notes belong in ignored runtime/private storage.

## Automated coverage

The application suite covers API jobs, credential persistence, first-run setup, private-file permissions, config reuse, input validation, ignored-file rules, source/backend-artifact privacy scans, terminal command construction, PTY resize/input/output, same-origin WebSocket access and process cleanup after disconnect.

Result: **42 automated tests passed**. The backend transport's subprocess/PTY tests use real local child processes, not simulated terminal output.

Browser scripts cover:

- `browser_smoke.py --fixture`: inventory, management forms, navigation and responsive layout.
- `browser_setup.py`: setup shown on first load, settings submission, sensitive-form cleanup and reuse on refresh.
- `browser_credentials.py`: deployment dialog updates, labeled credentials, clipboard and recording known passwords.
- `browser_terminals.py`: retro styles, tabs, host/VM controls, input/output, resize, reconnect and close using browser fixtures.
- `browser_terminals.py --live`: marker commands on a configured Windows PowerShell/CMD host and running Linux SSH guest; no transcripts or screenshots are saved.

## Privacy boundaries

- No target address, personal account, SSH identity, real VM password or installation history is embedded in shared source or the bundled backend.
- Only an empty-personal-fields `config.example.toml` is distributed.
- Actual configuration, SSH material, job history and VM credentials persist in the Docker data volume.
- Local config files, `.local/`, databases, key files, logs, screenshots, bytecode, environments and Node dependencies are gitignored.
- The image build uses a source-only allowlist, excludes bytecode and contains no runtime data mounts.

## Runtime migration

An existing installation's host configuration and SSH identity were migrated to persistent runtime storage before switching to the first-run configuration model. Existing job and credential data remain in that storage. Fresh installations start without a configured target.

## Docker and browser results

- The rebuilt service is healthy with migrated runtime settings.
- A separate disposable container started with no host settings or identity, displayed setup, accepted an ephemeral test identity through the actual form, and reused the saved settings after refresh. The disposable container was removed afterward.
- Setup, credential-display, inventory/mobile, and retro-terminal fixture browser checks passed.
- Live browser terminal checks passed for Windows PowerShell, Windows CMD, and a running Linux VM over SSH, including input/output markers, tabs, themes, CRT effects, resize, reconnect, and closing sessions.
- No live terminal transcripts or screenshots were retained. Browser terminal screenshots use only synthetic fixtures and are gitignored.
