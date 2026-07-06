"use strict";
// FaxxMe client: CRT boot -> auth -> console. Live delivery over WebSocket,
// physical printing over WebUSB (ESC/POS bytes are built server-side and forwarded raw).

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) {
    let d = data.detail;
    if (Array.isArray(d)) d = d.map((e) => (e && e.msg) ? e.msg : JSON.stringify(e)).join("; ");
    else if (d && typeof d === "object") d = d.msg || JSON.stringify(d);
    const err = new Error(d || r.statusText || "request failed");
    err.status = r.status;
    throw err;
  }
  return data;
};
const form = (obj) => {
  const f = new FormData();
  for (const k in obj) f.append(k, obj[k]);
  return f;
};

// ------------------------------------------------------- retro sounds ------
// synthesized with Web Audio (no assets): dial-up handshake on send, bell on receive
let _ac = null;
let soundOn = localStorage.getItem("fx_sound") !== "off";
function ac() {
  if (!_ac) { try { _ac = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) { return null; } }
  if (_ac.state === "suspended") _ac.resume();
  return _ac;
}
function _tone(freq, start, dur, type = "sine", gain = 0.14) {
  const ctx = ac(); if (!ctx) return;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type; o.frequency.value = freq; o.connect(g); g.connect(ctx.destination);
  const t = ctx.currentTime + start;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.start(t); o.stop(t + dur + 0.03);
}
function _noise(start, dur, gain = 0.045, freq = 1700) {
  const ctx = ac(); if (!ctx) return;
  const buf = ctx.createBuffer(1, Math.max(1, Math.ceil(ctx.sampleRate * dur)), ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource(); src.buffer = buf;
  const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq; bp.Q.value = 0.8;
  const g = ctx.createGain(); const t = ctx.currentTime + start;
  g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(bp); bp.connect(g); g.connect(ctx.destination);
  src.start(t); src.stop(t + dur);
}
function playSend() {          // stylized dial-up modem handshake
  if (!soundOn || !ac()) return;
  [1209, 1336, 1477, 941, 1336].forEach((f, i) => {   // DTMF "dialing"
    _tone(f, i * 0.11, 0.09, "square", 0.06); _tone(697, i * 0.11, 0.09, "square", 0.045);
  });
  _tone(420, 0.62, 0.9, "sine", 0.09);                // carrier
  _tone(1100, 0.75, 0.8, "sine", 0.06);
  _noise(0.95, 0.55, 0.05, 1600);                     // handshake screech
  _tone(2250, 1.05, 0.5, "sine", 0.05);
}
function playReceive() {       // bright telegraph/printer bell
  if (!soundOn || !ac()) return;
  _tone(1568, 0, 0.14, "sine", 0.18);
  _tone(2093, 0.05, 0.28, "sine", 0.13);
}
document.addEventListener("pointerdown", () => { if (soundOn) ac(); }, { once: true });  // unlock audio
{
  const btn = $("sound-toggle");
  if (btn) {
    const paint = () => { btn.textContent = soundOn ? "♪ sound: on" : "♪ sound: off"; };
    paint();
    btn.onclick = () => {
      soundOn = !soundOn;
      localStorage.setItem("fx_sound", soundOn ? "on" : "off");
      paint();
      if (soundOn) playReceive();
    };
  }
}

// ------------------------------------------------------------------ boot ---
const BOOT = [
  "initializing faxxme terminal...",
  "loading phosphor driver .......... OK",
  "spinning up modem ................ 56k CARRIER",
  "handshaking with mainframe ....... OK",
  "scanning for thermal printers ....",
  "ready.",
];
async function boot() {
  const el = $("bootlog");
  for (const line of BOOT) {
    const div = document.createElement("div");
    div.textContent = "> " + line;
    el.appendChild(div);
    await sleep(line.includes("scanning") ? 320 : 160);
  }
  $("boot").classList.add("hidden");
  try {
    const m = await api("/api/me");
    enterConsole(m);
  } catch (_) {
    showAuth();
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ auth ---
function showAuth() {
  $("auth").classList.remove("hidden");
  showLogin();
}
function showLogin() {
  $("login-form").classList.remove("hidden");
  $("register-form").classList.add("hidden");
  $("login-user").focus();
}
function showRegister() {
  $("register-form").classList.remove("hidden");
  $("login-form").classList.add("hidden");
  $("reg-user").focus();
}
$("tab-login").onclick = showLogin;
$("tab-register").onclick = showRegister;

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  const msg = $("login-msg"); msg.className = "msg"; msg.textContent = "authenticating...";
  try {
    const d = await api("/api/login", { method: "POST",
      body: form({ username: $("login-user").value, password: $("login-pass").value }) });
    $("auth").classList.add("hidden");
    enterConsole(await api("/api/me"));
  } catch (err) { msg.className = "msg err"; msg.textContent = "✗ " + err.message; }
};

$("register-form").onsubmit = async (e) => {
  e.preventDefault();
  const msg = $("reg-msg"); msg.className = "msg"; msg.textContent = "creating account...";
  try {
    await api("/api/register", { method: "POST", body: form({
      username: $("reg-user").value, password: $("reg-pass").value,
      display_name: $("reg-name").value }) });
    $("auth").classList.add("hidden");
    enterConsole(await api("/api/me"));
  } catch (err) { msg.className = "msg err"; msg.textContent = "✗ " + err.message; }
};

$("logout").onclick = async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (_) {}
  if (ws) { ws.close(); ws = null; }
  location.reload();
};

// --------------------------------------------------------------- console ---
let ME = null;
let nodeOnline = false;    // a printer agent (Pi node) is connected for my callsign
let localBridge = false;   // the server host has a wired printer for my callsign
async function enterConsole(m) {
  ME = m.user;
  $("console").classList.remove("hidden");
  $("who").textContent = ME.display_name + " @" + ME.username;
  localBridge = !!m.local_bridge;
  nodeOnline = !!m.node_online;
  if (m.local_bridge) {
    // This callsign owns a printer wired directly into the server host.
    $("connect-usb").classList.add("ghost");
    $("connect-usb").innerHTML = "▸ PRINTER (optional)";
    $("connect-usb").title = "this host already prints via its wired printer";
    $("local-note").innerHTML =
      "▲ <b>this host has a printer wired in.</b> Faxes to @" + ME.username +
      " print here automatically — even with no browser open. You don't need to \"Connect USB\".";
  }
  refreshPrinterPill();
  initTokenUI(m.has_token);
  await refreshUsers();
  await refreshLogs();
  updateComposeState();
  connectWS();
  autoBindPrinter();   // re-bind a previously-authorized USB printer, no click needed
}

// ---- device token for the headless printer agent ----
function initTokenUI(hasToken) {
  $("gen-token").textContent = hasToken ? "REGENERATE TOKEN" : "GENERATE TOKEN";
  $("token-active").classList.toggle("hidden", !hasToken);   // yellow "A device token is active"
}
$("gen-token").onclick = async () => {
  const regen = $("gen-token").textContent.startsWith("REGEN");
  if (regen && !(await confirmBox(
      "Regenerate device token? The current one stops working immediately — any Pi agent using it must be updated.",
      { title: "regenerate token", ok: "REGENERATE" }))) return;
  try {
    const d = await api("/api/token/regenerate", { method: "POST" });
    $("token-value").textContent = d.token;
    $("token-reveal").classList.remove("hidden");
    $("gen-token").textContent = "REGENERATE TOKEN";
    $("token-active").classList.remove("hidden");
  } catch (err) { $("token-status").className = "small"; $("token-status").textContent = "✗ " + err.message; }
};
$("token-copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("token-value").textContent);
    $("token-copy").textContent = "copied ✓";
    setTimeout(() => { $("token-copy").textContent = "copy"; }, 1500);
  } catch (_) { /* clipboard blocked on http — user can select manually */ }
};

