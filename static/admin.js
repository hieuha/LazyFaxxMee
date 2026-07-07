"use strict";
// FaxxMe admin console: operators + transmissions. Standalone (no app.js).
// Auth is a single hashed password (FAXXME_ADMIN_PASSWORD_HASH) — no user account, no shared DB.

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
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const _pad = (n) => String(n).padStart(2, "0");
const fmtTime = (t) => {                    // compact, fixed-width: 2026-07-06 19:02
  const d = new Date(t * 1000);
  return `${d.getFullYear()}-${_pad(d.getMonth() + 1)}-${_pad(d.getDate())} ${_pad(d.getHours())}:${_pad(d.getMinutes())}`;
};

// Condense a raw User-Agent to "Browser · OS" (full string stays in the title tooltip).
function shortUA(ua) {
  const s = ua || "";
  if (/FaxxMe-Agent/i.test(s)) return "FaxxMe agent (Pi)";
  const br = /Edg\//.test(s) ? "Edge" : /OPR\//.test(s) ? "Opera" : /Chrome\//.test(s) ? "Chrome"
    : /Firefox\//.test(s) ? "Firefox" : (/Safari\//.test(s) && !/Chrome/.test(s)) ? "Safari" : "";
  const os = /Android/.test(s) ? "Android" : /(iPhone|iPad|iOS)/.test(s) ? "iOS"
    : /(Macintosh|Mac OS X)/.test(s) ? "macOS" : /Windows/.test(s) ? "Windows"
    : /Linux/.test(s) ? "Linux" : "";
  const label = [br, os].filter(Boolean).join(" · ");
  return label || (s.length > 30 ? s.slice(0, 30) + "…" : s);
}

const PER_PAGE = 20;                 // default page size for both tables
let usersPage = 0;
let faxPage = 0;
let faxQuery = "";

// ------------------------------------------------------------------ boot ---
async function init() {
  try {
    await api("/api/admin/stats");   // 200 only if the admin cookie is valid
    show("admin");
    await refreshAll();
  } catch (err) {
    if (err.status === 401) { show("login"); return; }   // not authenticated -> password gate
    show("login"); flashLogin("✗ " + err.message, "err");
  }
}

function show(which) {
  for (const id of ["admin-loading", "admin-login", "admin"]) $(id).classList.add("hidden");
  $({ loading: "admin-loading", login: "admin-login", admin: "admin" }[which]).classList.remove("hidden");
}

function flash(text, cls = "info") {
  const el = $("admin-msg");
  el.textContent = text; el.className = "msg " + cls;
  el.classList.remove("hidden");
}
function flashLogin(text, cls = "err") {
  const el = $("admin-login-msg");
  el.textContent = text; el.className = "msg " + cls;
}

// -------------------------------------------- shared modals (match console) ---
// Terminal-styled confirm, same look as the main console's confirmBox.
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
    const onKey = (e) => { if (e.key === "Escape") done(false); else if (e.key === "Enter") done(true); };
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) return done(false);
      const a = e.target.getAttribute("data-act");
      if (a) done(a === "ok");
    });
    document.addEventListener("keydown", onKey);
    requestAnimationFrame(() => overlay.querySelector('[data-act="ok"]').focus());
  });
}

const _stamp = (t) => {
  const d = new Date(t * 1000);
  return `${d.getFullYear()}-${_pad(d.getMonth() + 1)}-${_pad(d.getDate())} ` +
         `${_pad(d.getHours())}:${_pad(d.getMinutes())}:${_pad(d.getSeconds())}`;
};

// View one transmission as the printed paper slip (same receipt style as the console).
function openFaxModal(f) {
  const rule = "-".repeat(32);
  const imgTag = f.has_image ? `<img class="r-img" src="/api/admin/faxes/${f.id}/image" alt="fax image">` : "";
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay receipt-overlay";
  overlay.innerHTML =
    `<div class="receipt-wrap">
       <div class="receipt">
         <div class="r-head">FAXXME</div>
         <div class="r-rule">${rule}</div>
         <div class="r-meta r-from"></div>
         <div class="r-meta r-to"></div>
         <div class="r-meta r-time"></div>
         <div class="r-meta r-status"></div>
         <div class="r-rule">${rule}</div>
         <div class="r-body"></div>
         ${imgTag}
         <div class="r-rule">${rule}</div>
         <div class="r-end">.: end of message :.</div>
       </div>
       <button type="button" class="ghost r-close">✕ close</button>
     </div>`;
  overlay.querySelector(".r-from").textContent = `FROM: ${f.sender_display} @${f.sender_name}`;
  overlay.querySelector(".r-to").textContent = `TO:   ${f.recipient_display} @${f.recipient_name}`;
  overlay.querySelector(".r-time").textContent = `TIME: ${_stamp(f.created_at)}`;
  const cleared = [f.sender_deleted && "S", f.recipient_deleted && "R"].filter(Boolean).join("/");
  overlay.querySelector(".r-status").textContent =
    `STATUS: #${f.id} ${f.status}${cleared ? `  (cleared ${cleared})` : ""}`;
  const body = overlay.querySelector(".r-body");
  if (f.body) body.textContent = f.body; else body.classList.add("hidden");
  document.body.appendChild(overlay);
  const done = () => { document.removeEventListener("keydown", onKey); overlay.remove(); };
  const onKey = (e) => { if (e.key === "Escape") done(); };
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.classList.contains("r-close")) done();
  });
  document.addEventListener("keydown", onKey);
}

