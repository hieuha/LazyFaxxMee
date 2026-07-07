"""FaxxMe — FastAPI server: auth, fax routing, presence, and live delivery over WebSocket."""
import asyncio
import base64
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, Request, Response,
                     HTTPException, Form, File, UploadFile)
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, imaging, printer

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
PRINTER_POLL = float(os.environ.get("FAXXME_PRINTER_POLL", "4"))  # seconds between printer checks

# per-sender fax rate limit (anti-spam so nobody floods a friend's paper roll)
FAX_RATE_MAX = int(os.environ.get("FAXXME_FAX_RATE_MAX", "20"))         # max faxes per window (0 = off)
FAX_RATE_WINDOW = float(os.environ.get("FAXXME_FAX_RATE_WINDOW", "60"))  # window, seconds
_fax_hits: dict[int, list[float]] = {}

# /admin panel is gated by a single hashed password in the env — fully separate from user
# accounts. Set FAXXME_ADMIN_PASSWORD_HASH to sha256(password).hexdigest(); unset = disabled.
ADMIN_PASSWORD_HASH = os.environ.get("FAXXME_ADMIN_PASSWORD_HASH", "").strip().lower()


def admin_enabled() -> bool:
    return bool(ADMIN_PASSWORD_HASH)


def _rate_ok(user_id: int) -> bool:
    """Sliding-window rate check per sender; records a hit when allowed."""
    if FAX_RATE_MAX <= 0:
        return True
    now = time.monotonic()
    hits = [t for t in _fax_hits.get(user_id, ()) if now - t < FAX_RATE_WINDOW]
    ok = len(hits) < FAX_RATE_MAX
    if ok:
        hits.append(now)
    _fax_hits[user_id] = hits
    return ok


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    db.init()
    # Background watcher: flushes the host printer's queue on boot AND on hot-replug.
    watcher = asyncio.create_task(_printer_watch())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


app = FastAPI(title="FaxxMe", lifespan=lifespan)


# --------------------------------------------------------------------------- #
#  Presence: who has a printer online right now (a live WebSocket == online)   #
# --------------------------------------------------------------------------- #
class Presence:
    def __init__(self) -> None:
        self._sockets: dict[int, set[WebSocket]] = {}
        self._agents: dict[int, set[WebSocket]] = {}   # subset that authed via device token
        self._lock = asyncio.Lock()

    async def add(self, user_id: int, ws: WebSocket, is_agent: bool = False) -> None:
        async with self._lock:
            self._sockets.setdefault(user_id, set()).add(ws)
            if is_agent:
                self._agents.setdefault(user_id, set()).add(ws)

    async def remove(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            for store in (self._sockets, self._agents):
                socks = store.get(user_id)
                if socks:
                    socks.discard(ws)
                    if not socks:
                        store.pop(user_id, None)

    def online(self, user_id: int) -> bool:
        return bool(self._sockets.get(user_id))

    def node_online(self, user_id: int) -> bool:
        """True if a headless printer agent (Pi node) is connected for this user."""
        return bool(self._agents.get(user_id))

    def sockets(self, user_id: int) -> list[WebSocket]:
        return list(self._sockets.get(user_id, ()))

    def browser_sockets(self, user_id: int) -> list[WebSocket]:
        agents = self._agents.get(user_id, set())
        return [w for w in self._sockets.get(user_id, ()) if w not in agents]

    def agent_sockets(self, user_id: int) -> list[WebSocket]:
        return list(self._agents.get(user_id, ()))

    def online_count(self) -> int:
        return len(self._sockets)


presence = Presence()


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #
def current_user(request: Request) -> dict | None:
    uid = auth.read_token(request.cookies.get(auth.COOKIE_NAME))
    user = db.get_user(uid) if uid else None
    return user if user and not user.get("deleted_at") else None   # tombstoned -> logged out everywhere


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def require_admin(request: Request) -> None:
    """Admin auth is a signed cookie set by /api/admin/login — no user account involved."""
    if not auth.valid_admin_session(request.cookies.get(auth.ADMIN_COOKIE)):
        raise HTTPException(status_code=401, detail="admin authentication required")


def _client_ip(conn) -> str | None:
    """Best-effort client IP for a Request or WebSocket: honor Cloudflare / reverse-proxy
    headers (the real peer is hidden behind them), else fall back to the socket address."""
    h = conn.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0]
    ip = ip.strip() or (conn.client.host if conn.client else "")
    return ip[:64] or None