// ---- recipient combobox (searchable, scales to many friends) ----
let ALL_USERS = [];
let ddActive = -1;   // keyboard-highlighted index in the current filtered list

async function refreshUsers() {
  try {
    const d = await api("/api/users");
    ALL_USERS = d.users;
    if (!$("user-dropdown").classList.contains("hidden")) renderDropdown();
  } catch (_) {}
}

function filteredUsers() {
  const q = $("fax-to").value.trim().toLowerCase();
  return ALL_USERS
    .filter((u) => !q || u.username.toLowerCase().includes(q) ||
                   (u.display_name || "").toLowerCase().includes(q))
    .sort((a, b) => (b.online - a.online) || a.username.localeCompare(b.username));  // online first
}

function renderDropdown() {
  const dd = $("user-dropdown");
  const list = filteredUsers();
  const online = ALL_USERS.filter((u) => u.online).length;
  let html = `<div class="dd-head"><span>${ALL_USERS.length} operators · ${online} online</span><span>↑↓ · enter</span></div>`;
  if (!list.length) {
    html += `<div class="dd-empty">${ALL_USERS.length ? "no match" : "no other operators yet"}</div>`;
  } else {
    html += list.map((u, i) =>
      `<div class="opt${i === ddActive ? " active" : ""}" data-user="${escapeHtml(u.username)}" role="option">
         <span class="dot ${u.online ? "on" : "off"}">${u.online ? "●" : "○"}</span>
         <span class="u">@${escapeHtml(u.username)}</span>
         <span class="nm">${escapeHtml(u.display_name || "")}</span>
       </div>`).join("");
  }
  dd.innerHTML = html;
}

