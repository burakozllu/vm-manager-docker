"""Browser-only credential fixtures; no real VM commands or password changes."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:8080")
    errors = []
    completed = False
    credentials = {"vm_name": "Browser-Lab", "vm_uuid": "fixture-uuid", "username": "kali",
                   "password": "browser-only-test-password", "source": "generated", "job_id": "fixture-job", "updated_at": "2026-11-08T00:00:00Z"}
    vm = {"name": "Browser-Lab", "uuid": "fixture-uuid", "state": "running", "memory": 8192, "cpus": 4,
          "ssh_user": "kali", "os_type": "Debian (64-bit)", "forwards": []}

    def route_api(route):
        nonlocal credentials
        path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
        if path == "config":
            result = {"host": {"hostname": "fixture", "username": "user"}, "defaults": {}}
        elif path == "vms":
            result = {"data": [vm], "stale": False}
        elif path == "host":
            result = {"data": {"memory_free_gib": 16, "os": "Browser fixture"}, "stale": False}
        elif path == "jobs":
            result = [{"id": "fixture-job", "argv": ["deploy-kali", "Browser-Lab"], "status": "succeeded" if completed else "running",
                       "created_at": "2026-11-08T00:00:00Z", "started_at": "2026-11-08T00:00:00Z",
                       "finished_at": "2026-11-08T00:01:00Z" if completed else None,
                       "credentials": credentials if completed else None,
                       "result": {"name": "Browser-Lab", "initial_password": credentials["password"]} if completed else None, "error": None}]
        elif path == "vms/Browser-Lab/credentials":
            if route.request.method == "PUT":
                credentials = {**credentials, **route.request.post_data_json, "source": "recorded", "job_id": None}
            result = credentials
        else:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Fixture endpoint not provided"}')
            return
        route.fulfill(content_type="application/json", body=json.dumps(result))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.route("**/api/**", route_api)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/#jobs")
        page.locator("#jobs-table").get_by_role("button", name="View result", exact=True).click()
        expect(page.locator("#job-credentials")).to_contain_text("automatically when deployment completes")
        completed = True
        # Reproduces the original bug: the dialog is left open while the job completes.
        expect(page.locator('#job-credentials input[name="password"]')).to_have_value("browser-only-test-password", timeout=10000)
        expect(page.locator('#job-credentials input[name="username"]')).to_have_value("kali")
        page.locator("#job-credentials").get_by_role("button", name="Copy password", exact=True).click()
        expect(page.locator("#toasts")).to_contain_text("Password copied")
        assert page.evaluate("navigator.clipboard.readText()") == "browser-only-test-password"
        page.locator("#job-dialog .close-dialog").click()
        expect(page.locator("#jobs-table").get_by_role("button", name="View credentials", exact=True)).to_be_visible()
        page.locator('nav a[href="#overview"]').click()
        page.locator("#vm-table").get_by_role("button", name="Credentials", exact=True).click()
        expect(page.locator('#credentials-body .credential-card input[name="password"]')).to_have_value("browser-only-test-password")
        page.locator("#credentials-body summary").click()
        page.locator("#credentials-body form").get_by_label("Guest username").fill("fixture-admin")
        page.locator("#credentials-body form").get_by_label("Known password").fill("updated-test-password")
        page.locator("#credentials-body form").get_by_role("button", name="Save credentials", exact=True).click()
        expect(page.locator('#credentials-body .credential-card input[name="password"]')).to_have_value("updated-test-password")
        expect(page.locator('#credentials-body .credential-card input[name="username"]')).to_have_value("fixture-admin")
        assert credentials["vm_uuid"] == "fixture-uuid"
        output = Path(__file__).resolve().parents[1] / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "credentials-fixture.png"), full_page=True)
        browser.close()
    assert not errors, errors
    print("Credential browser checks passed: live job-dialog updates, labeled username/password, clipboard, VM credentials and manual recording.")


if __name__ == "__main__":
    main()
