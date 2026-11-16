"""Sliding-window rate limiter.

In-process by design for single-instance deployments. When running several API
replicas, put a shared limiter (e.g. Redis or your load balancer) in front; the
per-session active-job and queue-depth limits enforced in the database still
apply globally.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from ..errors import OmniError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        k = f"{bucket}:{key}"
        with self._lock:
            q = self._hits[k]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                raise OmniError("rate_limited")
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                for stale in [kk for kk, v in self._hits.items() if not v][:10_000]:
                    self._hits.pop(stale, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
