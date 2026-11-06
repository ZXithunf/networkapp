"""A tiny in-memory sliding-window rate limiter.

State lives in the process, so each uvicorn worker counts separately and
counts reset on restart. That is fine for one small EC2 instance; use Redis
or an AWS WAF rate rule if you need a shared, durable limit.
"""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int = 30, window_seconds: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock  # injectable so tests don't need to sleep
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record a hit for `key`; return False if it is over the limit."""
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._sweep(cutoff)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest hit for `key` leaves the window."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(hits[0] + self.window - self._clock()) + 1)

    def _sweep(self, cutoff: float) -> None:
        """Drop idle clients so memory can't grow without bound."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]
