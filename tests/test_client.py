import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vm_manager_docker.client import main, upload


class ClientTests(unittest.TestCase):
    @patch("vm_manager_docker.client.request")
    def test_credentials_command_uses_explicit_endpoint_and_encodes_vm_name(self, request):
        request.return_value = {"username": "kali", "password": "test-password"}
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["credentials", "Lab With Spaces"]), 0)
        request.assert_called_once_with("http://127.0.0.1:8080", "/api/vms/Lab%20With%20Spaces/credentials")
        self.assertIn("test-password", output.getvalue())

    @patch("vm_manager_docker.client.urlopen")
    def test_streamed_upload_has_correct_length_and_preserves_file_bytes(self, urlopen):
        received = {}

        def receive(request, **kwargs):
            payload = b"".join(request.data)
            received["body"] = payload
            received["length"] = int(request.headers["Content-length"])
            return io.BytesIO(b'{"id":"upload-job","status":"queued"}')

        urlopen.side_effect = receive
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installer.iso"
            content = bytes(range(256)) * 5000
            path.write_bytes(content)
            result = upload("http://127.0.0.1:8080", path)
        self.assertEqual(result["id"], "upload-job")
        self.assertEqual(received["length"], len(received["body"]))
        self.assertIn(content, received["body"])
        self.assertIn(b'filename="installer.iso"', received["body"])

    @patch("vm_manager_docker.client.request")
    def test_no_wait_returns_job_without_polling(self, request):
        request.return_value = {"id": "123", "status": "queued"}
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["--no-wait", "start", "Kali"]), 0)
        self.assertIn('"id": "123"', output.getvalue())
        request.assert_called_once_with("http://127.0.0.1:8080", "/api/commands", {"argv": ["start", "Kali"]})

    @patch("vm_manager_docker.client.time.sleep")
    @patch("vm_manager_docker.client.request")
    def test_failed_job_sets_nonzero_cli_exit_status(self, request, sleep):
        request.side_effect = [{"id": "123", "status": "queued"},
                               {"id": "123", "status": "failed", "error": "Host unavailable"}]
        with patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(main(["start", "Kali"]), 1)
        self.assertIn("Host unavailable", error.getvalue())


if __name__ == "__main__":
    unittest.main()
