"""Password hashing (pbkdf2, stdlib) and signed session cookies (hmac, stdlib). No native deps."""
import base64
import hashlib
import hmac
import os
import secrets

_ITERATIONS = 200_000
COOKIE_NAME = "fx_session"

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
