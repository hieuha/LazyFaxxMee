"""SQLite persistence for FaxxMe. Stdlib-only, thread-safe via a single guarded connection."""
import os
import sqlite3
import threading
import time

_DB_PATH = os.environ.get("FAXXME_DB", os.path.join(os.path.dirname(__file__), "..", "faxxme.db"))
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    global _conn
    with _lock:
        _conn = _connect()
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                pass_hash    TEXT NOT NULL,
                salt         TEXT NOT NULL,
                created_at   REAL NOT NULL,
                token_hash   TEXT,             -- sha256 of the device/API token, or NULL
                deleted_at   REAL              -- tombstone timestamp (anonymized), or NULL if active
            );
            CREATE TABLE IF NOT EXISTS faxes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id    INTEGER NOT NULL REFERENCES users(id),
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                body         TEXT NOT NULL,
                created_at   REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',  -- pending | delivered
                delivered_at REAL,
                image        BLOB,     -- dithered 1-bit PNG, or NULL
                img_w        INTEGER,
                img_h        INTEGER,
                sender_deleted    INTEGER NOT NULL DEFAULT 0,  -- hidden from sender's outbox
                recipient_deleted INTEGER NOT NULL DEFAULT 0   -- hidden from recipient's inbox
            );
            CREATE INDEX IF NOT EXISTS idx_faxes_recipient ON faxes(recipient_id, status);
            """
        )
        # migrate older DBs that predate newer columns
        have = {r[1] for r in _conn.execute("PRAGMA table_info(faxes)").fetchall()}
        for name, ddl in (("image", "BLOB"), ("img_w", "INTEGER"), ("img_h", "INTEGER"),
                          ("sender_deleted", "INTEGER NOT NULL DEFAULT 0"),
                          ("recipient_deleted", "INTEGER NOT NULL DEFAULT 0")):
            if name not in have:
                _conn.execute(f"ALTER TABLE faxes ADD COLUMN {name} {ddl}")
        have_u = {r[1] for r in _conn.execute("PRAGMA table_info(users)").fetchall()}
        if "token_hash" not in have_u:
            _conn.execute("ALTER TABLE users ADD COLUMN token_hash TEXT")
        if "deleted_at" not in have_u:
            _conn.execute("ALTER TABLE users ADD COLUMN deleted_at REAL")
        # normalize any legacy non-lowercase usernames (idempotent; skip if it would collide)
        for uid, uname in _conn.execute(
                "SELECT id, username FROM users WHERE username <> lower(username)").fetchall():
            low = uname.lower()
            clash = _conn.execute(
                "SELECT 1 FROM users WHERE username=? AND id<>?", (low, uid)).fetchone()
            if not clash:
                _conn.execute("UPDATE users SET username=? WHERE id=?", (low, uid))
        _conn.commit()


MAX_KEEP = 50  # most recent faxes retained per side (inbox / outbox); older auto-pruned


def _c() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


# ---- users ----

def create_user(username: str, display_name: str, pass_hash: str, salt: str) -> dict:
    with _lock:
        cur = _c().execute(
            "INSERT INTO users(username, display_name, pass_hash, salt, created_at) VALUES (?,?,?,?,?)",
            (username, display_name, pass_hash, salt, time.time()),
        )
        _c().commit()
        return get_user(cur.lastrowid)


def get_user(user_id: int) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_name(username: str) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def set_user_token(user_id: int, token_hash: str) -> None:
    with _lock:
        _c().execute("UPDATE users SET token_hash=? WHERE id=?", (token_hash, user_id))
        _c().commit()


def get_user_by_token_hash(username: str, token_hash: str) -> dict | None:
    import hmac
    with _lock:
        row = _c().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row or not row["token_hash"]:
            return None
        return dict(row) if hmac.compare_digest(row["token_hash"], token_hash) else None


def list_users() -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT id, username, display_name FROM users WHERE deleted_at IS NULL ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]


# ---- faxes ----

def create_fax(sender_id: int, recipient_id: int, body: str,
               image: bytes | None = None, img_w: int | None = None,
               img_h: int | None = None) -> dict:
    with _lock:
        cur = _c().execute(
            "INSERT INTO faxes(sender_id, recipient_id, body, created_at, status, image, img_w, img_h) "
            "VALUES (?,?,?,?,'pending',?,?,?)",
            (sender_id, recipient_id, body, time.time(), image, img_w, img_h),
        )
        # keep only the newest MAX_KEEP on each affected side; purge fully-deleted rows
        _trim_side(recipient_id, "recipient")
        _trim_side(sender_id, "sender")
        _purge()
        _c().commit()
        return get_fax(cur.lastrowid)


def _trim_side(user_id: int, side: str) -> None:
    """Mark everything past the newest MAX_KEEP (for this user's inbox or outbox) as deleted."""
    id_col = "recipient_id" if side == "recipient" else "sender_id"
    del_col = "recipient_deleted" if side == "recipient" else "sender_deleted"
    _c().execute(
        f"""UPDATE faxes SET {del_col}=1
            WHERE {id_col}=? AND {del_col}=0 AND id NOT IN (
                SELECT id FROM faxes WHERE {id_col}=? AND {del_col}=0
                ORDER BY created_at DESC LIMIT ?)""",
        (user_id, user_id, MAX_KEEP),
    )


def _purge() -> None:
    """Physically remove faxes both parties have cleared."""
    _c().execute("DELETE FROM faxes WHERE sender_deleted=1 AND recipient_deleted=1")


def clear_inbox(user_id: int) -> int:
    with _lock:
        cur = _c().execute(
            "UPDATE faxes SET recipient_deleted=1 WHERE recipient_id=? AND recipient_deleted=0",
            (user_id,),
        )
        _purge()
        _c().commit()
        return cur.rowcount


def clear_outbox(user_id: int) -> int:
    with _lock:
        cur = _c().execute(
            "UPDATE faxes SET sender_deleted=1 WHERE sender_id=? AND sender_deleted=0",
            (user_id,),
        )
        _purge()
        _c().commit()
        return cur.rowcount


def get_fax(fax_id: int) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM faxes WHERE id=?", (fax_id,)).fetchone()
        return dict(row) if row else None


def pending_for(recipient_id: int) -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM faxes WHERE recipient_id=? AND status='pending' "
            "AND recipient_deleted=0 ORDER BY created_at",
            (recipient_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_delivered(fax_id: int) -> None:
    with _lock:
        _c().execute(
            "UPDATE faxes SET status='delivered', delivered_at=? WHERE id=?",
            (time.time(), fax_id),
        )
        _c().commit()


_FAX_COLS = ("f.id, f.sender_id, f.recipient_id, f.body, f.created_at, f.status, "
             "f.delivered_at, (f.image IS NOT NULL) AS has_image")


def inbox(recipient_id: int, limit: int = 50) -> list[dict]:
    with _lock:
        rows = _c().execute(
            f"""SELECT {_FAX_COLS}, u.username AS sender_name, u.display_name AS sender_display
               FROM faxes f JOIN users u ON u.id=f.sender_id
               WHERE f.recipient_id=? AND f.recipient_deleted=0
               ORDER BY f.created_at DESC LIMIT ?""",
            (recipient_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def outbox(sender_id: int, limit: int = 50) -> list[dict]:
    with _lock:
        rows = _c().execute(
            f"""SELECT {_FAX_COLS}, u.username AS recipient_name, u.display_name AS recipient_display
               FROM faxes f JOIN users u ON u.id=f.recipient_id
               WHERE f.sender_id=? AND f.sender_deleted=0
               ORDER BY f.created_at DESC LIMIT ?""",
            (sender_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- admin ----

def clear_user_token(user_id: int) -> None:
    """Revoke a user's device token (used by the admin panel)."""
    with _lock:
        _c().execute("UPDATE users SET token_hash=NULL WHERE id=?", (user_id,))
        _c().commit()


def tombstone_user(user_id: int) -> None:
    """Anonymize a user **in place** instead of deleting them, so every fax survives for the
    other party (foreign keys stay valid). The account can no longer log in (password wiped),
    its device token is revoked, and the original callsign is freed for re-registration.
    Historical faxes then show the sender/recipient as `deleted_<id>`."""
    with _lock:
        _c().execute(
            "UPDATE users SET username=?, display_name=?, pass_hash='', salt='', "
            "token_hash=NULL, deleted_at=? WHERE id=?",
            (f"deleted_{user_id}", "(deleted operator)", time.time(), user_id),
        )
        _c().commit()


def admin_delete_fax(fax_id: int) -> int:
    """Hard-delete a single fax regardless of the per-side soft-delete flags."""
    with _lock:
        cur = _c().execute("DELETE FROM faxes WHERE id=?", (fax_id,))
        _c().commit()
        return cur.rowcount


def admin_list_users(limit: int = 20, offset: int = 0) -> list[dict]:
    """A page of users with sent/received counts and whether a device token is set."""
    with _lock:
        rows = _c().execute(
            """SELECT u.id, u.username, u.display_name, u.created_at,
                      (u.token_hash IS NOT NULL) AS has_token,
                      (SELECT COUNT(*) FROM faxes f WHERE f.sender_id=u.id)    AS sent,
                      (SELECT COUNT(*) FROM faxes f WHERE f.recipient_id=u.id) AS received
               FROM users u WHERE u.deleted_at IS NULL ORDER BY u.created_at LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def admin_count_users() -> int:
    with _lock:
        return _c().execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0]


def admin_count_faxes(q: str = "") -> int:
    like = f"%{q}%"
    with _lock:
        return _c().execute(
            """SELECT COUNT(*) FROM faxes f
               JOIN users s ON s.id=f.sender_id
               JOIN users r ON r.id=f.recipient_id
               WHERE (?='' OR f.body LIKE ? OR s.username LIKE ? OR r.username LIKE ?)""",
            (q, like, like, like),
        ).fetchone()[0]


def admin_stats() -> dict:
    with _lock:
        c = _c()
        one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
        return {
            "users":     one("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL"),
            "faxes":     one("SELECT COUNT(*) FROM faxes"),
            "pending":   one("SELECT COUNT(*) FROM faxes WHERE status='pending'"),
            "delivered": one("SELECT COUNT(*) FROM faxes WHERE status='delivered'"),
            "images":    one("SELECT COUNT(*) FROM faxes WHERE image IS NOT NULL"),
        }


def admin_all_faxes(q: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
    """All faxes (both sides), newest first, with sender/recipient names. Optional
    substring filter `q` matches the body or either party's callsign."""
    like = f"%{q}%"
    with _lock:
        rows = _c().execute(
            """SELECT f.id, f.body, f.created_at, f.status, f.delivered_at,
                      (f.image IS NOT NULL) AS has_image,
                      f.sender_deleted, f.recipient_deleted,
                      s.username AS sender_name,    s.display_name AS sender_display,
                      r.username AS recipient_name, r.display_name AS recipient_display
               FROM faxes f
               JOIN users s ON s.id=f.sender_id
               JOIN users r ON r.id=f.recipient_id
               WHERE (?='' OR f.body LIKE ? OR s.username LIKE ? OR r.username LIKE ?)
               ORDER BY f.created_at DESC LIMIT ? OFFSET ?""",
            (q, like, like, like, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