// ----------------------------------------------------------------- login ---
$("admin-login-form").onsubmit = async (e) => {
  e.preventDefault();
  const password = $("admin-pass").value;
  try {
    await api("/api/admin/login", { method: "POST", body: new URLSearchParams({ password }) });
    $("admin-pass").value = "";
    show("admin");
    usersPage = 0; faxPage = 0; faxQuery = ""; $("fax-search").value = "";
    await refreshAll();
  } catch (err) {
    flashLogin("✗ " + err.message, "err");
    if (err.status === 403) $("admin-disabled-note").style.display = "block";
  }
};

$("admin-logout").onclick = async () => {
  try { await api("/api/admin/logout", { method: "POST" }); } catch (_) {}
  show("login");
  flashLogin("locked. enter the admin password to continue.", "info");
};

async function refreshAll() {
  await Promise.all([loadStats(), loadUsers(), loadFaxes()]);
}

// ----------------------------------------------------------------- stats ---
async function loadStats() {
  const s = await api("/api/admin/stats");
  const cards = [
    ["operators", s.users], ["online now", s.online], ["transmissions", s.faxes],
    ["queued", s.pending], ["delivered", s.delivered], ["with image", s.images],
  ];
  $("stats").innerHTML = cards.map(([label, val]) =>
    `<div class="stat"><div class="stat-num">${val}</div><div class="stat-label">${label}</div></div>`).join("");
}

// ---------------------------------------------------------------- pager ----
function renderPager(el, page, total, go) {
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));
  if (page > pages - 1) { page = pages - 1; }        // clamp if a page emptied out
  const from = total ? page * PER_PAGE + 1 : 0;
  const to = Math.min(total, (page + 1) * PER_PAGE);
  el.innerHTML =
    `<button class="ghost tiny" data-nav="prev" ${page <= 0 ? "disabled" : ""}>‹ prev</button>` +
    `<span class="pageinfo">${from}–${to} of ${total}</span>` +
    `<button class="ghost tiny" data-nav="next" ${page >= pages - 1 ? "disabled" : ""}>next ›</button>`;
  el.querySelector('[data-nav="prev"]').onclick = () => go(page - 1);
  el.querySelector('[data-nav="next"]').onclick = () => go(page + 1);
}

// ----------------------------------------------------------------- users ---
async function loadUsers() {
  const { users, total } = await api(`/api/admin/users?limit=${PER_PAGE}&offset=${usersPage * PER_PAGE}`);
  const last = Math.max(0, Math.ceil(total / PER_PAGE) - 1);
  if (usersPage > last) { usersPage = last; return loadUsers(); }   // page emptied out -> step back
  $("users-count").textContent = `(${total})`;
  $("users-body").innerHTML = users.map(userRow).join("") ||
    `<tr><td colspan="8" class="empty">no operators on this page</td></tr>`;
  renderPager($("users-pager"), usersPage, total, (p) => { usersPage = p; loadUsers().catch(onErr); });
}

function userRow(u) {
  const tags = [];
  tags.push(u.online ? `<span class="dot on">●</span>online` : `<span class="dot off">○</span>offline`);
  if (u.node_online) tags.push(`<span class="tag-node">NODE</span>`);
  if (u.has_token) tags.push(`<span class="tag-tok">TOKEN</span>`);
  const actions = [];
  if (u.has_token) actions.push(`<button class="ghost tiny" data-act="revoke" data-id="${u.id}" data-name="${esc(u.username)}">revoke</button>`);
  actions.push(`<button class="danger tiny" data-act="deluser" data-id="${u.id}" data-name="${esc(u.username)}">delete</button>`);
  const ipLine = u.last_ip ? `<span class="mono">${esc(u.last_ip)}</span>` : `<span class="small">—</span>`;
  const seenLine = u.last_seen ? `<div class="small">${fmtTime(u.last_seen)}</div>` : "";
  const uaLine = u.last_ua ? `<div class="small ua" title="${esc(u.last_ua)}">${esc(shortUA(u.last_ua))}</div>` : "";
  return `<tr>
    <td class="mono nowrap">@${esc(u.username)}</td>
    <td>${esc(u.display_name)}</td>
    <td class="nowrap small">${fmtTime(u.created_at)}</td>
    <td class="statuscell">${tags.join(" ")}</td>
    <td class="sesscell">${ipLine}${seenLine}${uaLine}</td>
    <td class="num">${u.sent}</td>
    <td class="num">${u.received}</td>
    <td class="actions"><div class="actbtns">${actions.join("")}</div></td>
  </tr>`;
}