function openDropdown() { ddActive = -1; renderDropdown(); $("user-dropdown").classList.remove("hidden"); $("fax-to").setAttribute("aria-expanded", "true"); }
function closeDropdown() { $("user-dropdown").classList.add("hidden"); $("fax-to").setAttribute("aria-expanded", "false"); ddActive = -1; }
function pickUser(username) { $("fax-to").value = username; closeDropdown(); updateComposeState(); $("fax-body").focus(); }
function scrollActive() { const el = $("user-dropdown").querySelector(".opt.active"); if (el) el.scrollIntoView({ block: "nearest" }); }

$("fax-to").addEventListener("focus", openDropdown);
$("fax-to").addEventListener("input", () => { ddActive = -1; openDropdown(); });
$("user-dropdown").addEventListener("mousedown", (e) => {
  const opt = e.target.closest(".opt");
  if (opt) { e.preventDefault(); pickUser(opt.getAttribute("data-user")); }
});
$("fax-to").addEventListener("keydown", (e) => {
  if ($("user-dropdown").classList.contains("hidden")) return;
  const list = filteredUsers();
  if (e.key === "ArrowDown") { e.preventDefault(); ddActive = Math.min(ddActive + 1, list.length - 1); renderDropdown(); scrollActive(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); ddActive = Math.max(ddActive - 1, 0); renderDropdown(); scrollActive(); }
  else if (e.key === "Enter") {
    if (ddActive >= 0 && list[ddActive]) { e.preventDefault(); pickUser(list[ddActive].username); }
    else if (list.length === 1) { e.preventDefault(); pickUser(list[0].username); }
    else closeDropdown();
  } else if (e.key === "Escape") { closeDropdown(); }
});
document.addEventListener("mousedown", (e) => { if (!e.target.closest(".combo")) closeDropdown(); });

