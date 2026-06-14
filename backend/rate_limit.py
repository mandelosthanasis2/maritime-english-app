"""A tiny in-memory sliding-window rate limiter.

Used to cap the expensive admin content-generation endpoint so an external
agent (or a runaway script) can't fire unbounded Claude calls. It is
per-process: with multiple gunicorn workers the effective limit is multiplied
by the worker count, which is fine as a cheap safety valve. If you ever need a
hard, cluster-wide limit, back this with Redis instead.
"""

import threading
import time


class RateLimiter:
    """Allow at most `max_calls` per `period` seconds for each key."""

    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self._hits = {}  # key -> list[timestamp]
        self._lock = threading.Lock()

    def check(self, key):
        """Record a hit for `key`. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        with self._lock:
            window = [t for t in self._hits.get(key, []) if now - t < self.period]
            if len(window) >= self.max_calls:
                self._hits[key] = window
                retry_after = max(1, int(self.period - (now - window[0])) + 1)
                return False, retry_after
            window.append(now)
            self._hits[key] = window
            # Opportunistically drop keys that have fully aged out, so the dict
            # doesn't grow without bound under many distinct callers.
            if len(self._hits) > 1024:
                self._hits = {
                    k: [t for t in v if now - t < self.period]
                    for k, v in self._hits.items()
                    if any(now - t < self.period for t in v)
                }
            return True, 0
