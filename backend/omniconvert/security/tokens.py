"""Session tokens and signed, expiring download tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from ..config import get_settings


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_download_token(kind: str, object_id: str, session_id: str, ttl: int = 900) -> str:
    """Token binding a download to an object *and* the owning session."""
    exp = int(time.time()) + ttl
    payload = f"{kind}.{object_id}.{exp}"
    sig = _sign(f"{payload}.{session_id}")
    return f"{payload}.{sig}"


def verify_download_token(token: str, session_id: str) -> tuple[str, str] | None:
    try:
        kind, object_id, exp_s, sig = token.split(".")
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < time.time():
        return None
    expected = _sign(f"{kind}.{object_id}.{exp}.{session_id}")
    if not hmac.compare_digest(expected, sig):
        return None
    return kind, object_id