function faxEntry(f, dir) {
  const who = dir === "in" ? ("@" + f.sender_name) : ("@" + f.recipient_name);
  const t = new Date(f.created_at * 1000).toLocaleString();
  const status = f.status === "delivered" ? "printed" : "queued";
  const div = document.createElement("div");
  div.className = "entry clickable";
  div.title = "click to view the printed slip";
  div.innerHTML =
    `<div class="meta">${dir === "in" ? "◀ FROM " : "▶ TO "} ${who} · ${t} · [${status}]${f.has_image ? " · [img]" : ""}</div>` +
    `<div class="bodytext"></div>`;
  div.querySelector(".bodytext").textContent = f.body || "(image only)";
  if (f.has_image) {
    const img = document.createElement("img");
    img.className = "fax-img"; img.loading = "lazy";
    img.src = `/api/fax/${f.id}/image`;
    div.appendChild(img);
  }
  div.onclick = () => openReceipt(f, dir);
  return div;
}
async function refreshLogs() {
  try {
    const inb = await api("/api/inbox");
    const box = $("inbox"); box.innerHTML = "";
    if (!inb.faxes.length) box.innerHTML = '<div class="empty">— nothing received —</div>';
    inb.faxes.forEach((f) => box.appendChild(faxEntry(f, "in")));
    $("clear-inbox").classList.toggle("hidden", inb.faxes.length === 0);
    const out = await api("/api/outbox");
    const ob = $("outbox"); ob.innerHTML = "";
    if (!out.faxes.length) ob.innerHTML = '<div class="empty">— nothing sent —</div>';
    out.faxes.forEach((f) => ob.appendChild(faxEntry(f, "out")));
    $("clear-outbox").classList.toggle("hidden", out.faxes.length === 0);
  } catch (_) {}
}

// ---- compose: live char counter + enable/disable TRANSMIT ----
const MAX_BODY = 200;
function updateComposeState() {
  const len = $("fax-body").value.length;
  const cnt = $("body-count");
  cnt.textContent = `${len} / ${MAX_BODY}`;
  cnt.classList.toggle("full", len >= MAX_BODY);
  const hasRecipient = $("fax-to").value.trim().length > 0;
  const hasImage = $("fax-image").files.length > 0;
  $("transmit-btn").disabled = !hasRecipient || (len === 0 && !hasImage);  // need recipient + (text OR image)
}
$("fax-body").addEventListener("input", updateComposeState);
$("fax-to").addEventListener("input", updateComposeState);

// ---- compose / send ----
$("fax-form").onsubmit = async (e) => {
  e.preventDefault();
  const msg = $("fax-msg"); msg.className = "msg";
  if (!$("fax-to").value.trim()) {
    msg.className = "msg err"; msg.textContent = "✗ pick a recipient callsign first";
    return;
  }
  if ($("fax-to").value.trim().toLowerCase() === ME.username) {
    msg.className = "msg err"; msg.textContent = "✗ you can't fax yourself — pick a friend's callsign";
    return;
  }
  if ($("fax-body").value.length > MAX_BODY) {
    msg.className = "msg err"; msg.textContent = `✗ message too long (max ${MAX_BODY})`;
    return;
  }
  msg.textContent = "transmitting...";
  const fd = new FormData();
  fd.append("to", $("fax-to").value);
  fd.append("body", $("fax-body").value);
  const file = $("fax-image").files[0];
  if (file) fd.append("image", file);
  try {
    const d = await api("/api/fax", { method: "POST", body: fd });
    msg.className = "msg ok";
    msg.textContent = (d.delivered ? "✓ delivered & printing on their end"
                                   : "✓ queued — prints when they come online")
                    + (d.has_image ? " · image dithered ✓" : "");
    playSend();
    $("fax-body").value = "";
    clearAttachment();
    await refreshLogs();
  } catch (err) {
    msg.className = "msg err"; msg.textContent = "✗ " + err.message;
    if (err.status === 429) alertBox(err.message, { title: "slow down" });   // rate limited
  }
};

