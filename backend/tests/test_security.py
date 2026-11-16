"""SSRF defences, URL validation, filename sanitization, adapter error mapping."""
from __future__ import annotations

import socket

import pytest

from omniconvert.errors import OmniError
from omniconvert.media.planner import sanitize_filename
from omniconvert.security.ssrf import is_ip_allowed, redact_url, validate_public_url


@pytest.mark.parametrize("url", [
    "http://localhost/x.mp4",
    "http://127.0.0.1/x.mp4",
    "http://127.1/x.mp4",
    "http://2130706433/x.mp4",          # decimal 127.0.0.1
    "http://0x7f000001/x.mp4",          # hex 127.0.0.1
    "http://10.0.0.5/x.mp4",
    "http://192.168.1.1/x.mp4",
    "http://172.16.0.1/x.mp4",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://[::1]/x.mp4",
    "http://[::ffff:127.0.0.1]/x.mp4",
    "http://[fd00::1]/x.mp4",
    "http://100.64.0.1/x.mp4",
    "http://0.0.0.0/x.mp4",
    "https://example.com:22/x.mp4",     # disallowed port
    "https://example.com:6379/x.mp4",
])
def test_blocked_destinations(url):
    with pytest.raises(OmniError) as e:
        validate_public_url(url)
    assert e.value.code in ("blocked_destination", "invalid_url", "media_unavailable")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a.mp4", "gopher://x/", "javascript:alert(1)",
                                 "https://user:pass@example.com/a.mp4", "https://exa mple.com/", "", "http://"])
def test_invalid_urls(url):
    with pytest.raises(OmniError):
        validate_public_url(url)


def test_ip_rules():
    assert not is_ip_allowed("169.254.169.254")
    assert not is_ip_allowed("::ffff:10.0.0.1")
    assert not is_ip_allowed("2002:7f00:0001::1")  # 6to4 wrapping 127.0.0.1
    assert is_ip_allowed("93.184.215.14")
    assert is_ip_allowed("2606:4700::6810:84e5")


def test_redaction_strips_signatures():
    r = redact_url("https://cdn.example.com/v/abc.mp4?Signature=SECRET&Expires=1#frag")
    assert "SECRET" not in r and r == "https://cdn.example.com/v/abc.mp4"


def test_filename_sanitization():
    assert sanitize_filename("../../etc/passwd", "mp4") == "etc_passwd.mp4"
    assert sanitize_filename('a<b>:"c|?*.mp4', "mp3") == "a_b_c.mp3"
    assert sanitize_filename("CON", "mp4") == "omniconvert.mp4"
    assert sanitize_filename("  ", "gif") == "omniconvert.gif"
    assert sanitize_filename("Vidéo été 🎬", "mp4") == "Vidéo été 🎬.mp4"


def test_egress_proxy_blocks_internal_targets():
    from omniconvert.security.egress_proxy import get_egress_proxy

    proxy = get_egress_proxy()
    for target in ("127.0.0.1:443", "localhost:443", "169.254.169.254:80", "10.1.2.3:443", "example.com:22"):
        s = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        resp = s.recv(200).decode()
        s.close()
        assert resp.startswith("HTTP/1.1 403"), (target, resp)
    s = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
    s.sendall(b"GET http://127.0.0.1:80/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    assert s.recv(200).decode().startswith("HTTP/1.1 403")
    s.close()


def test_analyze_api_rejects_ssrf_and_unsupported(client):
    for url, code in [("http://169.254.169.254/latest/meta-data/", "blocked_destination"),
                      ("http://localhost:80/clip.mp4", "blocked_destination"),
                      ("not a url", "invalid_url"),
                      ("https://example.com/some/article", "unsupported_link")]:
        r = client.post("/api/analyze", json={"url": url})
        assert r.status_code >= 400 and r.json()["error"]["code"] == code, (url, r.text)
        assert r.json()["error"]["action"] or code == "invalid_url"


def test_ytdlp_error_classification():
    from omniconvert.adapters.ytdlp import classify_error

    cases = {
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access": "restricted_content",
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot": "source_requires_access",
        "ERROR: This video is DRM protected": "drm_protected",
        "ERROR: Unsupported URL: https://x": "unsupported_link",
        "HTTP Error 429: Too Many Requests": "source_rate_limited",
        "ERROR: [instagram] x: Requested content is not available, rate-limit reached or login required": "source_rate_limited",
        "ERROR: Video unavailable. This video has been removed by the uploader": "media_unavailable",
        "ERROR: The uploader has not made this video available in your country": "restricted_content",
        "ERROR: [vimeo] 1: The web client only works when logged-in. Use --cookies, --cookies-from-browser, --username and --password": "source_requires_access",
        "Tunnel connection failed: 403 Forbidden by egress policy": "blocked_destination",
    }
    for text, code in cases.items():
        assert classify_error(text).code == code, (text, classify_error(text).code)


def test_cors_origin_and_cookie_params():
    from omniconvert.config import Settings
    from omniconvert.security.cors import cookie_params, cors_enabled, origin_allowed

    s = Settings(
        cors_origins="https://app.example, https://www.example",
        cors_origin_regex=r"https://.*\.vercel\.app",
        cookie_secure=False,
    )
    assert cors_enabled(s)
    assert origin_allowed("https://app.example", s)
    assert origin_allowed("https://www.example", s)
    assert origin_allowed("https://omniconvert-git-main-user.vercel.app", s)
    assert not origin_allowed("https://evil.example", s)
    kw = cookie_params(s)
    assert kw["samesite"] == "none" and kw["secure"] is True and kw["httponly"] is True

    local = Settings(cors_origins="", cors_origin_regex="", cookie_secure=False, cookie_samesite="lax")
    assert not cors_enabled(local)
    assert cookie_params(local)["samesite"] == "lax"
    assert cookie_params(local)["secure"] is False
