"""Basic in-memory per-client rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class RateLimiter:
    """Sliding-window rate limiter keyed by client identifier."""

    def __init__(self, max_per_minute: int = 60, window_seconds: int = 60):
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, client_id: str) -> tuple[bool, int, int]:
        """
        Record a request and return (allowed, remaining, retry_after_seconds).

        When not allowed, retry_after_seconds is seconds until the oldest hit
        in the window expires.
        """
        now = time.time()
        with self._lock:
            window = [t for t in self._hits[client_id] if now - t < self.window_seconds]
            if len(window) >= self.max_per_minute:
                oldest = min(window)
                retry_after = max(1, int(self.window_seconds - (now - oldest)) + 1)
                self._hits[client_id] = window
                return False, 0, retry_after

            window.append(now)
            self._hits[client_id] = window
            remaining = self.max_per_minute - len(window)
            return True, remaining, 0