// ---- terminal-styled confirm modal ----
function alertBox(message, { title = "notice", ok = "OK" } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML =
      `<div class="modal" role="alertdialog" aria-modal="true">
         <div class="modal-title">:: ${title}</div>
         <div class="modal-body"></div>
         <div class="modal-actions"><button data-act="ok">${ok}</button></div>
       </div>`;
    overlay.querySelector(".modal-body").textContent = message;
    document.body.appendChild(overlay);
    const done = () => { document.removeEventListener("keydown", onKey); overlay.remove(); resolve(); };
    const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") done(); };
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.getAttribute("data-act") === "ok") done();
    });
    document.addEventListener("keydown", onKey);
    requestAnimationFrame(() => overlay.querySelector('[data-act="ok"]').focus());
  });
}

function confirmBox(message, { title = "confirm", ok = "CONFIRM", cancel = "CANCEL" } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML =
      `<div class="modal" role="dialog" aria-modal="true">
         <div class="modal-title">:: ${title}</div>
         <div class="modal-body"></div>
         <div class="modal-actions">
           <button class="ghost" data-act="cancel">${cancel}</button>
           <button data-act="ok">${ok}</button>
         </div>
       </div>`;
    overlay.querySelector(".modal-body").textContent = message;
    document.body.appendChild(overlay);
    const done = (v) => { document.removeEventListener("keydown", onKey); overlay.remove(); resolve(v); };
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
      else if (e.key === "Enter") done(true);
    };
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) return done(false);
      const act = e.target.getAttribute("data-act");
      if (act) done(act === "ok");
    });
    document.addEventListener("keydown", onKey);
    requestAnimationFrame(() => overlay.querySelector('[data-act="ok"]').focus());
  });
}

