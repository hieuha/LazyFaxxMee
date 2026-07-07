"""Password hashing (pbkdf2, stdlib) and signed session cookies (hmac, stdlib). No native deps."""
import base64
import hashlib
import hmac
import os
import secrets

_ITERATIONS = 200_000
COOKIE_NAME = "fx_session"
ADMIN_COOKIE = "fx_admin"

_SECRET_PATH = os.environ.get("FAXXME_SECRET", os.path.join(os.path.dirname(__file__), "..", ".faxxme_secret"))


def _load_secret() -> bytes:
    try:
        with open(_SECRET_PATH, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        sec = secrets.token_bytes(32)
        with open(_SECRET_PATH, "wb") as fh:
            fh.write(sec)
        os.chmod(_SECRET_PATH, 0o600)
        return sec


_SECRET = _load_secret()


# ---- passwords ----

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, pass_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, pass_hash)


# ---- signed session tokens ----

def make_token(user_id: int) -> str:
    payload = str(user_id).encode()
    sig = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + \
        base64.urlsafe_b64encode(sig).decode().rstrip("=")


# ---- device tokens (for the headless printer agent) ----

def new_device_token() -> str:
    """A fresh high-entropy API token, shown to the user once."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Store/compare device tokens as sha256 (they're high-entropy, so no slow KDF needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---- admin panel (single hashed password in the env, no DB user involved) ----

def admin_password_hash(password: str) -> str:
    """sha256 hex of the admin password — this is what's stored in FAXXME_ADMIN_PASSWORD_HASH."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_admin_password(password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(admin_password_hash(password), (stored_hash or "").strip().lower())


def make_admin_session() -> str:
    """An opaque admin session value, signed with the server secret (not tied to any user)."""
    return hmac.new(_SECRET, b"admin-session", hashlib.sha256).hexdigest()


def valid_admin_session(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, make_admin_session())


def read_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64 + "==")
        sig = base64.urlsafe_b64decode(sig_b64 + "==")
    except Exception:
        return None
    expected = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return int(payload.decode())
    except ValueError:
        return None
