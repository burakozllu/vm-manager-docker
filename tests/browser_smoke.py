"""Read-only browser smoke test against an already running Docker service."""
import os
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true", help="Use browser-only inventory fixtures when Windows is offline")
    args = parser.parse_args()
    base = os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:8080")
    output = Path(__file__).resolve().parents[1] / "test-results"
    output.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        if args.fixture:
            vm = {"name": "Kali", "state": "running", "uuid": "browser-fixture", "memory": 8192, "cpus": 4,
                  "os_type": "Debian (64-bit)", "ssh_user": "kali", "forwards": [
                      {"name": "kali-ssh", "protocol": "tcp", "guest_port": 22, "host_ip": "hypervisor.example.invalid", "host_port": 2222}]}
            def respond(data):
                return lambda route: route.fulfill(content_type="application/json", body=json.dumps({"data": data, "stale": False}))
            page.route("**/api/config", lambda route: route.fulfill(content_type="application/json", body=json.dumps({"configured": True, "host": {"hostname": "hypervisor.example.invalid", "username": "test-operator"}, "defaults": {}})))
            page.route("**/api/vms", respond([vm]))
            page.route("**/api/host", respond({"memory_free_gib": 17.8, "os": "Microsoft Windows 11 Pro"}))
            page.route("**/api/vms/Kali/ssh", respond({"command": "ssh -p 2222 kali@hypervisor.example.invalid"}))
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="domcontentloaded")
        expect(page.locator("#vm-table .machine-name").filter(has_text="Kali")).to_be_visible(timeout=60000)
        expect(page.locator("#stat-free")).not_to_have_text("—", timeout=60000)
        expect(page.locator("#alert")).to_be_hidden()
        page.screenshot(path=str(output / "dashboard.png"), full_page=True)
        kali_row = page.locator("#vm-table tr").filter(has=page.locator(".machine-name", has_text="Kali")).first
        kali_row.get_by_role("button", name="Manage", exact=True).click()
        expect(page.locator("#vm-dialog")).to_be_visible()
        expect(page.locator("#vm-dialog .ssh-box code")).to_contain_text("ssh ", timeout=30000)
        expect(page.locator("#vm-dialog").get_by_role("heading", name="Snapshots", exact=True)).to_be_visible()
        page.locator("#vm-dialog .close-dialog").click()
        page.locator('nav a[href="#deploy"]').click()
        page.get_by_label("Deployment type").select_option("iso")
        expect(page.locator("#deploy-iso")).to_be_visible()
        page.locator('#deploy-form input[name="name"]').fill("Browser-Form-Only")
        page.locator("#deploy-iso").fill("https://example.com/test.iso")
        assert page.locator("#deploy-form").evaluate("form => form.checkValidity()")
        page.get_by_label("Deployment type").select_option("import")
        expect(page.locator("#deploy-source")).to_be_visible()
        expect(page.locator("#deploy-iso")).to_be_hidden()
        page.locator('nav a[href="#jobs"]').click()
        expect(page.locator("#page-jobs")).to_be_visible()
        page.locator('nav a[href="#api"]').click()
        expect(page.locator("#api-reference")).to_contain_text("/api/commands")
        page.locator('nav a[href="#overview"]').click()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#stat-total")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile page overflows horizontally"
        page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
        browser.close()
    assert not errors, errors
    mode = "browser fixtures" if args.fixture else "live Windows host"
    print(f"Browser smoke passed ({mode}): inventory, host stats, machine controls, deployment forms, jobs, API reference and mobile layout.")


if __name__ == "__main__":
    main()