def _client_ua(conn) -> str | None:
    ua = (conn.headers.get("user-agent") or "").strip()
    return ua[:200] or None


def render_escpos(fax: dict) -> bytes:
    """Full ESC/POS for a fax: text header + body + optional dithered image raster."""
    sender = db.get_user(fax["sender_id"])
    image_escpos = imaging.escpos_raster(fax["image"]) if fax.get("image") else None
    return printer.build_receipt(
        sender["display_name"], sender["username"], fax["body"], fax["created_at"],
        image_escpos=image_escpos,
    )


def fax_payload(fax: dict) -> dict:
    """Build the WS message for one fax, including ready-to-print ESC/POS bytes."""
    sender = db.get_user(fax["sender_id"])
    payload = {
        "type": "fax",
        "id": fax["id"],
        "from": sender["display_name"],
        "from_username": sender["username"],
        "body": fax["body"],
        "created_at": fax["created_at"],
        "escpos_b64": base64.b64encode(render_escpos(fax)).decode(),
    }
    if fax.get("image"):
        payload["image_b64"] = "data:image/png;base64," + base64.b64encode(fax["image"]).decode()
    return payload


async def deliver(fax: dict) -> bool:
    """Try to deliver a fax now. Returns True if delivered, False if left pending."""
    rid = fax["recipient_id"]
    recipient = db.get_user(rid)
    # 1) browser printer online?
    socks = presence.sockets(rid)
    if socks:
        payload = fax_payload(fax)
        sent = False
        for ws in socks:
            try:
                await ws.send_json(payload)
                sent = True
            except Exception:
                pass
        if sent:
            return True  # client will ack -> mark delivered
    # 2) local bridge for the host-attached printer?
    if recipient and recipient["username"] == printer.LOCAL_USER and printer.local_available():
        if printer.print_local(render_escpos(fax)):
            db.mark_delivered(fax["id"])
            return True
    return False


async def _notify_status(fax_id: int) -> None:
    """Push a fax's new status to any online sender/recipient so their logs update live."""
    fax = db.get_fax(fax_id)
    if not fax:
        return
    msg = {"type": "status", "fax_id": fax_id, "status": fax["status"]}
    for uid in (fax["sender_id"], fax["recipient_id"]):
        for ws in presence.sockets(uid):
            try:
                await ws.send_json(msg)
            except Exception:
                pass


async def _flush_local_bridge() -> None:
    """Print faxes queued for the host printer once it's available (boot or hot-replug)."""
    if not (printer.LOCAL_USER and printer.local_available()):
        return
    u = db.get_user_by_name(printer.LOCAL_USER)
    if not u:
        return
    for fax in db.pending_for(u["id"]):
        if presence.online(u["id"]):
            continue  # a live browser session handles delivery for this user
        if printer.print_local(render_escpos(fax)):
            db.mark_delivered(fax["id"])
            await _notify_status(fax["id"])


async def _printer_watch() -> None:
    """Poll the wired printer; flush the queue whenever it (re)appears."""
    while True:
        try:
            await _flush_local_bridge()
        except Exception:
            pass
        await asyncio.sleep(PRINTER_POLL)


# --------------------------------------------------------------------------- #
#  Auth API                                                                     #
# --------------------------------------------------------------------------- #
def _set_session(resp: Response, user_id: int, secure: bool) -> None:
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_token(user_id),
        httponly=True, samesite="lax", secure=secure,   # Secure only over HTTPS
        max_age=60 * 60 * 24 * 30, path="/",
    )


_USERNAME_RE = re.compile(r"[a-z0-9_]{2,24}")


