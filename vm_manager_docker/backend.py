from __future__ import annotations

import argparse
from pathlib import Path

from vm_manager.cli import build_parser, execute
from vm_manager.transport import ManagerError, load_config


COMMANDS = (
    "doctor", "list", "os-types", "info", "start", "stop", "restart", "pause", "resume", "save",
    "create", "import", "deploy-kali", "configure", "eject-iso", "remove", "ssh-configure", "ssh-info",
    "snapshot", "autostart",
)


class CommandError(ValueError):
    pass


class Backend:
    def __init__(self, config_path: Path, public_key: Path, uploads: Path):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.public_key = public_key
        self.uploads = uploads.resolve()

    def parse(self, argv: list[str], *, allow_upload=False):
        if not argv or argv[0] not in (*COMMANDS, *(("upload",) if allow_upload else ())):
            raise CommandError("Unsupported command. Use ssh-info for connection details; file transfers use /api/uploads.")
        # Requests cannot override the server configuration or select server filesystem keys.
        forbidden = ("--config", "--identity", "--public-key", "--help", "-h", "--json")
        if any(arg == option or arg.startswith(option + "=") for arg in argv for option in forbidden):
            raise CommandError("Server configuration, keys and output format are managed by the service")
        parser = build_parser()

        def error(message):
            raise CommandError(message)

        # argparse's nested command parsers need the same non-exiting error handler.
        def configure(current):
            current.error = error
            for action in current._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        configure(child)
        configure(parser)
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            raise CommandError("Invalid command arguments") from exc
        args.config = self.config_path
        args.json = True
        if args.command == "deploy-kali":
            args.public_key = self.public_key
        if args.command == "upload":
            path = args.file.resolve()
            if path.parent != self.uploads or not path.is_file():
                raise CommandError("Upload must refer to a file received by this API")
        return args

    def validate(self, argv):
        self.parse(argv)

    def run(self, argv):
        try:
            return execute(self.parse(argv, allow_upload=True), self.config)
        except ManagerError as exc:
            raise RuntimeError(str(exc)) from exc

    def cleanup(self, argv):
        if len(argv) == 2 and argv[0] == "upload":
            path = Path(argv[1]).resolve()
            if path.parent == self.uploads:
                path.unlink(missing_ok=True)
