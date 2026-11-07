"use strict";
const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const state = {vms: [], jobs: [], config: null, host: null, page: "overview", seen: new Map(), initialJobs: true, inventoryLoaded: false, openJobId: null, jobVersion: null};
function el(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
function button(text, action, className = "secondary small") { const b = el("button", text, "button " + className); b.type = "button"; b.addEventListener("click", action); return b; }
function badge(status) { return el("span", status, "pill " + status); }
function toast(message, error = false) { const node = el("div", message, "toast" + (error ? " error" : "")); $("#toasts").append(node); setTimeout(() => node.remove(), error ? 13000 : 6500); }
function alertMessage(message) { $("#alert").textContent = message || ""; $("#alert").hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch("/api" + path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
async function post(path, data) { return api(path, {method: "POST", body: JSON.stringify(data)}); }
async function submit(path, data) {
  try { const job = await post(path, data); toast("Queued: " + job.argv.join(" ")); await loadJobs(); return job; }
  catch (error) { toast(error.message, true); return null; }
}
function go(page) {
  if (!["overview", "deploy", "jobs", "api", "host", "terminals"].includes(page)) page = "overview";
  state.page = page;
  $$(".page").forEach(node => node.hidden = node.id !== "page-" + page);
  $$("nav a").forEach(node => node.classList.toggle("active", node.dataset.page === page));
  $("#page-title").textContent = {overview: "Virtual machines", deploy: "Deploy a VM", jobs: "Activity", api: "API & CLI", host: "Host details", terminals: "Terminals"}[page];
  if (page === "host") loadHost();
  if (page === "jobs") loadJobs();
  if (page === "terminals") requestAnimationFrame(() => window.vmTerminals?.fit());
}
function renderVMs() {
  const tbody = $("#vm-table"); tbody.replaceChildren();
  const filter = $("#search").value.toLowerCase();
  const vms = state.vms.filter(vm => vm.name.toLowerCase().includes(filter));
  if (!vms.length) { const td = el("td", state.vms.length ? "No machines match your filter." : "No registered VMs yet. Deploy your first environment.", "empty"); td.colSpan = 5; const row = el("tr"); row.append(td); tbody.append(row); }
  vms.forEach(vm => {
    const row = el("tr");
    const name = el("td"), machine = el("div", undefined, "machine"), icon = el("span", "▦", "machine-icon"), text = el("div");
    text.append(el("div", vm.name, "machine-name"), el("div", vm.os_type || "VirtualBox guest", "machine-sub")); machine.append(icon, text); name.append(machine);
    const status = el("td"); status.append(badge(vm.state));
    const resources = el("td"); resources.append(el("div", (vm.memory / 1024).toFixed(vm.memory % 1024 ? 1 : 0) + " GB RAM", "resource-main"), el("div", vm.cpus + " virtual CPU" + (vm.cpus === 1 ? "" : "s"), "resource-sub"));
    const ssh = el("td"); const forwards = (vm.forwards || []).filter(rule => rule.guest_port === 22 && rule.protocol === "tcp");
    if (forwards.length) { ssh.append(el("span", ":" + forwards[0].host_port, "port-tag"), el("div", forwards[0].host_ip === "127.0.0.1" ? "SSH tunnel" : "Local network", "resource-sub")); } else ssh.append(el("span", "Not configured", "machine-sub"));
    const actions = el("td"), controls = el("div", undefined, "controls");
    const primary = button(vm.state === "running" ? "Stop" : vm.state === "paused" ? "Resume" : "Start", () => submit(`/vms/${encodeURIComponent(vm.name)}/actions/${vm.state === "running" ? "stop" : "start"}`, {}));
    controls.append(primary, button("Terminal", () => window.vmTerminals.open({target: "vm", name: vm.name}), "secondary small"), button("Credentials", () => openCredentials(vm), "secondary small"), button("Manage", () => openVM(vm), "secondary small"));
    actions.append(controls); row.append(name, status, resources, ssh, actions); tbody.append(row);
  });
  $("#stat-total").textContent = state.vms.length;
  $("#inventory-count").textContent = state.vms.length;
  const running = state.vms.filter(vm => vm.state === "running");
  $("#stat-running").textContent = running.length;
  $("#stat-memory").textContent = (running.reduce((total, vm) => total + vm.memory, 0) / 1024).toFixed(1) + " GB";
  const vmSelect = $("#terminal-vm"), previous = vmSelect.value; vmSelect.replaceChildren();
  const placeholder = el("option", "Choose a running VM…"); placeholder.value = ""; vmSelect.append(placeholder);
  running.forEach(vm => { const option = el("option", vm.name); option.value = vm.name; vmSelect.append(option); });
  vmSelect.value = running.some(vm => vm.name === previous) ? previous : "";
}
async function loadVMs() {
  const refresh = $("#refresh"); refresh.disabled = true;
  try {
    const response = await api("/vms"); state.vms = response.data || []; state.inventoryLoaded = true; renderVMs(); alertMessage("");
    $("#inventory-caption").textContent = response.stale ? "Last known state · an operation is running" : "Live inventory from your Windows host";
    $("#last-refresh").textContent = "Updated " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  } catch (error) {
    alertMessage(error.message); $("#inventory-caption").textContent = "Windows host unavailable · check SSH connectivity and refresh";
    if (!state.inventoryLoaded) { const row = el("tr"), cell = el("td", "Host unavailable. Wake the Windows PC and click Refresh.", "empty"); cell.colSpan = 5; row.append(cell); $("#vm-table").replaceChildren(row); }
  }
  finally { refresh.disabled = false; }
}
async function loadHost() {
  try {
    const response = await api("/host"); state.host = response.data;
    $("#stat-free").textContent = response.data.memory_free_gib + " GB";
    $("#host-os").textContent = response.data.os;
    $("#host-details").textContent = JSON.stringify({configuration: state.config, diagnostics: response.data, stale: response.stale}, null, 2);
  } catch (error) { $("#host-details").textContent = error.message; }
}
async function copySSH(vm) {
  try { const response = await api(`/vms/${encodeURIComponent(vm.name)}/ssh`); await navigator.clipboard.writeText(response.data.command); toast("SSH command copied to clipboard"); }
  catch (error) { toast(error.message, true); }
}
function duration(job) {
  if (!job.started_at) return "—";
  const seconds = Math.max(0, Math.round(((job.finished_at ? new Date(job.finished_at) : new Date()) - new Date(job.started_at)) / 1000));
  return seconds >= 60 ? Math.floor(seconds / 60) + "m " + seconds % 60 + "s" : seconds + "s";
}
function credentialCard(parent, credentials) {
  const card = el("section", undefined, "credential-card"), row = el("div", undefined, "field-row");
  card.append(el("h3", "Login credentials"));
  for (const [key, title] of [["username", "Username"], ["password", "Password · also used for sudo"]]) {
    const [label, input] = inputLabel(title, "text", credentials[key], key); input.readOnly = true; input.autocomplete = "off"; input.className = "credential-value"; row.append(label);
  }
  card.append(row, button("Copy password", async () => { try { await navigator.clipboard.writeText(credentials.password); toast("Password copied"); } catch (error) { toast(error.message, true); } }, "primary small"));
  card.append(el("p", `${credentials.vm_name || "VM"} · ${credentials.source === "recorded" ? "Recorded password" : "Generated during deployment"}${credentials.updated_at ? " · " + new Date(credentials.updated_at).toLocaleString() : ""}`, "form-note"));
  parent.append(card);
}
function renderJobDetails(job) {
  const version = [job.id, job.status, job.finished_at, Boolean(job.credentials || job.result?.initial_password)].join("|");
  if (state.jobVersion === version) return;
  state.jobVersion = version;
  $("#job-status").replaceChildren(badge(job.status), el("p", job.argv.join(" "), "form-note"));
  $("#job-result").textContent = JSON.stringify(job, null, 2);
  const target = $("#job-credentials"); target.replaceChildren();
  const credentials = job.credentials || (job.result?.initial_password ? {username: job.result.ssh_user || "kali", password: job.result.initial_password, vm_name: job.result.name, source: "generated", updated_at: job.finished_at} : null);
  if (credentials) credentialCard(target, credentials);
  else if (job.argv[0] === "deploy-kali") target.append(el("p", ["queued", "running"].includes(job.status) ? "The username and password will appear here automatically when deployment completes." : "This attempt did not produce login credentials. Use the successful deployment entry or the VM's Credentials button.", "form-note"));
}
function showJob(job) { state.openJobId = job.id; state.jobVersion = null; renderJobDetails(job); $("#job-dialog").showModal(); }
async function openCredentials(vm) {
  const dialog = $("#credentials-dialog"), body = $("#credentials-body"), saved = el("div"); body.replaceChildren(saved); $("#credentials-title").textContent = vm.name;
  saved.append(el("p", "Loading saved credentials…", "muted"));
  const details = el("details", undefined, "detail-section"); details.append(el("summary", "Record or update a known password"), el("p", "This saves your record only. It does not change the user account or password inside the VM.", "form-note"));
  const form = el("form"), [usernameLabel, username] = inputLabel("Guest username", "text", vm.ssh_user || "", "username"), [passwordLabel, password] = inputLabel("Known password", "password", "", "password");
  username.required = password.required = true; password.autocomplete = "new-password";
  const save = el("button", "Save credentials", "button secondary"); save.type = "submit"; form.append(usernameLabel, passwordLabel, save); details.append(form); body.append(details);
  const path = `/vms/${encodeURIComponent(vm.name)}/credentials`;
  form.addEventListener("submit", async event => {
    event.preventDefault(); save.disabled = true;
    try { const credentials = await api(path, {method: "PUT", body: JSON.stringify({vm_uuid: vm.uuid, username: username.value, password: password.value})}); saved.replaceChildren(); credentialCard(saved, credentials); password.value = ""; details.open = false; toast("Credentials saved"); }
    catch (error) { toast(error.message, true); } finally { save.disabled = false; }
  });
  dialog.showModal();
  try { const credentials = await api(path + "?vm_uuid=" + encodeURIComponent(vm.uuid)); saved.replaceChildren(); credentialCard(saved, credentials); username.value = credentials.username; }
  catch (error) { saved.replaceChildren(el("p", error.message, "form-note")); }
}
function renderJobs() {
  const tbody = $("#jobs-table"); tbody.replaceChildren();
  if (!state.jobs.length) { const row = el("tr"), td = el("td", "No operations yet. Your activity will appear here.", "empty"); td.colSpan = 5; row.append(td); tbody.append(row); }
  state.jobs.forEach(job => {
    const row = el("tr"), operation = el("td"), title = job.argv[0] === "upload" ? "upload file" : job.argv.join(" ");
    operation.append(el("div", title, "machine-name"), el("div", job.id.slice(0, 8), "machine-sub"));
    const status = el("td"); status.append(badge(job.status));
    const created = el("td", new Date(job.created_at).toLocaleString(), "resource-main");
    const time = el("td", duration(job), "resource-main"), details = el("td"); details.append(button(job.credentials || job.result?.initial_password ? "View credentials" : "View result", () => showJob(job)));
    row.append(operation, status, created, time, details); tbody.append(row);
  });
  $("#job-counter").textContent = state.jobs.filter(job => ["queued", "running"].includes(job.status)).length;
}
let jobsLoading = false;
async function loadJobs() {
  if (jobsLoading) return;
  jobsLoading = true;
  try {
    state.jobs = await api("/jobs");
    let completed = false;
    state.jobs.forEach(job => {
      const previous = state.seen.get(job.id);
      if (!state.initialJobs && previous && previous !== job.status && ["succeeded", "failed", "interrupted"].includes(job.status)) {
        completed = true;
        toast(job.status === "succeeded" ? "Completed: " + job.argv[0] + (job.argv[1] && job.argv[0] !== "upload" ? " " + job.argv[1] : "") : job.error || "Operation " + job.status, job.status !== "succeeded");
        if (job.argv[0] === "upload" && job.result?.path) { $("#deploy-iso").value = job.result.path; $("#deploy-source").value = job.result.path; }
        if (job.result?.initial_password) toast("Kali is ready. Open the job result to save its generated login/sudo password.");
      }
      state.seen.set(job.id, job.status);
    });
    state.initialJobs = false; renderJobs();
    if ($("#job-dialog").open && state.openJobId) {
      const current = state.jobs.find(job => job.id === state.openJobId) || await api("/jobs/" + state.openJobId);
      renderJobDetails(current);
    }
    if (completed) { await loadVMs(); loadHost(); }
  } catch (error) { $("#service-dot").className = "dot red-dot"; }
  finally { jobsLoading = false; }
}
function setDeployKind() {
  const kind = $("#deploy-kind").value, form = $("#deploy-form");
  $$("[data-kinds]", form).forEach(node => { const hidden = !node.dataset.kinds.split(" ").includes(kind); node.hidden = hidden; $$("input, select", node).forEach(input => input.disabled = hidden); });
  form.elements.iso.required = kind === "iso"; form.elements.source.required = kind === "import";
  form.elements.memory.value = kind === "kali" ? "8192" : kind === "iso" ? "4096" : "";
  form.elements.cpus.value = kind === "kali" ? "4" : kind === "iso" ? "2" : "";
  form.elements.user.value = kind === "kali" ? "kali" : "";
  form.elements.user.readOnly = kind === "kali";
  $("#deploy-note").textContent = {kali: "Verifies the official image, provisions your public key and generates a login/sudo password in the job result.", iso: "Creates the VM and boots the installer. Complete OS installation in the VM console on Windows, then enable guest SSH.", import: "Imports the appliance with fresh identities and SSH port allocation. Guest SSH must be enabled in the appliance."}[kind];
}
$("#deploy-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, values = Object.fromEntries(new FormData(form));
  const data = {kind: values.kind, name: values.name, access: values.access};
  for (const key of ["iso", "source", "sha256", "version", "os_type", "firmware", "user"]) if (values[key]) data[key] = values[key];
  for (const key of ["memory", "cpus", "disk"]) if (values[key]) data[key] = Number(values[key]);
  if (values.kind !== "kali") data.start = Boolean(values.start);
  const job = await submit("/vms", data); if (job) location.hash = "jobs";
});
$("#upload-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, submitButton = $("button", form); submitButton.disabled = true;
  try { const response = await fetch("/api/uploads", {method: "POST", body: new FormData(form)}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Upload failed"); toast("File received. Transfer to the Windows host is queued."); await loadJobs(); }
  catch (error) { toast(error.message, true); } finally { submitButton.disabled = false; }
});
function inputLabel(label, type, value, name) {
  const wrapper = el("label", label), input = el(type === "select" ? "select" : "input"); input.name = name;
  if (type !== "select") { input.type = type; input.value = value ?? ""; }
  wrapper.append(input); return [wrapper, input];
}
function section(parent, title, danger = false) { const s = el("section", undefined, "detail-section" + (danger ? " danger-section" : "")); s.append(el("h3", title)); parent.append(s); return s; }
function commandForm(parent, title, fields, callback, label = "Save changes") {
  const s = section(parent, title), form = el("form"), row = el("div", undefined, "field-row"), inputs = {};
  fields.forEach(field => { const [wrapper, input] = inputLabel(field.label, field.type || "text", field.value, field.name); inputs[field.name] = input; if (field.type === "select") field.options.forEach(([value, text]) => { const option = el("option", text); option.value = value; option.selected = value === field.value; input.append(option); }); if (field.min) input.min = field.min; row.append(wrapper); });
  const save = el("button", label, "button secondary"); save.type = "submit"; form.append(row, save); s.append(form);
  form.addEventListener("submit", async event => { event.preventDefault(); save.disabled = true; try { await callback(inputs); } finally { save.disabled = false; } }); return s;
}
async function openVM(vm) {
  const dialog = $("#vm-dialog"), body = $("#vm-dialog-body"); body.replaceChildren(); $("#vm-dialog-title").textContent = vm.name;
  const summary = el("div", undefined, "button-row"); summary.append(badge(vm.state), el("span", `${vm.cpus} vCPUs · ${(vm.memory / 1024).toFixed(1)} GB RAM`, "muted")); body.append(summary);
  const encoded = encodeURIComponent(vm.name), action = (name, options = {}) => submit(`/vms/${encoded}/actions/${name}`, options);
  const controls = el("div", undefined, "button-row");
  ["start", "stop", "restart", "pause", "resume", "save", "eject-iso"].forEach(name => controls.append(button(name, () => action(name)))); body.append(controls);
  controls.append(button("Credentials", () => openCredentials(vm), "primary small"));
  controls.append(button("Open terminal", () => { dialog.close(); window.vmTerminals.open({target: "vm", name: vm.name}); }, "primary small"));
  const ssh = section(body, "SSH connection"), sshbox = el("div", undefined, "ssh-box"), code = el("code", "Loading connection command…");
  sshbox.append(code, button("Copy", async () => { try { await navigator.clipboard.writeText(code.textContent); toast("Copied"); } catch (e) { toast(e.message, true); } })); ssh.append(sshbox);
  api(`/vms/${encoded}/ssh`).then(response => code.textContent = response.data.command).catch(error => code.textContent = error.message);
  commandForm(body, "Resources · power off before changing", [{name: "memory", label: "Memory · MiB", type: "number", value: vm.memory, min: 512}, {name: "cpus", label: "Virtual CPUs", type: "number", value: vm.cpus, min: 1}], async inputs => action("configure", {memory: Number(inputs.memory.value), cpus: Number(inputs.cpus.value)}));
  const forward = (vm.forwards || []).find(rule => rule.guest_port === 22);
  commandForm(body, "SSH forwarding", [{name: "user", label: "Guest username", value: vm.ssh_user || ""}, {name: "access", label: "Access", type: "select", value: forward?.host_ip === "127.0.0.1" ? "tunnel" : "lan", options: [["tunnel", "Windows SSH tunnel"], ["lan", "Direct LAN"]]}, {name: "ssh_port", label: "Host port · blank for auto", type: "number", value: forward?.host_port || "", min: 1}], async inputs => { const data = {access: inputs.access.value}; if (inputs.user.value) data.user = inputs.user.value; if (inputs.ssh_port.value) data.ssh_port = Number(inputs.ssh_port.value); return action("ssh-configure", data); });
  const snapshots = section(body, "Snapshots"), snapshotData = el("pre", "Use Refresh to list snapshots."), snapshotButtons = el("div", undefined, "button-row");
  snapshotButtons.append(button("Refresh snapshots", async () => { try { snapshotData.textContent = JSON.stringify((await api(`/vms/${encoded}/snapshots`)).data, null, 2); } catch (error) { snapshotData.textContent = error.message; } })); snapshots.append(snapshotButtons, snapshotData);
  commandForm(body, "Snapshot operation", [{name: "snapshot", label: "Snapshot name", value: "clean-install"}, {name: "action", label: "Operation", type: "select", value: "create", options: [["create", "Create"], ["restore", "Restore · VM must be off"], ["delete", "Delete"]]}], inputs => submit(`/vms/${encoded}/snapshots`, {action: inputs.action.value, snapshot: inputs.snapshot.value}), "Submit snapshot operation");
  const autostart = section(body, "Start when the Windows user signs in"), autoButtons = el("div", undefined, "button-row");
  ["enable", "disable", "status"].forEach(mode => autoButtons.append(button(mode, () => submit(`/vms/${encoded}/autostart`, {mode})))); autostart.append(autoButtons);
  const metadata = section(body, "Configuration"), pre = el("pre", JSON.stringify(vm, null, 2)); metadata.append(pre);
  const remove = section(body, "Remove virtual machine", true), removeLabel = el("label", undefined, "checkbox"), check = el("input"); check.type = "checkbox"; removeLabel.append(check, document.createTextNode("Permanently delete VM files and snapshots"));
  const removeButton = button("Remove VM", async () => { const job = await action("remove", {delete_disks: check.checked}); if (job) dialog.close(); }, "danger"); removeButton.disabled = !["poweroff", "aborted"].includes(vm.state); remove.append(el("p", "Removal requires a powered-off VM. Files are retained unless deletion is selected.", "form-note"), removeLabel, el("br"), removeButton);
  dialog.showModal();
}
$$(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#job-dialog").addEventListener("close", () => { state.openJobId = null; state.jobVersion = null; });
$("#refresh").addEventListener("click", loadVMs); $("#refresh-jobs").addEventListener("click", loadJobs); $("#refresh-host").addEventListener("click", loadHost);
$("#search").addEventListener("input", renderVMs); $("#deploy-kind").addEventListener("change", setDeployKind);
$("#terminal-host-powershell").addEventListener("click", () => window.vmTerminals.open({target: "host", shell: "powershell"}));
$("#terminal-host-cmd").addEventListener("click", () => window.vmTerminals.open({target: "host", shell: "cmd"}));
$("#terminal-open-vm").addEventListener("click", () => { const name = $("#terminal-vm").value; if (!name) { toast("Choose a running VM first", true); return; } const user = $("#terminal-user").value.trim(); window.vmTerminals.open({target: "vm", name, ...(user ? {user} : {})}); });
window.addEventListener("hashchange", () => go(location.hash.slice(1)));
const references = [["GET", "/api/vms", "List virtual machines"], ["POST", "/api/vms", "Deploy ISO, Kali or appliance"], ["POST", "/api/vms/{name}/actions/{action}", "Lifecycle and configuration"], ["GET", "/api/vms/{name}/ssh", "SSH connection command"], ["GET", "/api/vms/{name}/credentials", "Saved username and password"], ["POST", "/api/vms/{name}/snapshots", "Manage snapshots"], ["POST", "/api/vms/{name}/autostart", "Windows sign-in startup"], ["POST", "/api/commands", "Existing CLI argument arrays"], ["POST", "/api/uploads", "Transfer a local image"], ["GET", "/api/jobs/{id}", "Operation status and results"]];
references.forEach(([method, path, description]) => { const row = el("div", undefined, "api-row"); row.append(el("span", method, "api-method " + method.toLowerCase()), el("span", path, "api-path"), el("span", description, "api-description")); $("#api-reference").append(row); });
$("#setup-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, save = $("button[type=submit]", form); save.disabled = true; $("#setup-error").hidden = true;
  try {
    const values = new FormData(form), data = {};
    for (const key of ["hostname", "username", "vm_root", "vboxmanage", "sevenzip"]) data[key] = values.get(key);
    data.port = Number(values.get("port"));
    for (const key of ["private_key", "public_key", "known_hosts"]) { const file = values.get(key); if (file instanceof File && file.size) data[key] = await file.text(); }
    await post("/setup", data); form.reset(); await init();
  } catch (error) { $("#setup-error").textContent = error.message; $("#setup-error").hidden = false; }
  finally { save.disabled = false; }
});
async function init() {
  setDeployKind();
  try {
    state.config = await api("/config"); document.body.classList.remove("loading-config");
    if (state.config.configured === false) { document.body.classList.add("needs-setup"); $("#setup-screen").hidden = false; return; }
    document.body.classList.remove("needs-setup"); $("#setup-screen").hidden = true;
    $("#target-host").textContent = `${state.config.host.username}@${state.config.host.hostname}`; $("#service-dot").className = "dot green-dot";
  } catch (error) { document.body.classList.remove("loading-config"); alertMessage(error.message); $("#service-dot").className = "dot red-dot"; return; }
  go(location.hash.slice(1));
  await loadJobs(); await loadVMs(); loadHost();
  if (!state.polling) { state.polling = true; setInterval(loadJobs, 2500); setInterval(() => { if (state.page === "overview" && !state.jobs.some(job => ["queued", "running"].includes(job.status))) loadVMs(); }, 30000); }
}
init();
