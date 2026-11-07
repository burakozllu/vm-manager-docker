import asyncio
import fcntl
from pathlib import Path
import struct
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from vm_manager.transport import load_config

from vm_manager_docker.app import create_app
from vm_manager_docker.terminals import PTYSession, build_command


ROOT = Path(__file__).resolve().parents[1]


def backend():
    fake = Mock()
    fake.config = load_config(ROOT / "tests/fixtures/config.toml")
    fake.run.return_value = {"name": "Example-VM", "uuid": "fixture-uuid", "state": "running", "ssh_user": "guest-user",
                             "forwards": [{"name": "vm-manager-ssh", "protocol": "tcp", "host_ip": "127.0.0.1", "host_port": 2223, "guest_port": 22}]}
    return fake


class CommandTests(unittest.TestCase):
    def test_host_uses_configured_target_pty_and_selected_windows_shell(self):
        command = build_command(backend(), "host")
        self.assertIn("test-operator@hypervisor.example.invalid", command)
        self.assertIn("-tt", command)
        self.assertIn("BatchMode=no", command)
        self.assertEqual(command[-1], "powershell.exe -NoLogo -NoProfile")
        self.assertEqual(build_command(backend(), "host", shell="cmd")[-1], "cmd.exe")

    def test_vm_uses_guest_username_and_jump_host(self):
        command = build_command(backend(), "vm", "Example-VM")
        self.assertIn("-J", command)
        self.assertEqual(command[-1], "guest-user@127.0.0.1")
        self.assertIn("2223", command)
        self.assertIn("HostKeyAlias=vm-manager-hypervisor.example.invalid-fixture-uuid", command)

    def test_stopped_vm_cannot_open_a_terminal(self):
        fake = backend()
        fake.run.return_value["state"] = "poweroff"
        with self.assertRaisesRegex(ValueError, "Start the VM"):
            build_command(fake, "vm", "Example-VM")

    def test_unknown_shell_or_target_is_rejected(self):
        for args in (("host", None, "arbitrary-shell"), ("invalid",), ("vm",)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_command(backend(), *args)


class PTYTests(unittest.IsolatedAsyncioTestCase):
    async def test_controlling_tty_resize_io_and_cleanup(self):
        command = [sys.executable, "-u", "-c", "import os; assert os.isatty(0); print('PTY_READY'); print('REPLY_'+input())"]
        session = PTYSession(command)
        try:
            session.resize(120, 40)
            size = fcntl.ioctl(session.master, termios.TIOCGWINSZ, b"\0" * 8)
            self.assertEqual(struct.unpack("HHHH", size)[:2], (40, 120))
            data = b""
            while b"PTY_READY" not in data:
                data += await asyncio.wait_for(session.read(), 5)
            await session.write(b"hello\n")
            while b"REPLY_hello" not in data:
                part = await asyncio.wait_for(session.read(), 5)
                self.assertTrue(part, data)
                data += part
        finally:
            await asyncio.to_thread(session.close)
        self.assertIsNone(session.master)
        self.assertIsNotNone(session.process.poll())


class WebSocketTests(unittest.TestCase):
    def test_websocket_streams_input_output_resize_and_closes_without_job_logging(self):
        command = [sys.executable, "-u", "-c", "import sys; print('FIXTURE_READY');\nfor line in sys.stdin: print('FIXTURE_REPLY:'+line.strip(), flush=True)"]
        with tempfile.TemporaryDirectory() as directory, patch("vm_manager_docker.terminals.build_command", return_value=command):
            app = create_app(data_dir=directory, backend=backend())
            with TestClient(app) as client:
                with client.websocket_connect("/api/terminals/ws?target=host", headers={"origin": "http://testserver"}) as socket:
                    self.assertEqual(socket.receive_json()["state"], "connecting")
                    self.assertEqual(socket.receive_json()["state"], "connected")
                    socket.send_json({"type": "resize", "cols": 110, "rows": 35})
                    socket.send_json({"type": "input", "data": "hello\n"})
                    output = b""
                    while b"FIXTURE_REPLY:hello" not in output:
                        output += socket.receive_bytes()
                    session = next(iter(app.state.terminal_sessions.values()))
                deadline = time.monotonic() + 3
                while (app.state.terminal_sessions or session.process.poll() is None) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(app.state.terminal_sessions, {})
                self.assertIsNotNone(session.process.poll())
                self.assertEqual(client.get("/api/jobs").json(), [])

    def test_cross_origin_terminal_is_rejected_before_spawning(self):
        with tempfile.TemporaryDirectory() as directory, patch("vm_manager_docker.terminals.PTYSession") as session:
            with TestClient(create_app(data_dir=directory, backend=backend())) as client:
                with self.assertRaises(WebSocketDisconnect) as error:
                    with client.websocket_connect("/api/terminals/ws", headers={"origin": "https://unrelated.example"}):
                        pass
                self.assertEqual(error.exception.code, 1008)
                session.assert_not_called()

    def test_first_run_installation_cannot_open_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(data_dir=directory)) as client:
                with self.assertRaises(WebSocketDisconnect) as error:
                    with client.websocket_connect("/api/terminals/ws"):
                        pass
                self.assertEqual(error.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()
