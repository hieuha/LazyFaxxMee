"""FaxxMe — FastAPI server: auth, fax routing, presence, and live delivery over WebSocket."""
import asyncio
import base64
import os
from contextlib import asynccontextmanager

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, Request, Response,
                     HTTPException, Form, File, UploadFile)
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, imaging, printer

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
PRINTER_POLL = float(os.environ.get("FAXXME_PRINTER_POLL", "4"))  # seconds between printer checks


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
        self._lock = asyncio.Lock()

    async def add(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._sockets.setdefault(user_id, set()).add(ws)

    async def remove(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            socks = self._sockets.get(user_id)
            if socks:
                socks.discard(ws)
                if not socks:
                    self._sockets.pop(user_id, None)

    def online(self, user_id: int) -> bool:
        return bool(self._sockets.get(user_id))

    def sockets(self, user_id: int) -> list[WebSocket]:
        return list(self._sockets.get(user_id, ()))


presence = Presence()


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #
def current_user(request: Request) -> dict | None:
    uid = auth.read_token(request.cookies.get(auth.COOKIE_NAME))
    return db.get_user(uid) if uid else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


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
def _set_session(resp: Response, user_id: int) -> None:
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_token(user_id),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path="/",
    )


@app.post("/api/register")
async def register(username: str = Form(...), password: str = Form(...),
                   display_name: str = Form("")):
    username = username.strip().lower()
    if not (2 <= len(username) <= 24) or not username.replace("_", "").isalnum():
        raise HTTPException(400, "username must be 2-24 chars: letters, digits, underscore")
    if len(password) < 4:
        raise HTTPException(400, "password too short (min 4)")
    if db.get_user_by_name(username):
        raise HTTPException(409, "callsign already taken")
    ph, salt = auth.hash_password(password)
    user = db.create_user(username, (display_name.strip() or username)[:32], ph, salt)
    resp = JSONResponse({"ok": True, "user": _public(user)})
    _set_session(resp, user["id"])
    return resp


@app.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_name(username.strip().lower())
    if not user or not auth.verify_password(password, user["pass_hash"], user["salt"]):
        raise HTTPException(401, "bad callsign or password")
    resp = JSONResponse({"ok": True, "user": _public(user)})
    _set_session(resp, user["id"])
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
    }


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
    if not recipient:
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


# --------------------------------------------------------------------------- #
#  WebSocket: presence + live delivery                                          #
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    uid = auth.read_token(ws.cookies.get(auth.COOKIE_NAME))
    user = db.get_user(uid) if uid else None
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    await presence.add(user["id"], ws)
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
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await presence.remove(user["id"], ws)


# --------------------------------------------------------------------------- #
#  Static frontend                                                              #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe for load balancers, Docker, systemd, uptime checks."""
    return {"status": "ok", "printer_bridge": printer.local_available()}


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
