"""Ensure shared files and backend artifacts do not capture installation data."""
import getpass
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class PrivacyTests(unittest.TestCase):
    def test_gitignore_protects_runtime_material_but_keeps_synthetic_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            base = ["git", "--git-dir", str(Path(directory) / ".git"), "--work-tree", str(ROOT), "check-ignore", "--no-index"]
            ignored = ["config.toml", ".env", ".env.private", ".local/config.toml", ".local/notes.md", "identity.pem",
                       "id_ed25519", "id_ecdsa", ".ssh/identity", "data/config.toml", "secrets/token", "known_hosts", "jobs.sqlite3", "test-results/dashboard.png", "app.log"]
            result = subprocess.run(base + ignored, capture_output=True, text=True, check=True)
            self.assertEqual(set(result.stdout.splitlines()), set(ignored))
            examples = subprocess.run(base + ["config.example.toml", "tests/fixtures/config.toml", "README.md", "vendor/README.md", "vendor/virtualbox_vm_manager-0.1.0-py3-none-any.whl"], capture_output=True, text=True)
            self.assertEqual(examples.returncode, 1)
            self.assertEqual(examples.stdout, "")

    def test_shareable_text_and_backend_wheel_have_no_personal_values_or_private_keys(self):
        tokens = []
        account = getpass.getuser()
        if len(account) > 4 and account not in ("runner", "ubuntu", "python"):
            tokens.append(account.casefold())
        local = ROOT / ".local/config.toml"
        if local.exists():
            values = tomllib.loads(local.read_text())["host"]
            for key in ("hostname", "username"):
                token = values.get(key, "")
                if len(token) > 4 and token not in ("administrator", "guest-user", "test-operator"):
                    tokens.append(token.casefold())
        files = [path for path in ROOT.iterdir() if path.is_file() and path.name not in ("config.toml", ".env")]
        for folder in ("vm_manager_docker", "tests", "docker", "vendor", "frontend"):
            files += [path for path in (ROOT / folder).rglob("*") if path.is_file() and "__pycache__" not in path.parts]
        private_address = re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
        private_key = re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|ENCRYPTED)? ?PRIVATE KEY-----\s+[A-Za-z0-9+/]{40}")
        for path in files:
            if path.suffix == ".whl":
                with zipfile.ZipFile(path) as archive:
                    members = [(name, archive.read(name).decode("utf-8")) for name in archive.namelist() if not name.endswith("/")]
            else:
                members = [(str(path.relative_to(ROOT)), path.read_text())]
            for name, text in members:
                with self.subTest(file=name):
                    self.assertIsNone(private_address.search(text), "Private network address found in shared file")
                    self.assertIsNone(private_key.search(text), "Private SSH key found in shared file")
                    for token in tokens:
                        self.assertNotIn(token, text.casefold(), "Installation-specific value found in shared file")

    def test_reference_config_has_no_preconfigured_personal_fields(self):
        config = tomllib.loads((ROOT / "config.example.toml").read_text())
        self.assertEqual(config["host"]["hostname"], "")
        self.assertEqual(config["host"]["username"], "")
        self.assertIn("**/*.pyc", (ROOT / ".dockerignore").read_text())


if __name__ == "__main__":
    unittest.main()
