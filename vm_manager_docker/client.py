"""Standard-library HTTP client; usable outside Docker without dependencies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote


class ClientError(Exception):
    pass


def configure(base):
    status = request(base, "/api/setup")
    if status["configured"]:
        return status
    hostname = input("Windows host name or address: ").strip()
    username = input("Windows SSH username: ").strip()
    try:
        port = int(input("SSH port [22]: ").strip() or "22")
    except ValueError as exc:
        raise ClientError("SSH port must be a number") from exc
    vm_root = input("Windows VM folder [C:\\VMs]: ").strip() or "C:\\VMs"
    key_path = Path(input("SSH private key file: ").strip()).expanduser()
    try:
        private = key_path.read_text()
    except OSError as exc:
        raise ClientError("Cannot read the selected SSH private key file") from exc
    return request(base, "/api/setup", {"hostname": hostname, "username": username, "port": port,
                                         "vm_root": vm_root, "private_key": private})


def request(base, path, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    try:
        with urlopen(Request(base.rstrip("/") + path, data=data, headers=headers), timeout=200) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", str(exc))
        except (ValueError, OSError):
            detail = str(exc)
        raise ClientError(f"HTTP {exc.code}: {detail}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise ClientError(str(exc)) from exc


def upload(base, path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ClientError(f"File does not exist: {path}")
    boundary = "vm-manager-" + uuid.uuid4().hex
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", path.name)
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
              'Content-Type: application/octet-stream\r\n\r\n').encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()

    def chunks():
        yield prefix
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        yield suffix

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
               "Content-Length": str(len(prefix) + path.stat().st_size + len(suffix))}
    try:
        with urlopen(Request(base.rstrip("/") + "/api/uploads", data=chunks(), headers=headers), timeout=7200) as response:
            return json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise ClientError(f"Upload failed: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Use VM Manager through its Docker REST API.",
                                     epilog="Examples: ./vmctl list | ./vmctl start Kali | ./vmctl --no-wait deploy-kali Kali-Lab")
    parser.add_argument("--url", default=os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--no-wait", action="store_true", help="return the submitted job immediately")
    parser.add_argument("--timeout", type=int, default=15000, help="job wait timeout in seconds")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="VM Manager command and arguments; jobs/job ID/health also supported")
    args = parser.parse_args(argv)
    command = args.argv
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.print_help()
        return 0
    try:
        if command[0] == "setup":
            result = configure(args.url)
        elif command[0] == "health":
            result = request(args.url, "/api/health")
        elif command[0] == "jobs":
            result = request(args.url, "/api/jobs")
        elif command[0] == "job" and len(command) == 2:
            result = request(args.url, "/api/jobs/" + command[1])
        elif command[0] == "credentials":
            if len(command) != 2:
                raise ClientError("Usage: ./vmctl credentials VM_NAME")
            result = request(args.url, "/api/vms/" + quote(command[1], safe="") + "/credentials")
        else:
            if command[0] == "upload":
                if len(command) != 2:
                    raise ClientError("Usage: ./vmctl upload /path/to/file.iso")
                result = upload(args.url, command[1])
            else:
                result = request(args.url, "/api/commands", {"argv": command})
            if not args.no_wait:
                end = time.monotonic() + args.timeout
                job_id = result["id"]
                print(f"Job {job_id}", file=sys.stderr)
                while result["status"] in ("queued", "running"):
                    if time.monotonic() >= end:
                        raise ClientError(f"Wait timed out; job continues. Inspect it with: ./vmctl job {job_id}")
                    time.sleep(1)
                    result = request(args.url, "/api/jobs/" + job_id)
                if result["status"] != "succeeded":
                    raise ClientError(result.get("error") or f"Job {result['status']}")
                result = result["result"]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped waiting; submitted jobs continue on the server.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