@app.post("/api/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...),
                   display_name: str = Form("")):
    username = username.strip().lower()
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "username must be 2-24 chars: a-z, 0-9, underscore")
    if username.startswith("deleted_"):    # reserved for tombstoned accounts
        raise HTTPException(400, "that callsign prefix is reserved")
    if len(password) < 8:
        raise HTTPException(400, "password too short (min 8)")
    if db.get_user_by_name(username):
        raise HTTPException(409, "callsign already taken")
    ph, salt = auth.hash_password(password)
    user = db.create_user(username, (display_name.strip() or username)[:32], ph, salt)
    db.touch_user(user["id"], _client_ip(request), _client_ua(request))
    resp = JSONResponse({"ok": True, "user": _public(user)})
    _set_session(resp, user["id"], secure=request.url.scheme == "https")
    return resp


@app.post("/api/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_name(username.strip().lower())
    if not user or not auth.verify_password(password, user["pass_hash"], user["salt"]):
        raise HTTPException(401, "bad callsign or password")
    db.touch_user(user["id"], _client_ip(request), _client_ua(request))
    resp = JSONResponse({"ok": True, "user": _public(user)})
    _set_session(resp, user["id"], secure=request.url.scheme == "https")
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


def _public(user: dict) -> dict:
    return {"username": user["username"], "display_name": user["display_name"]}


@app.get("/api/me")
async def me(request: Request):
    user = require_user(request)
    return {
        "user": _public(user),
        "printer_online": presence.online(user["id"]),
        "local_bridge": user["username"] == printer.LOCAL_USER and printer.local_available(),
        "node_online": presence.node_online(user["id"]),
        "has_token": bool(user.get("token_hash")),
    }


@app.post("/api/token/regenerate")
async def regenerate_token(request: Request):
    """Issue a fresh device/API token for the printer agent (shown once). Regenerating
    immediately revokes the previous token."""
    user = require_user(request)
    token = auth.new_device_token()
    db.set_user_token(user["id"], auth.hash_token(token))
    # kick any agent still using the old token so revocation is immediate
    for ws in presence.agent_sockets(user["id"]):
        try:
            await ws.close(code=4401)
        except Exception:
            pass
    return {"ok": True, "username": user["username"], "token": token}


@app.get("/api/users")
async def users(request: Request):
    me_user = require_user(request)
    out = []
    for u in db.list_users():
        if u["id"] == me_user["id"]:
            continue
        out.append({
            "username": u["username"],
            "display_name": u["display_name"],
            "online": presence.online(u["id"])
                      or (u["username"] == printer.LOCAL_USER and printer.local_available()),
        })
    return {"users": out}


# --------------------------------------------------------------------------- #
#  Fax API                                                                      #
# --------------------------------------------------------------------------- #
@app.post("/api/fax")
async def send_fax(request: Request, to: str = Form(""), body: str = Form(""),
                   image: UploadFile | None = File(None)):
    sender = require_user(request)
    if not _rate_ok(sender["id"]):
        raise HTTPException(429, f"slow down — max {FAX_RATE_MAX} faxes per {int(FAX_RATE_WINDOW)}s")
    to = to.strip().lower()
    if not to:
        raise HTTPException(400, "pick a recipient callsign")
    body = body.rstrip()
    if len(body) > 200:
        raise HTTPException(400, "message too long (max 200)")

    img_png = img_w = img_h = None
    if image is not None:
        raw = await image.read()
        if raw:
            if len(raw) > imaging.MAX_UPLOAD:
                raise HTTPException(400, f"image too large (max {imaging.MAX_UPLOAD // (1024*1024)}MB)")
            try:
                img_png, img_w, img_h = imaging.process_upload(raw)
            except Exception:
                raise HTTPException(400, "could not read that image file")

    if not body and img_png is None:
        raise HTTPException(400, "empty message (add text or an image)")

    recipient = db.get_user_by_name(to)
    if not recipient or recipient.get("deleted_at"):
        raise HTTPException(404, "no such callsign")
    if recipient["id"] == sender["id"]:
        raise HTTPException(400, "you can't fax yourself — pick a friend's callsign")
    fax = db.create_fax(sender["id"], recipient["id"], body, img_png, img_w, img_h)
    delivered = await deliver(fax)
    return {"ok": True, "fax_id": fax["id"], "delivered": delivered, "has_image": img_png is not None}


@app.get("/api/fax/{fax_id}/image")
async def fax_image(fax_id: int, request: Request):
    user = require_user(request)
    fax = db.get_fax(fax_id)
    if not fax or not fax.get("image"):
        raise HTTPException(404, "no image")
    if user["id"] not in (fax["sender_id"], fax["recipient_id"]):
        raise HTTPException(403, "not your fax")
    return Response(content=fax["image"], media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/inbox")
async def get_inbox(request: Request):
    user = require_user(request)
    return {"faxes": db.inbox(user["id"])}


@app.get("/api/outbox")
async def get_outbox(request: Request):
    user = require_user(request)
    return {"faxes": db.outbox(user["id"])}


@app.post("/api/inbox/clear")
async def clear_inbox_ep(request: Request):
    user = require_user(request)
    return {"ok": True, "cleared": db.clear_inbox(user["id"])}


@app.post("/api/outbox/clear")
async def clear_outbox_ep(request: Request):
    user = require_user(request)
    return {"ok": True, "cleared": db.clear_outbox(user["id"])}


@app.post("/api/test-print")
async def test_print(request: Request):
    """Print a test page on the user's own printer node (agent) or host local bridge."""
    user = require_user(request)
    now = time.time()
    escpos = printer.build_receipt(
        user["display_name"], user["username"],
        "*** TEST PRINT ***\nyour FaxxMe printer node is working.", now)
    payload = {"type": "fax", "id": 0, "from": "FAXXME", "from_username": "system",
               "body": "test print", "created_at": now,
               "escpos_b64": base64.b64encode(escpos).decode()}
    delivered = False
    for ws in presence.agent_sockets(user["id"]):
        try:
            await ws.send_json(payload)
            delivered = True
        except Exception:
            pass
    if not delivered and user["username"] == printer.LOCAL_USER and printer.local_available():
        delivered = printer.print_local(escpos)
    return {"ok": True, "delivered": delivered}


# --------------------------------------------------------------------------- #
#  Admin API (single hashed password in FAXXME_ADMIN_PASSWORD_HASH)             #
# --------------------------------------------------------------------------- #
def _set_admin_session(resp: Response, secure: bool) -> None:
    resp.set_cookie(
        auth.ADMIN_COOKIE, auth.make_admin_session(),
        httponly=True, samesite="lax", secure=secure,
        max_age=60 * 60 * 12, path="/",
    )


@app.post("/api/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    if not admin_enabled():
        raise HTTPException(403, "admin panel is disabled (set FAXXME_ADMIN_PASSWORD_HASH)")
    if not auth.verify_admin_password(password, ADMIN_PASSWORD_HASH):
        raise HTTPException(401, "wrong admin password")
    resp = JSONResponse({"ok": True})
    _set_admin_session(resp, secure=request.url.scheme == "https")
    return resp


@app.post("/api/admin/logout")
async def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.ADMIN_COOKIE, path="/")
    return resp


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    require_admin(request)
    stats = db.admin_stats()
    stats["online"] = presence.online_count()
    return stats


@app.get("/api/admin/users")
async def admin_users(request: Request, limit: int = 20, offset: int = 0):
    require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    out = []
    for u in db.admin_list_users(limit, offset):
        out.append({
            **u,
            "online": presence.online(u["id"]),
            "node_online": presence.node_online(u["id"]),
        })
    return {"users": out, "total": db.admin_count_users()}


@app.post("/api/admin/users/{user_id}/delete")
async def admin_delete_user(user_id: int, request: Request):
    """Tombstone (anonymize) a user: their faxes survive for the other party, the account can no
    longer log in, its device token is revoked, and the callsign is freed."""
    require_admin(request)
    target = db.get_user(user_id)
    if not target or target.get("deleted_at"):
        raise HTTPException(404, "no such user")
    # drop any live browser/agent sockets this user has open
    for ws in presence.sockets(user_id):
        try:
            await ws.close(code=4403)
        except Exception:
            pass
    db.tombstone_user(user_id)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/revoke-token")
async def admin_revoke_token(user_id: int, request: Request):
    require_admin(request)
    if not db.get_user(user_id):
        raise HTTPException(404, "no such user")
    db.clear_user_token(user_id)
    for ws in presence.agent_sockets(user_id):   # kick the agent so revocation is immediate
        try:
            await ws.close(code=4401)
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/admin/faxes")
async def admin_faxes(request: Request, q: str = "", limit: int = 20, offset: int = 0):
    require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    q = q.strip()
    return {"faxes": db.admin_all_faxes(q, limit, offset), "total": db.admin_count_faxes(q)}


@app.post("/api/admin/faxes/{fax_id}/delete")
async def admin_delete_fax(fax_id: int, request: Request):
    require_admin(request)
    if not db.admin_delete_fax(fax_id):
        raise HTTPException(404, "no such fax")
    return {"ok": True}


@app.get("/api/admin/faxes/{fax_id}/image")
async def admin_fax_image(fax_id: int, request: Request):
    require_admin(request)
    fax = db.get_fax(fax_id)
    if not fax or not fax.get("image"):
        raise HTTPException(404, "no image")
    return Response(content=fax["image"], media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


# --------------------------------------------------------------------------- #
#  WebSocket: presence + live delivery                                          #
# --------------------------------------------------------------------------- #
def _ws_authenticate(ws: WebSocket) -> dict | None:
    """A WebSocket may authenticate as a browser (session cookie) or as a headless printer
    agent (device token via `Authorization: Bearer <token>` + `X-Faxxme-User: <callsign>`)."""
    authz = ws.headers.get("authorization", "")
    uname = ws.headers.get("x-faxxme-user")
    if authz.lower().startswith("bearer ") and uname:
        token = authz.split(" ", 1)[1].strip()
        u = db.get_user_by_token_hash(uname.strip().lower(), auth.hash_token(token))
        if u:
            return u
    uid = auth.read_token(ws.cookies.get(auth.COOKIE_NAME))
    user = db.get_user(uid) if uid else None
    return user if user and not user.get("deleted_at") else None


async def _broadcast_node(user_id: int, online: bool) -> None:
    """Tell a user's browser tabs whether their printer node (agent) is connected."""
    msg = {"type": "node", "online": online}
    for ws in presence.browser_sockets(user_id):
        try:
            await ws.send_json(msg)
        except Exception:
            pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    user = _ws_authenticate(ws)
    if not user:
        await ws.close(code=4401)
        return
    db.touch_user(user["id"], _client_ip(ws), _client_ua(ws))   # IP + UA for browser tabs and Pi agents
    is_agent = ws.headers.get("authorization", "").lower().startswith("bearer ")
    await ws.accept()
    await presence.add(user["id"], ws, is_agent=is_agent)
    if is_agent:
        await _broadcast_node(user["id"], True)
    try:
        await ws.send_json({"type": "hello", "user": _public(user)})
        # flush queued faxes now that a printer is online
        for fax in db.pending_for(user["id"]):
            await ws.send_json(fax_payload(fax))
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ack":
                fid = msg.get("fax_id")
                fax = db.get_fax(fid) if fid else None
                if fax and fax["recipient_id"] == user["id"] and fax["status"] != "delivered":
                    db.mark_delivered(fid)
            elif msg.get("type") == "ping":
                db.touch_seen(user["id"])           # heartbeat -> keep last_seen fresh while online
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        db.touch_seen(user["id"])                   # record when they dropped off
        await presence.remove(user["id"], ws)
        if is_agent and not presence.node_online(user["id"]):
            await _broadcast_node(user["id"], False)


# --------------------------------------------------------------------------- #
#  Static frontend                                                              #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe for load balancers, Docker, systemd, uptime checks."""
    return {"status": "ok", "printer_bridge": printer.local_available()}


def _asset_version() -> int:
    ver = 0
    for f in ("app.js", "style.css", "admin.js"):
        try:
            ver = max(ver, int(os.path.getmtime(os.path.join(STATIC_DIR, f))))
        except OSError:
            pass
    return ver


def _serve_page(filename: str) -> HTMLResponse:
    with open(os.path.join(STATIC_DIR, filename), encoding="utf-8") as fh:
        html = fh.read()
    # cache-bust JS/CSS by their mtime so a deploy takes effect without a CDN purge
    ver = _asset_version()
    for asset in ("app.js", "admin.js", "style.css"):
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={ver}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return _serve_page("index.html")


@app.get("/admin")
async def admin_page():
    # The page loads for anyone; its data calls hit /api/admin/* which enforce admin.
    return _serve_page("admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
