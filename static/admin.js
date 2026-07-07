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
const fmtTime = (t) => new Date(t * 1000).toLocaleString();

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
  $("users-count").textContent = `(${total})`;
  $("users-body").innerHTML = users.map(userRow).join("") ||
    `<tr><td colspan="7" class="empty">no operators on this page</td></tr>`;
  renderPager($("users-pager"), usersPage, total, (p) => { usersPage = p; loadUsers().catch(onErr); });
}

function userRow(u) {
  const tags = [];
  tags.push(u.online ? `<span class="dot on">●</span>online` : `<span class="dot off">○</span>offline`);
  if (u.node_online) tags.push(`<span class="tag-node">NODE</span>`);
  if (u.has_token) tags.push(`<span class="tag-tok">TOKEN</span>`);
  const actions = [];
  if (u.has_token) actions.push(`<button class="ghost tiny" data-act="revoke" data-id="${u.id}" data-name="${esc(u.username)}">revoke token</button>`);
  actions.push(`<button class="danger tiny" data-act="deluser" data-id="${u.id}" data-name="${esc(u.username)}">delete</button>`);
  return `<tr>
    <td class="mono">@${esc(u.username)}</td>
    <td>${esc(u.display_name)}</td>
    <td class="nowrap small">${fmtTime(u.created_at)}</td>
    <td class="statuscell">${tags.join(" ")}</td>
    <td class="num">${u.sent}</td>
    <td class="num">${u.received}</td>
    <td class="actions">${actions.join(" ")}</td>
  </tr>`;
}

// ----------------------------------------------------------------- faxes ---
async function loadFaxes() {
  const { faxes, total } = await api(`/api/admin/faxes?limit=${PER_PAGE}&offset=${faxPage * PER_PAGE}` +
    (faxQuery ? `&q=${encodeURIComponent(faxQuery)}` : ""));
  $("faxes-count").textContent = `(${total})`;
  $("faxes-body").innerHTML = faxes.map(faxRow).join("") ||
    `<tr><td colspan="6" class="empty">no transmissions${faxQuery ? " match that filter" : ""}</td></tr>`;
  renderPager($("faxes-pager"), faxPage, total, (p) => { faxPage = p; loadFaxes().catch(onErr); });
}

function faxRow(f) {
  const st = f.status === "delivered" ? "on" : "warn";
  const cleared = [f.sender_deleted && "S", f.recipient_deleted && "R"].filter(Boolean).join("/");
  const img = f.has_image
    ? ` <a class="imglink" href="/api/admin/faxes/${f.id}/image" target="_blank" rel="noopener">[img]</a>` : "";
  const preview = f.body ? esc(f.body).slice(0, 140) : `<span class="small">(image only)</span>`;
  return `<tr>
    <td class="num small">${f.id}</td>
    <td class="nowrap"><span class="mono">@${esc(f.sender_name)}</span> → <span class="mono">@${esc(f.recipient_name)}</span></td>
    <td class="nowrap small">${fmtTime(f.created_at)}</td>
    <td><span class="pill ${st}">${f.status}</span>${cleared ? `<span class="small"> cleared:${cleared}</span>` : ""}</td>
    <td class="msgcell">${preview}${img}</td>
    <td class="actions"><button class="danger tiny" data-act="delfax" data-id="${f.id}">delete</button></td>
  </tr>`;
}

// --------------------------------------------------------------- actions ---
const onErr = (err) => flash("✗ " + err.message, "err");

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, id, name } = btn.dataset;
  try {
    if (act === "revoke") {
      if (!confirm(`Revoke @${name}'s device token? Their Pi agent is disconnected immediately.`)) return;
      await api(`/api/admin/users/${id}/revoke-token`, { method: "POST" });
      flash(`✓ token revoked for @${name}`, "ok");
      await loadUsers();
    } else if (act === "deluser") {
      if (!confirm(`Delete @${name} and ALL faxes they sent or received? This cannot be undone.`)) return;
      await api(`/api/admin/users/${id}/delete`, { method: "POST" });
      flash(`✓ deleted @${name}`, "ok");
      await Promise.all([loadStats(), loadUsers(), loadFaxes()]);
    } else if (act === "delfax") {
      if (!confirm(`Permanently delete transmission #${id} for both parties?`)) return;
      await api(`/api/admin/faxes/${id}/delete`, { method: "POST" });
      flash(`✓ deleted transmission #${id}`, "ok");
      await Promise.all([loadStats(), loadFaxes()]);
    }
  } catch (err) { onErr(err); }
});

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