// ---- receipt modal: render a fax exactly like the printed paper slip ----
function openReceipt(f, dir) {
  // inbox: who it's FROM; outbox: who you sent it TO
  const label = dir === "in"
    ? `FROM: ${f.sender_display} @${f.sender_name}`
    : `TO:   ${f.recipient_display} @${f.recipient_name}`;
  const stamp = fmtStamp(f.created_at);
  const rule = "-".repeat(32);
  const imgTag = f.has_image ? `<img class="r-img" src="/api/fax/${f.id}/image" alt="fax image">` : "";

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay receipt-overlay";
  overlay.innerHTML =
    `<div class="receipt-wrap">
       <div class="receipt">
         <div class="r-head">FAXXME</div>
         <div class="r-rule">${rule}</div>
         <div class="r-meta r-from"></div>
         <div class="r-meta r-time"></div>
         <div class="r-rule">${rule}</div>
         <div class="r-body"></div>
         ${imgTag}
         <div class="r-rule">${rule}</div>
         <div class="r-end">.: end of message :.</div>
       </div>
       <button type="button" class="ghost r-close">✕ close</button>
     </div>`;
  overlay.querySelector(".r-from").textContent = label;
  overlay.querySelector(".r-time").textContent = `TIME: ${stamp}`;
  overlay.querySelector(".r-body").textContent = f.body || "";
  if (!f.body) overlay.querySelector(".r-body").classList.add("hidden");
  document.body.appendChild(overlay);

  const done = () => { document.removeEventListener("keydown", onKey); overlay.remove(); };
  const onKey = (e) => { if (e.key === "Escape") done(); };
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.classList.contains("r-close")) done();
  });
  document.addEventListener("keydown", onKey);
}
function fmtStamp(sec) {
  const d = new Date(sec * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ---- clear inbox / outbox (only ever affects YOUR side) ----
$("clear-inbox").onclick = () => clearBox("inbox");
$("clear-outbox").onclick = () => clearBox("outbox");
async function clearBox(which) {
  const note = which === "inbox"
    ? "Clear your inbox? This removes these faxes from your account only — senders still keep their copy."
    : "Clear your outbox? This removes these faxes from your account only — recipients still keep their copy.";
  if (!(await confirmBox(note, { title: "clear " + which, ok: "CLEAR" }))) return;
  try { await api(`/api/${which}/clear`, { method: "POST" }); await refreshLogs(); }
  catch (err) { /* non-fatal */ }
}

// ---- image attachment: pick + live halftone preview (Floyd–Steinberg) ----
$("attach-btn").onclick = () => $("fax-image").click();
$("attach-clear").onclick = clearAttachment;
$("fax-image").onchange = () => {
  const file = $("fax-image").files[0];
  if (!file) return clearAttachment();
  $("attach-name").textContent = file.name;
  $("attach-clear").classList.remove("hidden");
  ditherPreview(file);
  updateComposeState();
};
function clearAttachment() {
  $("fax-image").value = "";
  $("attach-name").textContent = "";
  $("attach-clear").classList.add("hidden");
  $("attach-preview").classList.add("hidden");
  $("attach-img").removeAttribute("src");
  updateComposeState();
}
function ditherPreview(file) {
  const url = URL.createObjectURL(file);
  const im = new Image();
  im.onload = () => {
    const W = 240, H = Math.max(1, Math.round(im.height * W / im.width));
    const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d"); ctx.drawImage(im, 0, 0, W, H);
    const id = ctx.getImageData(0, 0, W, H), px = id.data;
    const g = new Float32Array(W * H);
    let mn = 255, mx = 0;
    for (let i = 0; i < W * H; i++) {
      const v = 0.299 * px[i*4] + 0.587 * px[i*4+1] + 0.114 * px[i*4+2];
      g[i] = v; if (v < mn) mn = v; if (v > mx) mx = v;
    }
    const rng = (mx - mn) || 1;
    for (let i = 0; i < W * H; i++) g[i] = (g[i] - mn) * 255 / rng;   // autocontrast
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {          // Floyd–Steinberg
      const i = y * W + x, oldv = g[i], nv = oldv < 128 ? 0 : 255, err = oldv - nv;
      g[i] = nv;
      if (x + 1 < W) g[i+1] += err * 7 / 16;
      if (y + 1 < H) {
        if (x > 0) g[i+W-1] += err * 3 / 16;
        g[i+W] += err * 5 / 16;
        if (x + 1 < W) g[i+W+1] += err * 1 / 16;
      }
    }
    for (let i = 0; i < W * H; i++) { const v = g[i]; px[i*4] = px[i*4+1] = px[i*4+2] = v; px[i*4+3] = 255; }
    ctx.putImageData(id, 0, 0);
    $("attach-img").src = cv.toDataURL();
    $("attach-preview").classList.remove("hidden");
    URL.revokeObjectURL(url);
  };
  im.onerror = () => { $("attach-name").textContent = file.name + " (not a readable image)"; };
  im.src = url;
}

// --------------------------------------------------------------- WebSocket -
let ws = null;
function setLink(up) {
  const el = $("link-state");
  el.textContent = up ? "UP" : "DOWN";
  el.className = "pill " + (up ? "on" : "off");
}
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setLink(true);
  ws.onclose = () => { setLink(false); setTimeout(() => { if (ME) connectWS(); }, 2500); };
  ws.onmessage = async (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "fax") await onIncomingFax(m);
    else if (m.type === "status") await refreshLogs();   // e.g. queued -> printed on the far end
    else if (m.type === "node") { nodeOnline = m.online; refreshPrinterPill(); }  // Pi agent on/off
  };
}

// ------------------------------------------------------------- WebUSB ------
let usb = null; // { device, iface, endpoint }
const seen = new Set();      // fax ids already handled
const queue = [];            // faxes waiting for a printer to bind

async function onIncomingFax(m) {
  if (seen.has(m.id)) { ackFax(m.id); return; }
  seen.add(m.id);
  playReceive();
  queue.push(m);
  await flushQueue();
  // reflect in inbox live
  await refreshLogs();
  const box = $("inbox"); box.classList.remove("flash"); void box.offsetWidth; box.classList.add("flash");
}

