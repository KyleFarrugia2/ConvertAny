"""CORS / cookie helpers for a split deploy (Vercel website + separate API)."""
from __future__ import annotations

import re
from typing import Any

from ..config import Settings


def origin_allowed(origin: str, s: Settings) -> bool:
    if not origin:
        return False
    allowed = {o.strip().rstrip("/") for o in s.cors_origins.split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True
    pattern = s.cors_origin_regex.strip()
    if not pattern:
        return False
    try:
        return re.fullmatch(pattern, origin) is not None
    except re.error:
        return False


def cors_enabled(s: Settings) -> bool:
    return bool(s.cors_origins.strip() or s.cors_origin_regex.strip())


def cookie_params(s: Settings) -> dict[str, Any]:
    """HttpOnly session cookie. Cross-site (Vercel → API) needs SameSite=None; Secure."""
    requested = (s.cookie_samesite or "lax").lower()
    cross = cors_enabled(s) or requested == "none"
    samesite = "none" if cross else requested
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"
    return {
        "httponly": True,
        "samesite": samesite,
        "secure": True if samesite == "none" else s.cookie_secure,
        "max_age": 60 * 60 * 24 * 30,
        "path": "/",
    }
