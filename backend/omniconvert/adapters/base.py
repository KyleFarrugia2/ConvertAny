from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

SUPPORTED = "supported"
LIMITED = "limited"
TEMP_UNAVAILABLE = "temporarily_unavailable"
UNSUPPORTED = "unsupported"


@dataclass
class FetchRequest:
    url: str
    item: dict
    dest_dir: Path
    category: str  # video | audio | gif
    target_short: Optional[int]  # desired short-side resolution, None = best
    prefer_container: str  # helps pick streams that can be remuxed without re-encoding
    max_bytes: int
    on_progress: Callable[[Optional[float]], None]
    should_cancel: Callable[[], bool]


@dataclass
class SourceAdapter:
    id: str
    name: str
    domains: tuple[str, ...]
    status: str = SUPPORTED
    notes: str = ""
    examples: tuple[str, ...] = ()
    _health: deque = field(default_factory=lambda: deque(maxlen=12), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def matches(self, host: str, path: str) -> bool:
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    # health tracking: temporary upstream failures flip the public status.
    def record(self, ok: bool, temporary: bool = False) -> None:
        with self._lock:
            self._health.append("ok" if ok else ("temp" if temporary else "perm"))

    @property
    def effective_status(self) -> str:
        with self._lock:
            h = list(self._health)
        if len(h) >= 5 and h[-5:].count("temp") >= 5:
            return TEMP_UNAVAILABLE
        return self.status

    def analyze(self, url: str) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch(self, req: FetchRequest) -> Path:  # pragma: no cover - interface
        raise NotImplementedError

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.effective_status, "notes": self.notes, "domains": list(self.domains)}
