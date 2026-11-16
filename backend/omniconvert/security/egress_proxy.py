"""A tiny SSRF-filtering forward proxy bound to 127.0.0.1.

All outbound HTTP(S) traffic for URL analysis and media fetching (yt-dlp) is
routed through this proxy. For every request it:

* allows only CONNECT (for HTTPS) and absolute-form plain HTTP requests;
* allows only configured destination ports (default 80/443);
* resolves the hostname once, rejects the request if *any* resolved address is
  private/loopback/link-local/metadata/reserved, and then connects to the
  already-vetted IP — so DNS rebinding between check and use is impossible;
* never logs full URLs (only host:port).

Redirects are followed by the client, which issues a new proxied request, so
every hop is re-validated.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional
from urllib.parse import urlsplit

from ..config import get_settings
from ..errors import OmniError
from .ssrf import resolve_host

log = logging.getLogger("omniconvert.egress")

MAX_HEADER_BYTES = 64 * 1024
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 120


class EgressProxy:
    def __init__(self) -> None:
        self.port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()
        self.blocked_count = 0

    # public -------------------------------------------------------------------
    def start(self) -> int:
        t = threading.Thread(target=self._run, name="egress-proxy", daemon=True)
        t.start()
        if not self._ready.wait(10):
            raise RuntimeError("egress proxy failed to start")
        assert self.port
        return self.port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # internals ----------------------------------------------------------------
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        server = self._loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    async def _resolve(self, host: str, port: int) -> str:
        if port not in get_settings().allowed_ports:
            raise OmniError("blocked_destination")
        ips = await asyncio.get_running_loop().run_in_executor(None, resolve_host, host, port)
        # Prefer IPv4 for broad reachability.
        ips.sort(key=lambda ip: ":" in ip)
        return ips[0]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except Exception:
            writer.close()
            return
        if len(head) > MAX_HEADER_BYTES:
            await self._reply(writer, 431, "Request Header Fields Too Large")
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            await self._reply(writer, 400, "Bad Request")
            return
        try:
            if method.upper() == "CONNECT":
                await self._connect(target, reader, writer)
            else:
                await self._forward_http(method, target, version, lines[1:], reader, writer)
        except OmniError as e:
            self.blocked_count += 1
            log.warning("egress blocked", extra={"event": "egress_blocked", "target": target.split("?")[0][:200], "code": e.code})
            await self._reply(writer, 403, "Forbidden by egress policy")
        except (ConnectionError, asyncio.TimeoutError, OSError):
            await self._reply(writer, 502, "Bad Gateway")

    async def _connect(self, target: str, reader, writer) -> None:
        host, _, port_s = target.rpartition(":")
        host = host.strip("[]")
        port = int(port_s)
        ip = await self._resolve(host, port)
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._pipe_both(reader, writer, up_r, up_w)

    async def _forward_http(self, method, target, version, header_lines, reader, writer) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            raise OmniError("blocked_destination")
        port = parts.port or 80
        ip = await self._resolve(parts.hostname, port)
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        out = [f"{method} {path} {version}"]
        for h in header_lines:
            if not h:
                continue
            name = h.split(":", 1)[0].strip().lower()
            if name in ("proxy-connection", "proxy-authorization", "connection", "keep-alive"):
                continue
            out.append(h)
        out.append("Connection: close")
        up_w.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
        await up_w.drain()
        await self._pipe_both(reader, writer, up_r, up_w)

    async def _pipe_both(self, cr, cw, ur, uw) -> None:
        async def pipe(r, w):
            try:
                while True:
                    data = await asyncio.wait_for(r.read(65536), IDLE_TIMEOUT)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))

    @staticmethod
    async def _reply(writer, code: int, reason: str) -> None:
        try:
            writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
        finally:
            writer.close()


_proxy: Optional[EgressProxy] = None
_lock = threading.Lock()


def get_egress_proxy() -> EgressProxy:
    global _proxy
    with _lock:
        if _proxy is None:
            _proxy = EgressProxy()
            _proxy.start()
    return _proxy
