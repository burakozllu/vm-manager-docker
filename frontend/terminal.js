import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

const $ = selector => document.querySelector(selector);
const sessions = new Map();
let selected = null;
let theme = localStorage.getItem("vm-manager-terminal-theme") || "amber";
let effects = localStorage.getItem("vm-manager-terminal-crt") !== "false";
const colors = {amber: ["#ffbf69", "#140e07"], green: ["#86f6a0", "#07140c"], ice: ["#b8e8ff", "#08131a"], modern: ["#d8e3f5", "#0b111b"]};
if (!colors[theme]) theme = "amber";

function palette() {
  const [foreground, background] = colors[theme];
  const result = {foreground, background, cursor: foreground, cursorAccent: background, selectionBackground: foreground + "40"};
  if (theme !== "modern") for (const key of ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite"]) result[key] = foreground;
  return result;
}
function applyTheme() {
  $("#terminal-stage").style.setProperty("--phosphor", colors[theme][0]);
  $("#terminal-stage").style.setProperty("--terminal-bg", colors[theme][1]);
  $("#terminal-stage").classList.toggle("terminal-crt", effects);
  for (const session of sessions.values()) session.term.options.theme = palette();
  localStorage.setItem("vm-manager-terminal-theme", theme);
  localStorage.setItem("vm-manager-terminal-crt", String(effects));
}
function setStatus(session, message) {
  session.status = message;
  if (session.id === selected) $("#terminal-status").textContent = message;
}
function fit(session) {
  if (!session || session.id !== selected || $("#page-terminals").hidden) return;
  try { session.fit.fit(); } catch (_) { /* The tab may be closing during resize. */ }
}
function select(id) {
  selected = id;
  for (const session of sessions.values()) {
    session.view.hidden = session.id !== id;
    session.tab.classList.toggle("active", session.id === id);
  }
  const session = sessions.get(id);
  $("#terminal-empty").hidden = Boolean(session);
  $("#terminal-status").textContent = session?.status || "No active session";
  requestAnimationFrame(() => { fit(session); session?.term.focus(); });
}
function connect(session) {
  if (session.socket) session.socket.close();
  const query = new URLSearchParams({...session.options, cols: session.term.cols, rows: session.term.rows});
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/terminals/ws?${query}`);
  socket.binaryType = "arraybuffer";
  session.socket = socket;
  setStatus(session, "Connecting…");
  socket.addEventListener("message", event => {
    if (session.socket !== socket) return;
    if (typeof event.data !== "string") { session.term.write(new Uint8Array(event.data)); return; }
    const message = JSON.parse(event.data);
    if (message.type === "status") setStatus(session, message.state === "connected" ? "Connected" : "Connecting…");
    if (message.type === "error") { setStatus(session, "Connection error"); session.term.writeln("\r\n" + message.message); }
    if (message.type === "exit") { setStatus(session, "Exited · " + message.code); session.term.writeln("\r\n[Session ended]"); }
  });
  socket.addEventListener("close", event => { if (session.socket === socket && session.status === "Connected") setStatus(session, "Disconnected"); else if (session.socket === socket && session.status === "Connecting…") { setStatus(session, "Disconnected"); session.term.writeln("\r\n" + (event.reason || "Unable to open the terminal. Check host configuration and SSH access.")); } });
  socket.addEventListener("error", () => { if (session.socket === socket) setStatus(session, "Connection error"); });
}
function send(session, data, encoding) {
  if (session.socket?.readyState !== WebSocket.OPEN) return;
  const characters = Array.from(data);
  for (let offset = 0; offset < characters.length; offset += 4096) session.socket.send(JSON.stringify({type: "input", data: characters.slice(offset, offset + 4096).join(""), ...(encoding ? {encoding} : {})}));
}
function close(id) {
  const session = sessions.get(id);
  if (!session) return;
  session.socket?.close(); session.term.dispose(); session.tab.remove(); session.view.remove(); sessions.delete(id);
  if (selected === id) select([...sessions.keys()].at(-1) || null);
}
function open(options) {
  location.hash = "terminals";
  const id = crypto.randomUUID(), tab = document.createElement("div"), choose = document.createElement("button"), remove = document.createElement("button"), view = document.createElement("div");
  tab.className = "terminal-tab"; choose.type = remove.type = "button";
  choose.textContent = options.target === "host" ? (options.shell === "cmd" ? "Windows CMD" : "Windows PowerShell") : options.name;
  remove.textContent = "×"; remove.className = "terminal-tab-close"; remove.setAttribute("aria-label", "Close " + choose.textContent);
  choose.addEventListener("click", () => select(id)); remove.addEventListener("click", () => close(id)); tab.append(choose, remove); $("#terminal-tabs").append(tab);
  view.className = "terminal-view"; $("#terminal-stage").append(view);
  const term = new Terminal({fontFamily: '"Cascadia Mono", "DejaVu Sans Mono", monospace', fontSize: 15, lineHeight: 1.2,
                             cursorBlink: true, scrollback: 5000, theme: palette(), allowProposedApi: false});
  const addon = new FitAddon(); term.loadAddon(addon); term.open(view);
  const session = {id, options, tab, view, term, fit: addon, status: "Connecting…", socket: null}; sessions.set(id, session);
  term.onData(data => send(session, data)); term.onBinary(data => send(session, data, "binary"));
  term.onResize(({cols, rows}) => { if (session.socket?.readyState === WebSocket.OPEN) session.socket.send(JSON.stringify({type: "resize", cols, rows})); });
  select(id); requestAnimationFrame(() => { fit(session); connect(session); });
  return id;
}
window.vmTerminals = {open, fit: () => fit(sessions.get(selected)), closeAll: () => [...sessions.keys()].forEach(close)};
$("#terminal-theme").value = theme; $("#terminal-effects").checked = effects;
$("#terminal-theme").addEventListener("change", event => { theme = event.target.value; applyTheme(); });
$("#terminal-effects").addEventListener("change", event => { effects = event.target.checked; applyTheme(); });
$("#terminal-reconnect").addEventListener("click", () => { const session = sessions.get(selected); if (session) { session.term.writeln("\r\n[Reconnecting]\r\n"); connect(session); } });
$("#terminal-copy").addEventListener("click", async () => { const session = sessions.get(selected); if (session?.term.hasSelection()) await navigator.clipboard.writeText(session.term.getSelection()); });
$("#terminal-paste").addEventListener("click", async () => { const session = sessions.get(selected); if (session) { try { session.term.paste(await navigator.clipboard.readText()); session.term.focus(); } catch (_) { setStatus(session, "Clipboard access denied; use your browser's paste shortcut"); } } });
new ResizeObserver(() => requestAnimationFrame(() => fit(sessions.get(selected)))).observe($("#terminal-stage"));
window.addEventListener("beforeunload", () => window.vmTerminals.closeAll());
applyTheme();
