"""Retro terminal UI fixtures, or read-only marker commands on a configured host."""
import argparse
import json
import os
from pathlib import Path
import re
import time

from playwright.sync_api import expect, sync_playwright


def plain(text):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use the configured Windows host and a running Linux SSH guest")
    args = parser.parse_args()
    base = os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:8080")
    errors, sockets = [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        if not args.live:
            vm = {"name": "Example-VM", "uuid": "fixture-uuid", "state": "running", "memory": 4096, "cpus": 2,
                  "os_type": "Debian (64-bit)", "ssh_user": "guest-user", "forwards": []}

            def api(route):
                path = route.request.url.split("/api/", 1)[1]
                data = {"config": {"configured": True, "host": {"hostname": "hypervisor.example.invalid", "username": "test-operator"}},
                        "jobs": [], "vms": {"data": [vm], "stale": False}, "host": {"data": {"memory_free_gib": 8, "os": "Fixture"}, "stale": False}}
                route.fulfill(content_type="application/json", body=json.dumps(data.get(path, {})))

            def terminal(socket):
                socket.send(json.dumps({"type": "status", "state": "connected"}))
                socket.send(b"RETRO TERMINAL READY\r\n$ ")

                def incoming(message):
                    data = json.loads(message)
                    if data.get("type") == "input":
                        socket.send(data["data"].encode())
                        if "\r" in data["data"] or "\n" in data["data"]:
                            socket.send(b"\r\nFIXTURE_REPLY\r\n$ ")
                socket.on_message(incoming)

            page.route("**/api/**", api)
            page.route_web_socket(re.compile(r".*/api/terminals/ws.*"), terminal)

        def capture(socket):
            record = {"url": socket.url, "output": ""}
            sockets.append(record)
            socket.on("framereceived", lambda frame: record.update(output=record["output"] + (frame.decode("utf-8", errors="replace") if isinstance(frame, bytes) else "")))
        page.on("websocket", capture)
        page.goto(base + "/#terminals")
        expect(page.locator("#page-terminals")).to_be_visible()
        page.locator("#terminal-host-powershell").click()
        expect(page.locator("#terminal-status")).to_have_text("Connected", timeout=30000)

        def wait_output(marker, timeout=45):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if sockets and marker in plain(sockets[-1]["output"]):
                    return
                page.wait_for_timeout(100)
            raise AssertionError(f"Expected terminal marker not received: {marker}")

        def type_command(command):
            page.locator('.terminal-view:not([hidden]) .xterm-helper-textarea').focus()
            page.keyboard.type(command, delay=3)
            page.keyboard.press("Enter")

        if args.live:
            wait_output("PS ")
            type_command("Write-Output ('BROWSER_HOST_' + 'READY')")
            wait_output("BROWSER_HOST_READY")
        else:
            type_command("echo fixture")
            expect(page.locator(".terminal-view:not([hidden]) .xterm-rows")).to_contain_text("FIXTURE_REPLY")

        page.locator("#terminal-theme").select_option("green")
        expect(page.locator("#terminal-stage")).to_have_class(re.compile("terminal-crt"))
        page.locator("#terminal-effects").uncheck()
        expect(page.locator("#terminal-stage")).not_to_have_class(re.compile("terminal-crt"))
        page.locator("#terminal-effects").check()
        page.locator("#terminal-host-cmd").click()
        expect(page.locator("#terminal-tabs").get_by_text("Windows CMD", exact=True)).to_be_visible()
        expect(page.locator("#terminal-status")).to_have_text("Connected", timeout=30000)
        if args.live:
            wait_output(">")
            type_command("set CRT_TERM_SUFFIX=READY")
            type_command("echo BROWSER_CMD_%CRT_TERM_SUFFIX%")
            wait_output("BROWSER_CMD_READY")

        if args.live:
            response = page.request.get(base + "/api/vms", timeout=60000).json()
            choices = [vm for vm in response["data"] if vm["state"] == "running" and "Windows" not in vm["os_type"]]
            assert choices, "A running Linux SSH VM is required for the live guest test"
            guest_name = choices[0]["name"]
        else:
            guest_name = "Example-VM"
        page.locator("#terminal-vm").select_option(guest_name)
        page.locator("#terminal-open-vm").click()
        expect(page.locator("#terminal-status")).to_have_text("Connected", timeout=60000)
        if args.live:
            wait_output("$ ")
            type_command("printf 'BROWSER_VM_%s\\n' READY")
            wait_output("BROWSER_VM_READY")
        else:
            type_command("echo fixture")
            expect(page.locator(".terminal-view:not([hidden]) .xterm-rows")).to_contain_text("FIXTURE_REPLY")
            output = Path(__file__).resolve().parents[1] / "test-results"
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / "retro-terminal-fixture.png"), full_page=True)
        page.set_viewport_size({"width": 1100, "height": 800})
        page.locator("#terminal-tabs").get_by_text("Windows PowerShell", exact=True).click()
        page.locator("#terminal-reconnect").click()
        expect(page.locator("#terminal-status")).to_have_text("Connected", timeout=30000)
        page.evaluate("window.vmTerminals.closeAll()")
        expect(page.locator("#terminal-empty")).to_be_visible()
        browser.close()
    assert not errors, errors
    print("Retro terminal checks passed: " + ("live Windows PowerShell, CMD and Linux VM SSH" if args.live else "browser-only fixtures") + "; tabs, themes, effects, input/output, resize, reconnect and close.")


if __name__ == "__main__":
    main()
