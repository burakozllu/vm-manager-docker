"""First-run UI fixtures; no personal values or real server settings are changed."""
import json
import os

from playwright.sync_api import expect, sync_playwright


def main():
    configured = False
    submitted = {}
    errors = []

    def route_api(route):
        nonlocal configured, submitted
        path = route.request.url.split("/api/", 1)[1]
        if path == "setup" and route.request.method == "POST":
            submitted = route.request.post_data_json
            configured = True
            result = {"configured": True}
        elif path == "config":
            result = {"configured": True, "host": {"hostname": "hypervisor.example.invalid", "username": "test-operator"}, "defaults": {}} if configured else {"configured": False}
        elif path == "jobs":
            result = []
        elif path == "vms":
            result = {"data": [], "stale": False}
        elif path == "host":
            result = {"data": {"memory_free_gib": 8, "os": "Browser fixture"}, "stale": False}
        else:
            route.fulfill(status=404, content_type="application/json", body='{"detail":"Fixture endpoint not provided"}')
            return
        route.fulfill(content_type="application/json", body=json.dumps(result))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/api/**", route_api)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(os.environ.get("VM_MANAGER_URL", "http://127.0.0.1:8080"))
        expect(page.locator("#setup-screen")).to_be_visible()
        expect(page.locator("main")).to_be_hidden()
        page.get_by_label("Windows host", exact=True).fill("hypervisor.example.invalid")
        page.get_by_label("Windows SSH account", exact=True).fill("test-operator")
        page.locator('#setup-form input[name="private_key"]').set_input_files({"name": "identity", "mimeType": "text/plain", "buffer": b"synthetic-browser-key-payload"})
        page.get_by_role("button", name="Save & open dashboard", exact=True).click()
        expect(page.locator("#setup-screen")).to_be_hidden()
        expect(page.locator("main")).to_be_visible()
        self_host = submitted["hostname"]
        assert self_host == "hypervisor.example.invalid"
        assert submitted["private_key"] == "synthetic-browser-key-payload"
        assert submitted["port"] == 22
        assert page.locator('#setup-form input[name="private_key"]').input_value() == ""
        page.reload()
        expect(page.locator("#setup-screen")).to_be_hidden()
        expect(page.locator("#target-host")).to_contain_text("hypervisor.example.invalid")
        browser.close()
    assert not errors, errors
    print("First-run browser checks passed: empty configuration, setup submission, dashboard transition, form cleanup and persisted configuration reuse.")


if __name__ == "__main__":
    main()
