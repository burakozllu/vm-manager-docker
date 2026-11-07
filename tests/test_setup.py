import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from vm_manager_docker.app import create_app
from vm_manager_docker.client import main
from vm_manager_docker.settings import SetupError, SetupRequest, save_setup


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        path = Path(cls.keys.name) / "identity"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "fixture-only", "-f", str(path)], check=True)
        cls.private = path.read_text()
        cls.public = path.with_suffix(".pub").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def payload(self, **overrides):
        return {"hostname": "hypervisor.example.invalid", "username": "test-operator", "private_key": self.private, **overrides}

    def test_empty_installation_starts_and_requires_setup_before_vm_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(data_dir=directory, config_path=Path(directory) / "config.toml")
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(client.get("/api/setup").json(), {"configured": False})
                self.assertEqual(client.get("/api/config").json(), {"configured": False})
                self.assertEqual(client.get("/api/vms").status_code, 409)
                self.assertEqual(client.post("/api/commands", json={"argv": ["list"]}).status_code, 409)
                self.assertIn("First-run setup", client.get("/").text)

    def test_setup_persists_private_runtime_files_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = data / "config.toml"
            app = create_app(data_dir=data, config_path=config)
            with TestClient(app) as client:
                response = client.post("/api/setup", json=self.payload())
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"configured": True})
                self.assertEqual(client.get("/api/config").json()["host"]["hostname"], "hypervisor.example.invalid")
                self.assertNotIn(self.private, response.text)
                self.assertEqual(client.post("/api/setup", json=self.payload()).status_code, 409)
                client.put("/api/vms/Example/credentials", json={"vm_uuid": "fixture-uuid", "username": "guest-user", "password": "fixture-password"})
            self.assertEqual((data / "ssh/id_ed25519").read_text(), self.private)
            self.assertEqual((data / "ssh/id_ed25519.pub").read_text().split()[:2], self.public.split()[:2])
            for path in (config, data / "ssh/id_ed25519", data / "ssh/id_ed25519.pub"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((data / "ssh").stat().st_mode & 0o777, 0o700)
            with TestClient(create_app(data_dir=data, config_path=config)) as client:
                self.assertEqual(client.get("/api/setup").json(), {"configured": True})
                self.assertEqual(client.get("/api/vms/Example/credentials").json()["password"], "fixture-password")

    def test_invalid_configuration_or_key_never_commits_setup(self):
        for overrides in ({"hostname": "invalid;command"}, {"vm_root": "relative/path"}, {"private_key": "not-a-private-key"},
                          {"public_key": "ssh-ed25519 not-the-matching-key"}):
            with self.subTest(overrides=list(overrides)), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "config.toml"
                with TestClient(create_app(data_dir=directory, config_path=config)) as client:
                    response = client.post("/api/setup", json=self.payload(**overrides))
                    self.assertEqual(response.status_code, 422)
                    self.assertFalse(config.exists())
                    self.assertNotIn(self.private, response.text)
                    self.assertFalse((Path(directory) / "ssh/id_ed25519").exists())

    def test_validation_error_does_not_echo_private_key_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(data_dir=directory)) as client:
                response = client.post("/api/setup", json=self.payload(port=0))
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("input", response.json()["detail"][0])
                self.assertNotIn(self.private, response.text)

    def test_template_or_missing_identity_opens_setup(self):
        root = Path(__file__).resolve().parents[1]
        for template in (root / "config.example.toml", root / "tests/fixtures/config.toml"):
            with tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "config.toml"
                config.write_text(template.read_text())
                with TestClient(create_app(data_dir=directory, config_path=config)) as client:
                    self.assertFalse(client.get("/api/setup").json()["configured"])

    def test_matching_public_key_and_known_hosts_are_private_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            settings = SetupRequest(**self.payload(public_key=self.public, known_hosts="# synthetic fixture\n"))
            save_setup(settings, data / "config.toml", data)
            self.assertEqual((data / "ssh/id_ed25519.pub").read_text(), self.public)
            self.assertEqual((data / "ssh/known_hosts").read_text(), "# synthetic fixture\n")

    @patch("vm_manager_docker.client.request")
    def test_cli_setup_prompts_and_posts_settings_without_printing_the_key(self, request):
        request.side_effect = [{"configured": False}, {"configured": True}]
        key = Path(self.keys.name) / "identity"
        with patch("builtins.input", side_effect=["hypervisor.example.invalid", "test-operator", "", "", str(key)]), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["setup"]), 0)
        payload = request.call_args.args[2]
        self.assertEqual(payload["private_key"], self.private)
        self.assertNotIn(self.private, output.getvalue())


if __name__ == "__main__":
    unittest.main()
