"""SSRF defences.

Two layers:
1. ``validate_public_url`` — applied to every user-supplied URL before analysis.
2. The egress proxy (``egress_proxy.py``) — every outbound request made while
   analyzing or fetching media (including redirects and the media/segment
   requests issued by yt-dlp) is forced through it. The proxy resolves DNS
   once, checks *every* resolved address, then connects to the vetted IP, so a
   DNS-rebinding answer cannot redirect the connection to an internal host.
"""
from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from ..config import get_settings
from ..errors import OmniError

MAX_URL_LENGTH = 2048

# Networks never reachable from media workers (in addition to the ipaddress
# module's is_private/is_loopback/etc. checks).
_EXTRA_BLOCKED = [
    "0.0.0.0/8",
    "100.64.0.0/10",  # carrier-grade NAT
    "169.254.0.0/16",  # link-local incl. 169.254.169.254 cloud metadata
    "192.0.0.0/24",
    "192.0.2.0/24",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "::/128",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
    "64:ff9b::/96",  # NAT64 can map to internal v4
    "2001:db8::/32",
    "fd00:ec2::254/128",  # AWS IMDS v6
]

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
}


@lru_cache
def _blocked_networks() -> tuple:
    extra = [c.strip() for c in get_settings().egress_extra_blocked_cidrs.split(",") if c.strip()]
    return tuple(ipaddress.ip_network(c) for c in _EXTRA_BLOCKED + extra)


def is_ip_allowed(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    # Unwrap IPv4-mapped / 6to4 / Teredo addresses and check the embedded v4 too.
    if isinstance(addr, ipaddress.IPv6Address):
        for embedded in (addr.ipv4_mapped, addr.sixtofour, addr.teredo[1] if addr.teredo else None):
            if embedded is not None and not is_ip_allowed(str(embedded)):
                return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    ):
        return False
    return not any(addr in net for net in _blocked_networks() if net.version == addr.version)


def resolve_host(host: str, port: int) -> list[str]:
    """Resolve and return all addresses. Raises OmniError if *any* is disallowed."""
    host_l = host.lower().rstrip(".")
    if host_l in BLOCKED_HOSTNAMES or host_l.endswith((".localhost", ".internal", ".local", ".lan", ".home.arpa")):
        raise OmniError("blocked_destination")
    try:
        infos = socket.getaddrinfo(host_l, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise OmniError("media_unavailable", "We couldn't reach that website (the domain didn't resolve).")
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise OmniError("media_unavailable")
    for ip in ips:
        if not is_ip_allowed(ip):
            raise OmniError("blocked_destination")
    return ips


def validate_public_url(raw: str, *, resolve: bool = True) -> str:
    """Normalize and validate a user-supplied URL. Returns the normalized URL."""
    if not raw or len(raw) > MAX_URL_LENGTH:
        raise OmniError("invalid_url")
    raw = raw.strip()
    if any(c in raw for c in "\r\n\t\x00 "):
        raise OmniError("invalid_url")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise OmniError("invalid_url")
    if parts.scheme.lower() not in ("http", "https"):
        raise OmniError("invalid_url", "Only http:// and https:// links are supported.")
    if parts.username or parts.password:
        raise OmniError("invalid_url", "Links containing usernames or passwords aren't accepted.")
    host = (parts.hostname or "").lower()
    if host in BLOCKED_HOSTNAMES:
        raise OmniError("blocked_destination")
    if not host or "." not in host and ":" not in host:
        raise OmniError("invalid_url")
    effective_port = port or (443 if parts.scheme.lower() == "https" else 80)
    if effective_port not in get_settings().allowed_ports:
        raise OmniError("blocked_destination", "Links to non-standard ports aren't fetched.")
    # IP-literal hosts are checked directly (covers decimal/hex forms that
    # urlsplit leaves alone via getaddrinfo below as well).
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal and not is_ip_allowed(host.strip("[]")):
        raise OmniError("blocked_destination")
    if resolve:
        resolve_host(host.strip("[]"), effective_port)
    netloc = host if not port else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" + (f":{port}" if port else "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def redact_url(url: str | None) -> str:
    """Strip query strings & fragments (which often carry signatures/tokens) for logging."""
    if not url:
        return ""
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.hostname or "", p.path[:120], "", ""))
    except ValueError:
        return "<unparseable-url>"