async function flushQueue() {
  if (!usb && !window.__forcePrintFallback) {
    $("printer-msg").className = "msg warn";
    $("printer-msg").textContent = `» ${queue.length} fax(es) waiting — bind a printer to print`;
    return;
  }
  while (queue.length) {
    const m = queue[0];
    let ok = false;
    try {
      const bytes = b64ToBytes(m.escpos_b64);
      if (usb) { await usbWrite(bytes); ok = true; }
      else { printFallback(m); ok = true; }
    } catch (err) {
      $("printer-msg").className = "msg err";
      $("printer-msg").textContent = "✗ print failed: " + err.message;
      break;
    }
    if (ok) { queue.shift(); ackFax(m.id); }
  }
}

function ackFax(id) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "ack", fax_id: id }));
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

function setPrinter(state, cls) {
  const el = $("printer-state");
  el.textContent = state; el.className = "pill " + cls;
}

// pick the best available print path for the PRINTER pill + button states
function refreshPrinterPill() {
  if (usb) setPrinter("ONLINE", "on");            // a printer bound in this browser
  else if (nodeOnline) setPrinter("NODE ✓", "on"); // an agent (Pi node) is printing for me
  else if (localBridge) setPrinter("WIRED", "on"); // this host prints for my callsign
  else setPrinter("OFFLINE", "off");
  // CONNECT PRINTER (WebUSB) is only needed when nothing else prints for you
  $("connect-usb").classList.toggle("hidden", (nodeOnline || localBridge) && !usb);
  // TEST works for whichever printer you have (browser USB, node, or bridge)
  $("print-test").disabled = !(usb || nodeOnline || localBridge);
}

// Bind a USB device as the printer. announce=true surfaces detailed errors (manual click);
// announce=false is used for silent auto-(re)binding on page load / hot-replug.
async function bindDevice(device, { announce = true } = {}) {
  try {
    await device.open();
    if (device.configuration === null) await device.selectConfiguration(1);
    let chosen = null;
    for (const iface of device.configuration.interfaces) {
      const alt = iface.alternates[0];
      const out = alt.endpoints.find((e) => e.direction === "out");
      if (out) {
        chosen = { number: iface.interfaceNumber, endpoint: out.endpointNumber };
        if (alt.interfaceClass === 7) break;   // prefer the printer-class interface
      }
    }
    if (!chosen) throw new Error("no OUT endpoint found on this device");
    await device.claimInterface(chosen.number);
    usb = { device, iface: chosen.number, endpoint: chosen.endpoint };
    refreshPrinterPill();
    $("printer-msg").className = "msg ok";
    $("printer-msg").textContent = `✓ bound to ${device.productName || "printer"} (if#${chosen.number} ep#${chosen.endpoint})`;
    await flushQueue();          // print anything that queued while it was unbound
    return true;
  } catch (err) {
    if (announce) throw err;
    try { await device.close(); } catch (_) {}
    return false;
  }
}

function unbindPrinter() {
  usb = null;
  refreshPrinterPill();
}

// Re-bind a printer the user already granted — no click needed (WebUSB permission persists).
async function autoBindPrinter() {
  if (!("usb" in navigator) || usb) return;
  try {
    for (const d of await navigator.usb.getDevices()) {
      if (await bindDevice(d, { announce: false })) return;
    }
  } catch (_) {}
}

if ("usb" in navigator) {
  // printer plugged back in → auto-rebind and flush the queue, no CONNECT click
  navigator.usb.addEventListener("connect", async (e) => {
    if (!usb) await bindDevice(e.device, { announce: false });
  });
  // printer unplugged → mark offline; the connect handler will resume when it returns
  navigator.usb.addEventListener("disconnect", (e) => {
    if (usb && e.device === usb.device) {
      unbindPrinter();
      $("printer-msg").className = "msg warn";
      $("printer-msg").textContent =
        "◦ printer unplugged — it auto-reconnects and prints any waiting faxes when you plug it back in";
    }
  });
}

