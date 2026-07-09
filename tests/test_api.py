"""End-to-end-ish tests for FaxxMe: auth, fax queueing, delivery, local bridge, and WS flush."""
import base64
import hashlib
import os
import tempfile

os.environ["FAXXME_DB"] = tempfile.mktemp(suffix=".db")
os.environ["FAXXME_SECRET"] = tempfile.mktemp(suffix=".secret")
_printfile = tempfile.mktemp(suffix=".prn")
os.environ["FAXXME_PRINTER_DEV"] = _printfile
os.environ["FAXXME_LOCAL_USER"] = "bob"  # bob has the wired-in printer
os.environ["FAXXME_FAX_RATE_MAX"] = "0"  # rate limit off by default; one test enables it
os.environ["FAXXME_WEBHOOK_RATE_MAX"] = "0"  # inbound rate limit off by default; one test enables it
os.environ["FAXXME_ADMIN_PASSWORD_HASH"] = hashlib.sha256(b"s3cret-admin").hexdigest()  # /admin gate

from fastapi.testclient import TestClient  # noqa: E402
from faxxme import app as appmod, printer  # noqa: E402

client = TestClient(appmod.app)


def _reg(u, p="pw123456", name=None):
    return client.post("/api/register", data={"username": u, "password": p, "display_name": name or u})


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "printer_bridge" in r.json()


def test_register_login_me():
    r = _reg("alice", name="Alice A")
    assert r.status_code == 200, r.text
    r = client.get("/api/me")
    assert r.json()["user"]["username"] == "alice"
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert r.status_code == 200


def test_logout_invalidates_session_server_side():
    """Logout bumps the user's session_epoch, so a captured cookie is rejected even if replayed."""
    client.post("/api/logout")
    _reg("logoutx")
    stolen = client.cookies.get("fx_session")   # the raw signed session cookie
    assert stolen and client.get("/api/me").status_code == 200
    client.post("/api/logout")                  # invalidates it server-side
    client.cookies.clear()
    client.cookies.set("fx_session", stolen)     # replay the old cookie
    assert client.get("/api/me").status_code == 401
    client.cookies.clear()


def test_register_validation():
    assert client.post("/api/register", data={"username": "x", "password": "pw123456"}).status_code == 400
    assert client.post("/api/register", data={"username": "okname", "password": "1"}).status_code == 400
    _reg("dupe")
    assert client.post("/api/register", data={"username": "dupe", "password": "pw123456"}).status_code == 409


def test_fax_queue_then_ws_flush():
    """alice faxes carol (offline) -> queued. carol connects WS -> receives it -> acks -> delivered."""
    _reg("carol")
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "carol", "body": "meet at dawn"})
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] is False  # carol offline, no local bridge for her

    # carol logs in and opens her printer link (websocket)
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw123456"})
    with client.websocket_connect("/ws") as wsconn:
        hello = wsconn.receive_json()
        assert hello["type"] == "hello"
        fax = wsconn.receive_json()
        assert fax["type"] == "fax"
        assert fax["body"] == "meet at dawn"
        assert fax["from_username"] == "alice"
        raw = base64.b64decode(fax["escpos_b64"])
        assert b"FAXXME" in raw and b"meet at dawn" in raw
        assert b"incoming transmission" not in raw  # subtitle line removed
        wsconn.send_json({"type": "ack", "fax_id": fax["id"]})
    # after ack it must be delivered
    inb = client.get("/api/inbox").json()["faxes"]
    assert inb[0]["status"] == "delivered"


