"""Configure a disposable, initially empty service with an ephemeral test key."""
import os
from pathlib import Path
import subprocess
import tempfile

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:18081")
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / "identity"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "fixture-only", "-f", str(key)], check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            assert page.request.get(base + "/api/setup").json() == {"configured": False}, "Use a disposable unconfigured service"
            assert page.request.get(base + "/api/vms").status == 409
            page.goto(base)
            expect(page.locator("#setup-screen")).to_be_visible()
            page.get_by_label("Windows host", exact=True).fill("hypervisor.example.invalid")
            page.get_by_label("Windows SSH account", exact=True).fill("test-operator")
            page.locator('#setup-form input[name="private_key"]').set_input_files(str(key))
            page.get_by_role("button", name="Save & open dashboard", exact=True).click()
            expect(page.locator("#setup-screen")).to_be_hidden(timeout=30000)
            assert page.request.get(base + "/api/setup").json() == {"configured": True}
            configured = page.request.get(base + "/api/config").json()
            assert configured["host"]["hostname"] == "hypervisor.example.invalid"
            assert "private_key" not in configured
            page.reload()
            expect(page.locator("#setup-screen")).to_be_hidden()
            expect(page.locator("#target-host")).to_contain_text("hypervisor.example.invalid")
            browser.close()
    print("Fresh-container integration passed: no settings required to start, actual key validation/save, configured dashboard and settings reuse.")


if __name__ == "__main__":
    main()