// ----------------------------------------------------------------- faxes ---
let faxMap = {};                     // id -> fax, so "view" can open the full slip

async function loadFaxes() {
  const { faxes, total } = await api(`/api/admin/faxes?limit=${PER_PAGE}&offset=${faxPage * PER_PAGE}` +
    (faxQuery ? `&q=${encodeURIComponent(faxQuery)}` : ""));
  const last = Math.max(0, Math.ceil(total / PER_PAGE) - 1);
  if (faxPage > last) { faxPage = last; return loadFaxes(); }       // page emptied out -> step back
  faxMap = {};
  faxes.forEach((f) => { faxMap[f.id] = f; });
  $("faxes-count").textContent = `(${total})`;
  $("faxes-body").innerHTML = faxes.map(faxRow).join("") ||
    `<tr><td colspan="6" class="empty">no transmissions${faxQuery ? " match that filter" : ""}</td></tr>`;
  renderPager($("faxes-pager"), faxPage, total, (p) => { faxPage = p; loadFaxes().catch(onErr); });
}

function faxRow(f) {
  const st = f.status === "delivered" ? "on" : "warn";
  const cleared = [f.sender_deleted && "S", f.recipient_deleted && "R"].filter(Boolean).join("/");
  const parts = [];
  if (f.body) parts.push(esc(f.body).slice(0, 120));
  if (f.has_image) parts.push(`<span class="tag-img">IMG</span>`);
  return `<tr>
    <td class="num small">${f.id}</td>
    <td class="nowrap"><span class="mono">@${esc(f.sender_name)}</span> → <span class="mono">@${esc(f.recipient_name)}</span></td>
    <td class="nowrap small">${fmtTime(f.created_at)}</td>
    <td><span class="pill ${st}">${f.status}</span>${cleared ? `<span class="small"> cleared:${cleared}</span>` : ""}</td>
    <td class="msgcell">${parts.join(" ")}</td>
    <td class="actions"><div class="actbtns">
      <button class="ghost tiny" data-act="viewfax" data-id="${f.id}">view</button>
      <button class="danger tiny" data-act="delfax" data-id="${f.id}">delete</button>
    </div></td>
  </tr>`;
}

// --------------------------------------------------------------- actions ---
const onErr = (err) => {
  if (err && err.status === 401) {            // admin session expired mid-use -> back to the gate
    show("login");
    flashLogin("session expired — enter the admin password.", "info");
    return;
  }
  flash("✗ " + (err && err.message || "request failed"), "err");
};

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("table.admin button[data-act]");   // row actions only, not modal buttons
  if (!btn) return;
  const { act, id, name } = btn.dataset;
  if (act === "viewfax") { if (faxMap[id]) openFaxModal(faxMap[id]); return; }
  try {
    if (act === "revoke") {
      if (!(await confirmBox(`Revoke @${name}'s device token? Their Pi agent is disconnected immediately.`,
        { title: "revoke token", ok: "REVOKE" }))) return;
      await api(`/api/admin/users/${id}/revoke-token`, { method: "POST" });
      flash(`✓ token revoked for @${name}`, "ok");
      await loadUsers();
    } else if (act === "deluser") {
      if (!(await confirmBox(
        `Delete @${name}? The account is anonymized and can no longer log in, but existing faxes ` +
        `are kept for the other party (shown as a deleted account). This cannot be undone.`,
        { title: "delete operator", ok: "DELETE" }))) return;
      await api(`/api/admin/users/${id}/delete`, { method: "POST" });
      flash(`✓ @${name} deleted (anonymized; their faxes were kept)`, "ok");
      await Promise.all([loadStats(), loadUsers(), loadFaxes()]);
    } else if (act === "delfax") {
      if (!(await confirmBox(`Permanently delete transmission #${id} for both parties? This cannot be undone.`,
        { title: "delete transmission", ok: "DELETE" }))) return;
      await api(`/api/admin/faxes/${id}/delete`, { method: "POST" });
      flash(`✓ deleted transmission #${id}`, "ok");
      await Promise.all([loadStats(), loadFaxes()]);
    }
  } catch (err) { onErr(err); }
});

$("admin-back").onclick = () => { location.href = "/"; };
$("admin-refresh").onclick = () => refreshAll().catch(onErr);

let searchTimer = null;
$("fax-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const v = e.target.value.trim();
  searchTimer = setTimeout(() => {
    faxQuery = v; faxPage = 0;
    loadFaxes().catch(onErr);
  }, 250);
});

init().catch(onErr);