def test_local_bridge_prints_immediately():
    """A fax to bob (the local-bridge user) prints to the device file right away, no browser."""
    _reg("bob")
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    open(_printfile, "wb").close()   # stand in for the real /dev/usb/lp0 node
    assert printer.local_available()  # device file is writable in tmp
    r = client.post("/api/fax", data={"to": "bob", "body": "wired hello"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    with open(_printfile, "rb") as fh:
        data = fh.read()
    assert b"FAXXME" in data and b"wired hello" in data


def test_local_bridge_flushes_on_printer_reconnect():
    """Fax queued while the printer is unplugged auto-prints when it comes back."""
    import asyncio
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    # printer "unplugged": device node gone -> fax queues
    if os.path.exists(_printfile):
        os.remove(_printfile)
    assert not printer.local_available()
    r = client.post("/api/fax", data={"to": "bob", "body": "queued while unplugged"})
    assert r.json()["delivered"] is False
    # printer "replugged": watcher flush prints + marks delivered
    open(_printfile, "wb").close()
    assert printer.local_available()
    asyncio.run(appmod._flush_local_bridge())
    assert b"queued while unplugged" in open(_printfile, "rb").read()
    client.post("/api/logout")
    client.post("/api/login", data={"username": "bob", "password": "pw123456"})
    inb = client.get("/api/inbox").json()["faxes"]
    assert inb[0]["body"] == "queued while unplugged" and inb[0]["status"] == "delivered"


def _png(w=64, h=40):
    from PIL import Image
    import io
    im = Image.new("L", (w, h))
    for y in range(h):
        for x in range(w):
            im.putpixel((x, y), (x * 4) % 256)  # gradient -> exercises dithering
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def test_fax_with_image_dithers_and_prints():
    """alice faxes bob (local bridge) with an image -> dithered, raster printed, retrievable."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "bob", "body": "here's a pic"},
                    files={"image": ("g.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["has_image"] is True
    assert r.json()["delivered"] is True
    with open(_printfile, "rb") as fh:
        data = fh.read()
    assert b"\x1dv0" in data          # GS v 0 raster command reached the printer
    assert b"here's a pic" in data
    # the dithered PNG is retrievable by a party to the fax
    fid = client.get("/api/outbox").json()["faxes"][0]["id"]
    ir = client.get(f"/api/fax/{fid}/image")
    assert ir.status_code == 200 and ir.headers["content-type"] == "image/png"
    assert ir.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_only_no_text_ok():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "carol"},
                    files={"image": ("x.png", _png(20, 20), "image/png")})
    assert r.status_code == 200  # image alone (no body) is allowed


def test_image_access_control():
    _reg("trinity")
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "bob"},
                    files={"image": ("x.png", _png(16, 16), "image/png")})
    fid = client.get("/api/outbox").json()["faxes"][0]["id"]
    # trinity is neither sender nor recipient -> forbidden
    client.post("/api/logout")
    login = client.post("/api/login", data={"username": "trinity", "password": "pw123456"})
    assert login.status_code == 200
    assert client.get(f"/api/fax/{fid}/image").status_code == 403


def test_send_errors():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.post("/api/fax", data={"to": "ghost", "body": "hi"}).status_code == 404
    assert client.post("/api/fax", data={"to": "bob", "body": "   "}).status_code == 400


def test_empty_recipient_clean_400():
    """Empty 'to' must be a friendly string 400, not a 422 validation array (the [object Object] bug)."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "", "body": "hi"})
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_ws_rejects_anonymous():
    client.post("/api/logout")
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as wsconn:
            wsconn.receive_json()


def test_body_length_limit():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.post("/api/fax", data={"to": "carol", "body": "x" * 200}).status_code == 200
    assert client.post("/api/fax", data={"to": "carol", "body": "y" * 201}).status_code == 400


def test_cannot_fax_yourself():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    r = client.post("/api/fax", data={"to": "alice", "body": "note to self"})
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"]


def test_clear_is_per_side():
    """Clearing inbox hides it only from the recipient; the sender's outbox keeps it."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    client.post("/api/fax", data={"to": "carol", "body": "PERSIST-ME"})
    # carol clears her inbox
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw123456"})
    assert any(f["body"] == "PERSIST-ME" for f in client.get("/api/inbox").json()["faxes"])
    cleared = client.post("/api/inbox/clear").json()
    assert cleared["ok"] and cleared["cleared"] >= 1
    assert client.get("/api/inbox").json()["faxes"] == []
    # alice still has it in her outbox
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert any(f["body"] == "PERSIST-ME" for f in client.get("/api/outbox").json()["faxes"])
    client.post("/api/outbox/clear")
    assert client.get("/api/outbox").json()["faxes"] == []


def test_auto_prune_keeps_50():
    from faxxme import db as dbmod
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    for i in range(55):
        client.post("/api/fax", data={"to": "carol", "body": f"burst-{i}"})
    carol = dbmod.get_user_by_name("carol")
    visible = dbmod._c().execute(
        "SELECT COUNT(*) FROM faxes WHERE recipient_id=? AND recipient_deleted=0",
        (carol["id"],),
    ).fetchone()[0]
    assert visible == 50  # older ones auto-pruned to the cap
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw123456"})
    faxes = client.get("/api/inbox").json()["faxes"]
    assert len(faxes) == 50 and faxes[0]["body"] == "burst-54"  # newest first


def test_device_token_auth_and_revocation():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.get("/api/me").json()["has_token"] is False
    tok = client.post("/api/token/regenerate").json()["token"]
    assert tok and client.get("/api/me").json()["has_token"] is True

    # agent connects to /ws with the token (no cookie)
    client.post("/api/logout")
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as ws:
        assert ws.receive_json()["type"] == "hello"

    # regenerate -> the old token is revoked, a new one works
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    tok2 = client.post("/api/token/regenerate").json()["token"]
    assert tok2 != tok
    client.post("/api/logout")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=hdrs) as ws:  # old token
            ws.receive_json()
    with client.websocket_connect("/ws", headers={"Authorization": f"Bearer {tok2}", "X-Faxxme-User": "alice"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_bad_device_token_rejected():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    client.post("/api/logout")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer nope", "X-Faxxme-User": "alice"}) as ws:
            ws.receive_json()


def test_node_online_indicator():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    tok = client.post("/api/token/regenerate").json()["token"]
    assert client.get("/api/me").json()["node_online"] is False
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as agent:
        assert agent.receive_json()["type"] == "hello"
        assert client.get("/api/me").json()["node_online"] is True     # agent connected
    assert client.get("/api/me").json()["node_online"] is False         # agent gone


def test_test_print_routes_to_agent():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    tok = client.post("/api/token/regenerate").json()["token"]
    assert client.post("/api/test-print").json()["delivered"] is False   # no agent, not the bridge user
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as agent:
        assert agent.receive_json()["type"] == "hello"
        assert client.post("/api/test-print").json()["delivered"] is True
        fax = agent.receive_json()
        assert fax["type"] == "fax"
        assert b"TEST PRINT" in base64.b64decode(fax["escpos_b64"])


def test_unicode_body_renders_as_raster():
    from faxxme import printer
    vn = printer.build_receipt("Bob", "bob", "Chào bạn — tiếng Việt!", 1700000000.0)
    assert b"\x1dv0" in vn                       # Vietnamese line -> GS v 0 raster
    a = printer.build_receipt("Bob", "bob", "hello world", 1700000000.0)
    assert b"hello world" in a and b"\x1dv0" not in a   # ASCII stays native text


def test_fax_rate_limit():
    from faxxme import app as A
    A.FAX_RATE_MAX, A.FAX_RATE_WINDOW = 3, 60
    A._fax_hits.clear()
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    codes = [client.post("/api/fax", data={"to": "bob", "body": f"n{i}"}).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200]        # first 3 allowed
    assert codes[3] == 429 and codes[4] == 429  # then rate-limited
    A.FAX_RATE_MAX = 0                           # restore (off) for any other tests
    A._fax_hits.clear()


# ---- admin panel (single hashed password in the env; no user account) ----

ADMIN_PW = "s3cret-admin"


def _admin_login(pw=ADMIN_PW):
    return client.post("/api/admin/login", data={"password": pw})


def _admin_logout():
    return client.post("/api/admin/logout")


def _admin_users(**params):
    params.setdefault("limit", 200)
    return client.get("/api/admin/users", params=params).json()["users"]


def test_admin_login_and_access_control():
    _admin_logout()                                          # ensure no admin cookie
    assert client.get("/api/admin/stats").status_code == 401
    # a logged-in normal user still has no admin access — admin is not a user account
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.get("/api/admin/stats").status_code == 401
    assert "is_admin" not in client.get("/api/me").json()   # /api/me no longer carries admin
    assert _admin_login("nope").status_code == 401          # wrong password
    assert _admin_login().status_code == 200                # correct password -> cookie set
    assert client.get("/api/admin/stats").status_code == 200
    assert client.get("/admin").status_code == 200          # page HTML serves


def test_admin_pagination_stats_and_management():
    _admin_login()
    s = client.get("/api/admin/stats").json()
    assert "users" in s and "faxes" in s and "online" in s

    # pagination: limit honored, a total is returned, offset moves the window
    d = client.get("/api/admin/users", params={"limit": 1, "offset": 0}).json()
    assert d["total"] >= 1 and len(d["users"]) <= 1
    if d["total"] > 1:
        first = d["users"][0]["id"]
        second = client.get("/api/admin/users", params={"limit": 1, "offset": 1}).json()["users"][0]["id"]
        assert first != second

    # a throwaway user + fax the admin can see, filter, and delete
    _reg("victim")
    client.post("/api/login", data={"username": "victim", "password": "pw123456"})
    client.post("/api/fax", data={"to": "alice", "body": "trace this"})

    hit = client.get("/api/admin/faxes", params={"q": "trace this"}).json()
    assert hit["total"] >= 1 and all("trace" in f["body"] for f in hit["faxes"])
    fid = hit["faxes"][0]["id"]
    assert client.post(f"/api/admin/faxes/{fid}/delete").status_code == 200
    assert client.post(f"/api/admin/faxes/{fid}/delete").status_code == 404  # already gone

    vid = next(u for u in _admin_users() if u["username"] == "victim")["id"]
    assert client.post(f"/api/admin/users/{vid}/delete").status_code == 200
    assert "victim" not in [u["username"] for u in _admin_users()]
    assert client.post("/api/login", data={"username": "victim", "password": "pw123456"}).status_code == 401


def test_admin_revoke_token():
    _admin_login()
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    client.post("/api/token/regenerate")
    assert client.get("/api/me").json()["has_token"] is True
    aid = next(u for u in _admin_users() if u["username"] == "alice")["id"]
    assert client.post(f"/api/admin/users/{aid}/revoke-token").status_code == 200
    assert next(u for u in _admin_users() if u["username"] == "alice")["has_token"] == 0


def test_admin_fax_image_endpoint():
    # a user sends one fax WITH an image and one text-only
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    client.post("/api/fax", data={"to": "bob", "body": "pic for admin"},
                files={"image": ("g.png", _png(), "image/png")})
    img_fid = client.get("/api/outbox").json()["faxes"][0]["id"]
    client.post("/api/fax", data={"to": "bob", "body": "text only, no image"})
    txt_fid = client.get("/api/outbox").json()["faxes"][0]["id"]

    _admin_login()
    ir = client.get(f"/api/admin/faxes/{img_fid}/image")
    assert ir.status_code == 200 and ir.headers["content-type"] == "image/png"
    assert ir.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/admin/faxes/{txt_fid}/image").status_code == 404  # no image on that fax

    _admin_logout()
    assert client.get(f"/api/admin/faxes/{img_fid}/image").status_code == 401  # gated by admin cookie


def test_admin_delete_user_tombstones_and_keeps_faxes():
    _admin_login()
    _reg("ghost")
    client.post("/api/login", data={"username": "ghost", "password": "pw123456"})
    client.post("/api/fax", data={"to": "alice", "body": "survive me"})
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert any(f["body"] == "survive me" for f in client.get("/api/inbox").json()["faxes"])

    # admin "deletes" ghost -> tombstone
    gid = next(u for u in _admin_users() if u["username"] == "ghost")["id"]
    assert client.post(f"/api/admin/users/{gid}/delete").status_code == 200
    # ghost vanishes from the roster and can no longer log in
    assert "ghost" not in [u["username"] for u in _admin_users()]
    assert client.post("/api/login", data={"username": "ghost", "password": "pw123456"}).status_code == 401

    # …but the fax survives, now attributed to a deleted_* callsign, and alice still holds it
    fx = client.get("/api/admin/faxes", params={"q": "survive me"}).json()["faxes"]
    assert fx and fx[0]["sender_name"].startswith("deleted_")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert any(f["body"] == "survive me" for f in client.get("/api/inbox").json()["faxes"])
    # the tombstoned account isn't offered as a recipient, and its old callsign is free again
    assert not any(u["username"].startswith("deleted_") for u in client.get("/api/users").json()["users"])
    assert client.post("/api/register", data={"username": "ghost", "password": "pw123456"}).status_code == 200


def test_register_rejects_reserved_prefix():
    assert client.post("/api/register",
                       data={"username": "deleted_9", "password": "pw123456"}).status_code == 400


# ---- admin: thorough edge cases + end-to-end ----

def test_admin_cookie_tampered_rejected():
    _admin_logout()
    client.cookies.set("fx_admin", "not-a-real-signature")
    assert client.get("/api/admin/stats").status_code == 401
    client.cookies.delete("fx_admin")


def test_admin_login_disabled_returns_403():
    saved = appmod.ADMIN_PASSWORD_HASH
    appmod.ADMIN_PASSWORD_HASH = ""            # simulate FAXXME_ADMIN_PASSWORD_HASH unset
    try:
        _admin_logout()
        assert client.post("/api/admin/login", data={"password": "anything"}).status_code == 403
    finally:
        appmod.ADMIN_PASSWORD_HASH = saved


def test_admin_pagination_clamps_and_bounds():
    _admin_login()
    assert len(client.get("/api/admin/users", params={"limit": 9999}).json()["users"]) <= 200  # clamp hi
    assert len(client.get("/api/admin/users", params={"limit": 0}).json()["users"]) <= 1        # clamp lo
    beyond = client.get("/api/admin/users", params={"limit": 20, "offset": 100000}).json()
    assert beyond["users"] == [] and beyond["total"] >= 1                                       # offset past end
    assert len(client.get("/api/admin/faxes", params={"limit": 9999}).json()["faxes"]) <= 200


def test_admin_faxes_search_variants():
    _admin_login()
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    client.post("/api/fax", data={"to": "bob", "body": "ZQXmarker payload"})
    assert client.get("/api/admin/faxes", params={"q": "ZQXmarker"}).json()["total"] >= 1        # by body
    assert client.get("/api/admin/faxes", params={"q": "alice"}).json()["total"] >= 1            # by callsign
    assert client.get("/api/admin/faxes", params={"q": "no-such-marker-xyz"}).json()["total"] == 0  # no match


def test_admin_nonexistent_targets_404():
    _admin_login()
    assert client.get("/api/admin/faxes/999999/image").status_code == 404
    assert client.post("/api/admin/faxes/999999/delete").status_code == 404
    assert client.post("/api/admin/users/999999/delete").status_code == 404
    assert client.post("/api/admin/users/999999/revoke-token").status_code == 404


def test_tombstone_multiparty_keeps_all_copies():
    for u in ("hub", "peera", "peerb"):
        _reg(u)
    client.post("/api/login", data={"username": "hub", "password": "pw123456"})
    client.post("/api/fax", data={"to": "peera", "body": "hub-to-A"})
    client.post("/api/login", data={"username": "peerb", "password": "pw123456"})
    client.post("/api/fax", data={"to": "hub", "body": "B-to-hub"})

    _admin_login()
    hid = next(u for u in _admin_users() if u["username"] == "hub")["id"]
    assert client.post(f"/api/admin/users/{hid}/delete").status_code == 200
    bodies = [f["body"] for f in client.get("/api/admin/faxes", params={"limit": 200}).json()["faxes"]]
    assert "hub-to-A" in bodies and "B-to-hub" in bodies                 # both survive
    client.post("/api/login", data={"username": "peera", "password": "pw123456"})
    assert any(f["body"] == "hub-to-A" for f in client.get("/api/inbox").json()["faxes"])
    client.post("/api/login", data={"username": "peerb", "password": "pw123456"})
    assert any(f["body"] == "B-to-hub" for f in client.get("/api/outbox").json()["faxes"])


def test_tombstone_revokes_device_token():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    _reg("agz")
    client.post("/api/login", data={"username": "agz", "password": "pw123456"})
    tok = client.post("/api/token/regenerate").json()["token"]
    client.post("/api/logout")
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "agz"}
    with client.websocket_connect("/ws", headers=hdrs) as ws:   # token works before tombstone
        assert ws.receive_json()["type"] == "hello"
    _admin_login()
    uid = next(u for u in _admin_users() if u["username"] == "agz")["id"]
    client.post(f"/api/admin/users/{uid}/delete")
    with pytest.raises(WebSocketDisconnect):                     # old token can't reconnect
        with client.websocket_connect("/ws", headers=hdrs) as ws:
            ws.receive_json()


def test_tombstone_clears_webhook_secret():
    from faxxme import app as A
    key = _secret_for("whdel")                                   # register + mint a webhook secret
    h = {"Authorization": f"Bearer {key}"}
    assert client.post("/api/fax/inbound", data={"body": "pre"}, headers=h).status_code == 200
    _admin_login()
    uid = next(u for u in _admin_users() if u["username"] == "whdel")["id"]
    client.post(f"/api/admin/users/{uid}/delete")
    assert A.db.get_user(uid)["webhook_secret"] is None          # secret scrubbed from the row
    assert client.post("/api/fax/inbound", data={"body": "post"},
                       headers=h).status_code == 401             # webhook now rejects the old key
    import pytest
    from starlette.websockets import WebSocketDisconnect
    _reg("liveusr")
    client.post("/api/login", data={"username": "liveusr", "password": "pw123456"})
    assert client.get("/api/me").status_code == 200
    _admin_login()                                              # fx_admin set; fx_session still = liveusr
    uid = next(u for u in _admin_users() if u["username"] == "liveusr")["id"]
    assert client.post(f"/api/admin/users/{uid}/delete").status_code == 200
    # the still-present session cookie no longer authenticates anywhere
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/fax", data={"to": "alice", "body": "nope"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_cannot_fax_tombstoned_callsign():
    _reg("tombx")
    client.post("/api/login", data={"username": "tombx", "password": "pw123456"})
    _admin_login()
    uid = next(u for u in _admin_users() if u["username"] == "tombx")["id"]
    client.post(f"/api/admin/users/{uid}/delete")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.post("/api/fax", data={"to": f"deleted_{uid}", "body": "x"}).status_code == 404
    assert client.post("/api/fax", data={"to": "tombx", "body": "x"}).status_code == 404   # freed, no user


def test_reregister_freed_callsign_is_new_identity():
    _reg("recyc")
    client.post("/api/login", data={"username": "recyc", "password": "pw123456"})
    client.post("/api/fax", data={"to": "alice", "body": "old-recyc-msg"})
    _admin_login()
    old_id = next(u for u in _admin_users() if u["username"] == "recyc")["id"]
    client.post(f"/api/admin/users/{old_id}/delete")
    assert client.post("/api/register", data={"username": "recyc", "password": "pw123456"}).status_code == 200
    new_id = next(u for u in _admin_users() if u["username"] == "recyc")["id"]
    assert new_id != old_id                                     # a brand-new account
    fx = client.get("/api/admin/faxes", params={"q": "old-recyc-msg"}).json()["faxes"]
    assert fx and fx[0]["sender_name"] == f"deleted_{old_id}"    # old fax stays with the tombstone


def test_register_reserved_prefix_variants():
    assert client.post("/api/register", data={"username": "deleted_1", "password": "pw123456"}).status_code == 400
    assert client.post("/api/register", data={"username": "deleted_abc", "password": "pw123456"}).status_code == 400
    assert client.post("/api/register", data={"username": "notdeleted", "password": "pw123456"}).status_code == 200


def test_admin_records_client_ip_and_ua():
    # login behind Cloudflare -> the real client IP (CF header) + User-Agent are recorded
    client.post("/api/login", data={"username": "alice", "password": "pw123456"},
                headers={"CF-Connecting-IP": "203.0.113.7", "User-Agent": "TestBrowser/9"})
    _admin_login()
    alice = next(u for u in _admin_users() if u["username"] == "alice")
    assert alice["last_ip"] == "203.0.113.7" and alice["last_seen"] and alice["last_ua"] == "TestBrowser/9"
    # X-Forwarded-For (first hop) is used when there's no CF header
    client.post("/api/login", data={"username": "alice", "password": "pw123456"},
                headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.1"})
    _admin_login()
    alice = next(u for u in _admin_users() if u["username"] == "alice")
    assert alice["last_ip"] == "198.51.100.9"


def test_ws_connect_records_ip_and_ua():
    _reg("ipagent")
    client.post("/api/login", data={"username": "ipagent", "password": "pw123456"})
    tok = client.post("/api/token/regenerate").json()["token"]
    client.post("/api/logout")
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "ipagent",
            "CF-Connecting-IP": "192.0.2.55", "User-Agent": "FaxxMe-Agent/0.1"}
    with client.websocket_connect("/ws", headers=hdrs) as ws:
        assert ws.receive_json()["type"] == "hello"
    _admin_login()
    u = next(x for x in _admin_users() if x["username"] == "ipagent")
    assert u["last_ip"] == "192.0.2.55" and u["last_ua"] == "FaxxMe-Agent/0.1"


def test_ws_ping_pong_updates_last_seen():
    _reg("hbusr")
    client.post("/api/login", data={"username": "hbusr", "password": "pw123456"})
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"     # heartbeat answered, last_seen bumped
    _admin_login()
    assert next(x for x in _admin_users() if x["username"] == "hbusr")["last_seen"]


# ---- webhook inbound faxes (secret-key auth, no account for the end sender) ----

def _secret_for(username):
    """Register (idempotent) a user and mint a fresh webhook secret for them, logged out after."""
    _reg(username)  # 409 if already exists — fine
    client.post("/api/logout")
    client.post("/api/login", data={"username": username, "password": "pw123456"})
    key = client.post("/api/webhook/regenerate").json()["secret"]
    client.post("/api/logout")
    return key


def test_webhook_secret_lifecycle_and_me_field():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    assert client.get("/api/me").json()["webhook_secret"] is None
    key = client.post("/api/webhook/regenerate").json()["secret"]
    assert key.startswith("fxwh_")
    # the secret is retrievable (masked in the UI, plaintext here) — not shown just once
    assert client.get("/api/me").json()["webhook_secret"] == key
    # revoke turns it off AND stops inbound faxes
    client.post("/api/webhook/revoke")
    assert client.get("/api/me").json()["webhook_secret"] is None
    client.post("/api/logout")
    assert client.post("/api/fax/inbound", data={"body": "after revoke"},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_webhook_callsign_is_reserved():
    assert client.post("/api/register",
                       data={"username": "webhook", "password": "pw123456"}).status_code == 400


def test_inbound_requires_valid_secret():
    assert client.post("/api/fax/inbound", data={"body": "hi"}).status_code == 401       # no header
    assert client.post("/api/fax/inbound", data={"body": "hi"},
                       headers={"Authorization": "Bearer fxwh_bogus"}).status_code == 401


def test_inbound_delivers_to_local_bridge_with_attribution():
    """bob owns the wired printer; a reader's comment prints with '@webhook' sender + the message,
    and lands in bob's inbox as sent by the reserved 'webhook' account (not a real user)."""
    if os.path.exists(_printfile):
        os.remove(_printfile)
    open(_printfile, "wb").close()
    assert printer.local_available()
    key = _secret_for("bob")
    r = client.post("/api/fax/inbound",
                    data={"body": "loved this post!", "name": "Rita",
                          "post": "On Faxes", "url": "https://blog.example/on-faxes"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] is True
    printed = open(_printfile, "rb").read()
    assert b"loved this post!" in printed      # ASCII message prints natively
    assert b"\x1dv0" in printed                # attribution footer printed as a small raster
    assert b"@webhook" in printed              # sender header is the system 'webhook' account
    # visible in bob's inbox, attributed to 'webhook'; the stored body carries name + post + url
    client.post("/api/login", data={"username": "bob", "password": "pw123456"})
    inbox = client.get("/api/inbox").json()["faxes"]
    assert inbox[0]["sender_name"] == "webhook"
    assert "On Faxes" in inbox[0]["body"] and "Rita" in inbox[0]["body"]   # `post` + `name` shown
    client.post("/api/logout")


def test_inbound_body_validation():
    key = _secret_for("carol")
    h = {"Authorization": f"Bearer {key}"}
    assert client.post("/api/fax/inbound", data={"body": "   "}, headers=h).status_code == 400
    assert client.post("/api/fax/inbound", data={"body": "x" * 501}, headers=h).status_code == 400
    assert client.post("/api/fax/inbound", data={"body": "just right"}, headers=h).status_code == 200


def test_inbound_regenerate_revokes_old_key():
    key1 = _secret_for("erin")
    key2 = _secret_for("erin")   # regenerate
    assert key1 != key2
    assert client.post("/api/fax/inbound", data={"body": "old"},
                       headers={"Authorization": f"Bearer {key1}"}).status_code == 401
    assert client.post("/api/fax/inbound", data={"body": "new"},
                       headers={"Authorization": f"Bearer {key2}"}).status_code == 200


def test_webhook_sender_hidden_from_callsign_picker():
    # sending an inbound fax lazily creates the 'webhook' system user; it must not be faxable
    key = _secret_for("frank")
    client.post("/api/fax/inbound", data={"body": "spawn webhook user"},
                headers={"Authorization": f"Bearer {key}"})
    client.post("/api/login", data={"username": "frank", "password": "pw123456"})
    names = [u["username"] for u in client.get("/api/users").json()["users"]]
    assert "webhook" not in names
    client.post("/api/logout")


def test_write_all_survives_short_writes():
    """A big payload to a pipe forces short os.write counts; _write_all must still deliver it all."""
    import threading
    data = b"RASTER" * 100_000            # ~600 KB, well past a pipe buffer -> short writes
    r, w = os.pipe()
    got = bytearray()

    def reader():
        while True:
            chunk = os.read(r, 65536)
            if not chunk:
                break
            got.extend(chunk)

    t = threading.Thread(target=reader)
    t.start()
    ok = printer._write_all(w, data)
    os.close(w)
    t.join()
    os.close(r)
    assert ok and bytes(got) == data      # every byte arrived, in order


def test_bridge_print_gives_up_after_failures():
    """A printer that keeps failing to write must not be reprinted forever — after
    BRIDGE_MAX_ATTEMPTS the fax is marked delivered (given up) so the flush loop stops."""
    import asyncio
    from faxxme import app as A, db, printer as P
    _reg("alice"); _reg("bob")
    sender = db.get_user_by_name("alice")
    rcpt = db.get_user_by_name("bob")
    fid = db.create_fax(sender["id"], rcpt["id"], "boom")["id"]
    orig = P.print_local
    P.print_local = lambda data: False           # simulate a printer that always fails the write
    try:
        A._bridge_attempts.pop(fid, None)
        asyncio.run(A._bridge_print(db.get_fax(fid)))
        assert db.get_fax(fid)["status"] == "pending"        # still retrying before the cap
        for _ in range(A.BRIDGE_MAX_ATTEMPTS - 1):
            asyncio.run(A._bridge_print(db.get_fax(fid)))
        assert db.get_fax(fid)["status"] == "delivered"      # gave up -> stops re-queuing
        assert fid not in A._bridge_attempts
    finally:
        P.print_local = orig


def test_escpos_injection_stripped_from_body():
    """Control bytes in a fax body must not reach the printer as ESC/POS commands."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw123456"})
    if os.path.exists(_printfile):
        os.remove(_printfile)
    open(_printfile, "wb").close()
    assert printer.local_available()
    r = client.post("/api/fax", data={"to": "bob", "body": "hi\x1b@\x1dVevil there"})
    assert r.status_code == 200, r.text
    printed = open(_printfile, "rb").read()
    assert b"hi@Vevil there" in printed      # text kept, control bytes removed
    assert b"hi\x1b@" not in printed         # the raw ESC injection is gone
    client.post("/api/logout")


def test_raster_wrap_fills_paper_width():
    """Rasterized (Vietnamese/Unicode) text must wrap by real glyph width, not the widest-glyph
    char count — otherwise it bunches on the left and wastes paper. A ~31-char line fits on one
    58mm (384-dot) line; the old M-based wrap split it into ~2."""
    from PIL import ImageFont
    from faxxme import imaging
    font = ImageFont.truetype(imaging.FONT_PATH, imaging.FONT_SIZE)
    sample = "xin chao ban toi la mot doc gia"          # ~31 chars, fits one 384-dot line
    assert font.getlength(sample) <= imaging.DOTS - 4    # sanity: it does fit
    assert len(imaging._wrap_px(sample, font, imaging.DOTS - 4)) == 1


def test_oversized_image_rejected_before_processing():
    """A huge-canvas image is rejected by declared dimensions, before any decode (bomb guard)."""
    import io
    import pytest
    from PIL import Image
    from faxxme import imaging
    orig = imaging.MAX_PIXELS
    imaging.MAX_PIXELS = 10_000               # tiny cap for the test
    try:
        buf = io.BytesIO()
        Image.new("RGB", (300, 300)).save(buf, format="PNG")   # 90_000 px > cap
        with pytest.raises(ValueError):
            imaging.process_upload(buf.getvalue())
    finally:
        imaging.MAX_PIXELS = orig


def test_inbound_rate_limit():
    from faxxme import app as A
    A.WEBHOOK_RATE_MAX, A.WEBHOOK_RATE_WINDOW = 2, 60
    A._webhook_hits.clear()
    key = _secret_for("grace")
    h = {"Authorization": f"Bearer {key}"}
    codes = [client.post("/api/fax/inbound", data={"body": f"m{i}"}, headers=h).status_code for i in range(4)]
    assert codes[:2] == [200, 200]         # first 2 allowed
    assert codes[2] == 429 and codes[3] == 429  # then rate-limited (per author + per reader IP)
    A.WEBHOOK_RATE_MAX = 0                      # restore (off) for any other tests
    A._webhook_hits.clear()
