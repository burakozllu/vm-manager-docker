import json
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from vm_manager_docker.app import create_app, DeploymentRequest
from vm_manager_docker.backend import Backend, CommandError
from vm_manager_docker.jobs import JobStore


ROOT = Path(__file__).resolve().parents[1]


class FakeBackend:
    def __init__(self, directory):
        self.real = Backend(ROOT / "tests/fixtures/config.toml", Path("/public/key.pub"), directory / "uploads")
        self.config = self.real.config
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
        self.active = 0
        self.max_active = 0

    def validate(self, argv):
        self.real.validate(argv)

    def run(self, argv):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(argv)
            if argv[0] == "start":
                self.gate.wait(timeout=5)
                return {"name": argv[1], "state": "running"}
            if argv[0] == "stop":
                raise RuntimeError("Guest did not shut down")
            if argv[0] == "list":
                return [{"name": "Kali", "state": "running", "uuid": "123", "memory": 8192, "cpus": 4}]
            if argv[0] == "upload":
                return {"path": "F:\\VMs\\Downloads\\test.iso", "sha256": "a" * 64}
            if argv[0] == "deploy-kali":
                return {"name": argv[1], "uuid": "test-vm-uuid", "ssh_user": "kali", "initial_password": "test-generated-password"}
            return {"argv": argv}
        finally:
            self.active -= 1

    def cleanup(self, argv):
        self.real.cleanup(argv)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.backend = FakeBackend(self.directory)
        self.app = create_app(data_dir=self.directory, backend=self.backend)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.backend.gate.set()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.client.get("/api/jobs/" + job_id).json()
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.02)
        self.fail("Job never completed")

    def test_health_and_dashboard_and_openapi_are_available(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        self.assertIn("VM Manager", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertIn("/api/commands", self.client.get("/openapi.json").json()["paths"])

    def test_real_config_is_sanitized_for_dashboard(self):
        response = self.client.get("/api/config").json()
        self.assertEqual(response["host"]["hostname"], "hypervisor.example.invalid")
        self.assertNotIn("vboxmanage", response["host"])

    def test_job_returns_immediately_and_stores_result(self):
        self.backend.gate.clear()
        start = time.monotonic()
        response = self.client.post("/api/vms/Kali/actions/start", json={})
        self.assertEqual(response.status_code, 202)
        self.assertLess(time.monotonic() - start, 1)
        job_id = response.json()["id"]
        self.backend.gate.set()
        job = self.wait(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"], {"name": "Kali", "state": "running"})
        self.assertIsNotNone(job["finished_at"])

    def test_backend_failure_is_visible_in_job(self):
        response = self.client.post("/api/commands", json={"argv": ["stop", "Kali"]})
        job = self.wait(response.json()["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "Guest did not shut down")

    def test_command_dispatch_has_no_shell_or_arbitrary_config_paths(self):
        for argv in (["sh", "-c", "whoami"], ["ssh", "Kali"], ["list", "--config=/etc/passwd"],
                     ["deploy-kali", "Lab", "--public-key", "/etc/passwd"], ["upload", "/etc/passwd"]):
            with self.subTest(argv=argv):
                response = self.client.post("/api/commands", json={"argv": argv})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/jobs").json(), [])

    def test_mutation_validation_precedes_submission(self):
        for endpoint, body in (("/api/vms", {"kind": "iso", "name": "Lab"}),
                               ("/api/vms", {"kind": "kali", "name": "../escape"}),
                               ("/api/vms/Kali/actions/configure", {}),
                               ("/api/vms/Kali/actions/configure", {"cpus": 0})):
            with self.subTest(endpoint=endpoint, body=body):
                self.assertEqual(self.client.post(endpoint, json=body).status_code, 422)
        self.assertEqual(self.client.get("/api/jobs").json(), [])

    def test_worker_serializes_jobs(self):
        self.backend.gate.clear()
        first = self.client.post("/api/commands", json={"argv": ["start", "One"]}).json()
        second = self.client.post("/api/commands", json={"argv": ["start", "Two"]}).json()
        self.backend.gate.set()
        self.wait(first["id"])
        self.wait(second["id"])
        self.assertEqual(self.backend.max_active, 1)
        self.assertEqual(self.backend.calls, [["start", "One"], ["start", "Two"]])

    def test_cached_inventory_is_available_while_operation_is_running(self):
        self.client.get("/api/vms")
        self.backend.gate.clear()
        job = self.client.post("/api/commands", json={"argv": ["start", "Kali"]}).json()
        deadline = time.monotonic() + 2
        while not self.app.state.store.busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        response = self.client.get("/api/vms")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["name"], "Kali")
        self.backend.gate.set()
        self.wait(job["id"])

    def test_upload_is_queued_with_safe_filename_and_cleaned_up(self):
        response = self.client.post("/api/uploads", files={"file": ("../../test.iso", b"ISO content", "application/octet-stream")})
        self.assertEqual(response.status_code, 202)
        job = self.wait(response.json()["id"])
        self.assertEqual(job["status"], "succeeded")
        local_path = Path(job["argv"][1])
        self.assertEqual(local_path.parent, self.directory / "uploads")
        self.assertTrue(local_path.name.endswith("test.iso"))
        deadline = time.monotonic() + 1
        while local_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(local_path.exists())

    def test_oversized_upload_does_not_leave_partial_files(self):
        with patch.dict("os.environ", {"VM_MANAGER_MAX_UPLOAD_BYTES": "3"}):
            response = self.client.post("/api/uploads", files={"file": ("test.iso", b"1234")})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(list((self.directory / "uploads").iterdir()), [])

    def test_unknown_job_is_not_found(self):
        self.assertEqual(self.client.get("/api/jobs/not-real").status_code, 404)

    def test_browser_cross_origin_mutations_are_rejected(self):
        response = self.client.post("/api/commands", json={"argv": ["start", "Kali"]}, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/jobs").json(), [])

    def test_deployment_records_password_in_job_and_vm_credentials(self):
        response = self.client.post("/api/commands", json={"argv": ["deploy-kali", "Lab"]})
        job = self.wait(response.json()["id"])
        self.assertEqual(job["credentials"]["username"], "kali")
        self.assertEqual(job["credentials"]["password"], "test-generated-password")
        saved = self.client.get("/api/vms/Lab/credentials?vm_uuid=test-vm-uuid")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.headers["cache-control"], "no-store")
        self.assertEqual(saved.json()["password"], job["result"]["initial_password"])
        self.assertEqual(saved.json()["job_id"], job["id"])

    def test_known_credentials_can_be_recorded_without_touching_guest(self):
        response = self.client.put("/api/vms/Imported/credentials", json={"vm_uuid": "123", "username": "admin", "password": "known-test-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "recorded")
        self.assertEqual(self.client.get("/api/vms/Imported/credentials").json()["username"], "admin")
        self.assertEqual(self.backend.calls, [])

    def test_unknown_or_replaced_vm_has_no_invented_password(self):
        self.assertEqual(self.client.get("/api/vms/Unknown/credentials").status_code, 404)
        self.client.put("/api/vms/Lab/credentials", json={"vm_uuid": "old-uuid", "username": "kali", "password": "test-password"})
        self.assertEqual(self.client.get("/api/vms/Lab/credentials?vm_uuid=new-uuid").status_code, 404)
        self.assertEqual(self.client.put("/api/vms/Lab/credentials", json={"vm_uuid": "123", "username": "kali", "password": ""}).status_code, 422)


class PersistenceTests(unittest.TestCase):
    def test_legacy_job_passwords_are_backfilled_beyond_recent_history_without_overwriting_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, argv TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, result TEXT, error TEXT)""")
                result = {"name": "Older-Lab", "uuid": "old-uuid", "ssh_user": "kali", "initial_password": "legacy-password"}
                db.execute("INSERT INTO jobs VALUES (?,?, 'succeeded', ?, NULL, ?, ?, NULL)",
                           ("legacy-job", json.dumps(["deploy-kali", "Older-Lab"]), "2026-01-01", "2026-01-01", json.dumps(result)))
                for number in range(55):
                    db.execute("INSERT INTO jobs VALUES (?,?, 'succeeded', ?, NULL, ?, ?, NULL)",
                               (f"other-{number}", json.dumps(["list"]), f"2026-02-{number:02}", "2026-02-01", "[]"))
            store = JobStore(path)
            self.assertNotIn("legacy-job", [job["id"] for job in store.list()])
            self.assertEqual(store.credentials("older-lab")["password"], "legacy-password")
            self.assertEqual(store.get("legacy-job")["credentials"]["password"], "legacy-password")
            store.record_credentials("Older-Lab", "old-uuid", "kali", "updated-password")
            restarted = JobStore(path)
            self.assertEqual(restarted.credentials("Older-Lab")["password"], "updated-password")

    def test_failed_retries_do_not_overwrite_password_and_removed_vms_do_not_reappear_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            store = JobStore(path)
            created = store.submit(["deploy-kali", "Lab"])
            store.finish(created["id"], result={"name": "Lab", "uuid": "123", "initial_password": "actual-password"})
            retry = store.submit(["deploy-kali", "Lab"])
            store.finish(retry["id"], error="VM already exists")
            self.assertIsNone(store.get(retry["id"])["credentials"])
            self.assertEqual(store.credentials("Lab")["password"], "actual-password")
            removed = store.submit(["remove", "Lab"])
            store.finish(removed["id"], result={"name": "Lab", "removed": True})
            self.assertIsNone(JobStore(path).credentials("Lab"))
            self.assertEqual(store.get(created["id"])["credentials"]["password"], "actual-password")

    def test_reusing_name_for_iso_guest_clears_previous_vm_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3")
            store.record_credentials("Lab", "old-uuid", "kali", "old-password")
            created = store.submit(["create", "Lab", "--iso", "F:\\test.iso"])
            store.finish(created["id"], result={"name": "Lab", "uuid": "new-uuid"})
            self.assertIsNone(store.credentials("Lab"))

    def test_running_work_is_marked_interrupted_without_replay_and_queued_work_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            store = JobStore(path)
            first = store.submit(["start", "Kali"])
            second = store.submit(["start", "Lab"])
            self.assertEqual(store.claim()["id"], first["id"])
            restarted = JobStore(path)
            restarted.recover()
            self.assertEqual(restarted.get(first["id"])["status"], "interrupted")
            self.assertIn("may still be running", restarted.get(first["id"])["error"])
            self.assertEqual(restarted.claim()["id"], second["id"])

    def test_deployment_model_translates_resource_units_and_start_flag(self):
        request = DeploymentRequest(kind="iso", name="Debian-Lab", iso="F:\\installer.iso", memory=4096, cpus=2, disk=60, start=False)
        argv = request.argv()
        self.assertEqual(argv[:2], ["create", "Debian-Lab"])
        self.assertEqual(argv[argv.index("--disk") + 1], "60")
        self.assertIn("--no-start", argv)


if __name__ == "__main__":
    unittest.main()
