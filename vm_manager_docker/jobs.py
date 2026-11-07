from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


class QueueFull(Exception):
    pass


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, argv TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                result TEXT, error TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS vm_credentials (
                vm_name TEXT PRIMARY KEY COLLATE NOCASE, vm_uuid TEXT NOT NULL,
                username TEXT NOT NULL, password TEXT NOT NULL, source TEXT NOT NULL,
                job_id TEXT, updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
            if not db.execute("SELECT 1 FROM migrations WHERE name='credentials-v1'").fetchone():
                # Replay the complete history once, not merely the recent 50 jobs.
                for row in db.execute("SELECT * FROM jobs WHERE status='succeeded' ORDER BY created_at").fetchall():
                    job = self.decode(row)
                    self.sync_credentials(db, job, job["result"], job["finished_at"] or job["created_at"])
                db.execute("INSERT INTO migrations(name) VALUES ('credentials-v1')")
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        job = dict(row)
        job["argv"] = json.loads(job["argv"])
        job["result"] = json.loads(job["result"]) if job["result"] is not None else None
        job["credentials"] = JobStore.generated_credentials(job)
        return job

    @staticmethod
    def generated_credentials(job):
        result = job.get("result")
        if (job.get("status") != "succeeded" or job["argv"][0] != "deploy-kali"
                or not isinstance(result, dict) or not isinstance(result.get("initial_password"), str)
                or not result["initial_password"]):
            return None
        return {
            "vm_name": result.get("name") or job["argv"][1], "vm_uuid": result.get("uuid"),
            "username": result.get("ssh_user") or "kali", "password": result["initial_password"],
            "source": "generated", "job_id": job["id"],
            "updated_at": job.get("finished_at") or job["created_at"],
        }

    @staticmethod
    def write_credentials(db, credentials):
        db.execute("""INSERT INTO vm_credentials(vm_name,vm_uuid,username,password,source,job_id,updated_at)
            VALUES (:vm_name,:vm_uuid,:username,:password,:source,:job_id,:updated_at)
            ON CONFLICT(vm_name) DO UPDATE SET vm_uuid=excluded.vm_uuid, username=excluded.username,
            password=excluded.password, source=excluded.source, job_id=excluded.job_id, updated_at=excluded.updated_at""", credentials)

    @staticmethod
    def sync_credentials(db, job, result, timestamp):
        credentials = JobStore.generated_credentials({**job, "status": "succeeded", "result": result, "finished_at": timestamp})
        if credentials and credentials["vm_uuid"]:
            JobStore.write_credentials(db, credentials)
        elif isinstance(result, dict) and result.get("name"):
            if job["argv"][0] == "remove" and result.get("removed"):
                db.execute("DELETE FROM vm_credentials WHERE vm_name=?", (result["name"],))
            elif job["argv"][0] in ("create", "import") and result.get("uuid"):
                db.execute("DELETE FROM vm_credentials WHERE vm_name=? AND vm_uuid<>?", (result["name"], result["uuid"]))

    def credentials(self, name, vm_uuid=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM vm_credentials WHERE vm_name=?", (name,)).fetchone()
            if row is None or (vm_uuid is not None and row["vm_uuid"] != vm_uuid):
                return None
            return dict(row)

    def record_credentials(self, name, vm_uuid, username, password):
        credentials = {"vm_name": name, "vm_uuid": vm_uuid, "username": username, "password": password,
                       "source": "recorded", "job_id": None, "updated_at": now()}
        with self.connect() as db:
            self.write_credentials(db, credentials)
        return credentials

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status='interrupted', finished_at=?, error=? WHERE status='running'",
                (now(), "Service restarted during this operation. Inspect VM state before retrying; the remote operation may still be running."),
            )

    def submit(self, argv):
        job_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= 200:
                raise QueueFull("The job queue is full")
            db.execute("INSERT INTO jobs(id, argv, status, created_at) VALUES (?,?,'queued',?)", (job_id, json.dumps(argv), now()))
        return self.get(job_id)

    def get(self, job_id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list(self, limit=50):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def busy(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone())

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now(), row["id"]))
            job_id = row["id"]
        return self.get(job_id)

    def finish(self, job_id, *, result=None, error=None):
        with self.connect() as db:
            finished = now()
            db.execute("UPDATE jobs SET status=?, finished_at=?, result=?, error=? WHERE id=?",
                       ("failed" if error is not None else "succeeded", finished, json.dumps(result), error, job_id))
            if error is None:
                job = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
                if job:
                    self.sync_credentials(db, job, result, finished)


class Worker:
    def __init__(self, store, backend, invalidate):
        self.store, self.backend, self.invalidate = store, backend, invalidate
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="vm-manager-jobs", daemon=True)

    def start(self):
        self.thread.start()

    def wake(self):
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=2)

    def loop(self):
        while not self.stop_event.is_set():
            job = self.store.claim()
            if job is None:
                self.wake_event.wait(timeout=1)
                self.wake_event.clear()
                continue
            try:
                result = self.backend.run(job["argv"])
                self.store.finish(job["id"], result=result)
            except Exception as exc:
                self.store.finish(job["id"], error=str(exc))
            finally:
                try:
                    self.backend.cleanup(job["argv"])
                except OSError:
                    pass
                self.invalidate()
