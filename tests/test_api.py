"""End-to-end-ish tests for FaxxMe: auth, fax queueing, delivery, local bridge, and WS flush."""
import base64
import os
import tempfile

os.environ["FAXXME_DB"] = tempfile.mktemp(suffix=".db")
os.environ["FAXXME_SECRET"] = tempfile.mktemp(suffix=".secret")
_printfile = tempfile.mktemp(suffix=".prn")
os.environ["FAXXME_PRINTER_DEV"] = _printfile
os.environ["FAXXME_LOCAL_USER"] = "bob"  # bob has the wired-in printer

from fastapi.testclient import TestClient  # noqa: E402
from faxxme import app as appmod, printer  # noqa: E402

client = TestClient(appmod.app)


def _reg(u, p="pw12", name=None):
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
    r = client.post("/api/login", data={"username": "alice", "password": "pw12"})
    assert r.status_code == 200


def test_register_validation():
    assert client.post("/api/register", data={"username": "x", "password": "pw12"}).status_code == 400
    assert client.post("/api/register", data={"username": "okname", "password": "1"}).status_code == 400
    _reg("dupe")
    assert client.post("/api/register", data={"username": "dupe", "password": "pw12"}).status_code == 409


def test_fax_queue_then_ws_flush():
    """alice faxes carol (offline) -> queued. carol connects WS -> receives it -> acks -> delivered."""
    _reg("carol")
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    r = client.post("/api/fax", data={"to": "carol", "body": "meet at dawn"})
    assert r.status_code == 200, r.text
    assert r.json()["delivered"] is False  # carol offline, no local bridge for her

    # carol logs in and opens her printer link (websocket)
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
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
    client.post("/api/login", data={"username": "bob", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    r = client.post("/api/fax", data={"to": "carol"},
                    files={"image": ("x.png", _png(20, 20), "image/png")})
    assert r.status_code == 200  # image alone (no body) is allowed


def test_image_access_control():
    _reg("trinity")
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    r = client.post("/api/fax", data={"to": "bob"},
                    files={"image": ("x.png", _png(16, 16), "image/png")})
    fid = client.get("/api/outbox").json()["faxes"][0]["id"]
    # trinity is neither sender nor recipient -> forbidden
    client.post("/api/logout")
    login = client.post("/api/login", data={"username": "trinity", "password": "pw12"})
    assert login.status_code == 200
    assert client.get(f"/api/fax/{fid}/image").status_code == 403


def test_send_errors():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    assert client.post("/api/fax", data={"to": "ghost", "body": "hi"}).status_code == 404
    assert client.post("/api/fax", data={"to": "bob", "body": "   "}).status_code == 400


def test_empty_recipient_clean_400():
    """Empty 'to' must be a friendly string 400, not a 422 validation array (the [object Object] bug)."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    assert client.post("/api/fax", data={"to": "carol", "body": "x" * 200}).status_code == 200
    assert client.post("/api/fax", data={"to": "carol", "body": "y" * 201}).status_code == 400


def test_cannot_fax_yourself():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    r = client.post("/api/fax", data={"to": "alice", "body": "note to self"})
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"]


def test_clear_is_per_side():
    """Clearing inbox hides it only from the recipient; the sender's outbox keeps it."""
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    client.post("/api/fax", data={"to": "carol", "body": "PERSIST-ME"})
    # carol clears her inbox
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw12"})
    assert any(f["body"] == "PERSIST-ME" for f in client.get("/api/inbox").json()["faxes"])
    cleared = client.post("/api/inbox/clear").json()
    assert cleared["ok"] and cleared["cleared"] >= 1
    assert client.get("/api/inbox").json()["faxes"] == []
    # alice still has it in her outbox
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    assert any(f["body"] == "PERSIST-ME" for f in client.get("/api/outbox").json()["faxes"])
    client.post("/api/outbox/clear")
    assert client.get("/api/outbox").json()["faxes"] == []


def test_auto_prune_keeps_50():
    from faxxme import db as dbmod
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    for i in range(55):
        client.post("/api/fax", data={"to": "carol", "body": f"burst-{i}"})
    carol = dbmod.get_user_by_name("carol")
    visible = dbmod._c().execute(
        "SELECT COUNT(*) FROM faxes WHERE recipient_id=? AND recipient_deleted=0",
        (carol["id"],),
    ).fetchone()[0]
    assert visible == 50  # older ones auto-pruned to the cap
    client.post("/api/logout")
    client.post("/api/login", data={"username": "carol", "password": "pw12"})
    faxes = client.get("/api/inbox").json()["faxes"]
    assert len(faxes) == 50 and faxes[0]["body"] == "burst-54"  # newest first


def test_device_token_auth_and_revocation():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    assert client.get("/api/me").json()["has_token"] is False
    tok = client.post("/api/token/regenerate").json()["token"]
    assert tok and client.get("/api/me").json()["has_token"] is True

    # agent connects to /ws with the token (no cookie)
    client.post("/api/logout")
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as ws:
        assert ws.receive_json()["type"] == "hello"

    # regenerate -> the old token is revoked, a new one works
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
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
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    tok = client.post("/api/token/regenerate").json()["token"]
    assert client.get("/api/me").json()["node_online"] is False
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as agent:
        assert agent.receive_json()["type"] == "hello"
        assert client.get("/api/me").json()["node_online"] is True     # agent connected
    assert client.get("/api/me").json()["node_online"] is False         # agent gone


def test_test_print_routes_to_agent():
    client.post("/api/logout")
    client.post("/api/login", data={"username": "alice", "password": "pw12"})
    tok = client.post("/api/token/regenerate").json()["token"]
    assert client.post("/api/test-print").json()["delivered"] is False   # no agent, not the bridge user
    hdrs = {"Authorization": f"Bearer {tok}", "X-Faxxme-User": "alice"}
    with client.websocket_connect("/ws", headers=hdrs) as agent:
        assert agent.receive_json()["type"] == "hello"
        assert client.post("/api/test-print").json()["delivered"] is True
        fax = agent.receive_json()
        assert fax["type"] == "fax"
        assert b"TEST PRINT" in base64.b64decode(fax["escpos_b64"])