$("connect-usb").onclick = async () => {
  if (!("usb" in navigator)) {
    $("printer-msg").className = "msg err";
    $("printer-msg").textContent = "✗ WebUSB unsupported. Use Chrome/Edge over HTTPS or localhost, or use 'test page' → browser print.";
    window.__forcePrintFallback = true;
    return;
  }
  try {
    const device = await navigator.usb.requestDevice({ filters: [] });
    await bindDevice(device);   // announce=true → detailed errors handled below
  } catch (err) {
    const pm = $("printer-msg");
    if (err.name === "NotFoundError") {
      // empty picker ("no compatible devices") or user cancelled
      pm.className = "msg info";
      pm.innerHTML = "◦ no printer offered. Chrome only lists a USB printer it can actually claim — " +
        "on <b>macOS</b> the system grabs standard printers (e.g. the PT-280), so they don't appear here, " +
        "and Safari/Firefox don't support WebUSB at all. Easiest path: fax the <b>pi</b> callsign and it " +
        "prints on the Raspberry Pi's wired printer automatically — no WebUSB needed.";
    } else if (String(err).toLowerCase().includes("claim") || err.name === "SecurityError") {
      pm.className = "msg err";
      pm.innerHTML = "✗ " + escapeHtml(err.message) +
        " — the OS/driver is holding this printer. On Linux: <code>sudo modprobe -r usblp</code> " +
        "then retry (note: that disables the host's local-bridge printing).";
    } else {
      pm.className = "msg err";
      pm.textContent = "✗ " + err.message;
    }
  }
};

async function usbWrite(bytes) {
  // chunk to be safe on small controllers
  const CH = 4096;
  for (let i = 0; i < bytes.length; i += CH) {
    await usb.device.transferOut(usb.endpoint, bytes.slice(i, i + CH));
  }
}

$("print-test").onclick = async () => {
  const pm = $("printer-msg");
  if (usb) {   // browser-bound printer → test client-side over WebUSB
    const bytes = new TextEncoder().encode(
      "\x1b@\x1ba\x01\x1b!\x38FAXXME\x1b!\x00\nself-test OK\n\n\n\n\x1dV\x00");
    try { await usbWrite(bytes); pm.className = "msg ok"; pm.textContent = "✓ test page sent to the USB printer"; }
    catch (err) { pm.className = "msg err"; pm.textContent = "✗ " + err.message; }
  } else {     // node/bridge → ask the server to print a test page there
    try {
      const d = await api("/api/test-print", { method: "POST" });
      pm.className = "msg " + (d.delivered ? "ok" : "warn");
      pm.textContent = d.delivered ? "✓ test page sent to your printer node" : "◦ no printer node online to test";
    } catch (err) { pm.className = "msg err"; pm.textContent = "✗ " + err.message; }
  }
};

// browser-print fallback for non-USB / unsupported printers
function printFallback(m) {
  const w = window.open("", "_blank", "width=380,height=600");
  if (!w) throw new Error("popup blocked");
  const t = new Date(m.created_at * 1000).toLocaleString();
  const imgTag = m.image_b64
    ? `<img src="${m.image_b64}" style="width:100%;image-rendering:pixelated;margin:6px 0">` : "";
  w.document.write(`<pre style="font:14px monospace;white-space:pre-wrap;width:300px">` +
    `        F A X X M E\n--------------------------------\n` +
    `FROM: ${escapeHtml(m.from)} @${escapeHtml(m.from_username)}\n` +
    `TIME: ${t}\n--------------------------------\n${escapeHtml(m.body)}\n</pre>` +
    imgTag +
    `<pre style="font:14px monospace;width:300px">--------------------------------\n     .: end of message :.</pre>`);
  w.document.close(); w.focus(); setTimeout(() => w.print(), 200);
}
function escapeHtml(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// periodic presence refresh
setInterval(() => { if (ME) refreshUsers(); }, 10000);

boot();
