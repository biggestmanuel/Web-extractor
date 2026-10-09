"""Per-IP rate limiting and a small in-process TTL cache.

This is deliberately simple: an in-process token bucket keyed by client IP.
It protects a single-instance deployment. Behind multiple replicas put a shared
limiter (nginx limit_req, Redis, or a gateway quota) in front instead.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

DEFAULT_CAPACITY = 10          # burst size, also the sustained per-window quota
DEFAULT_WINDOW_SECONDS = 60.0  # refill period


class TokenBucketLimiter:
    """Token bucket with capacity *capacity* refilled over *window* seconds."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY, window: float = DEFAULT_WINDOW_SECONDS) -> None:
        self.capacity = float(capacity)
        self.window = float(window)
        self._tokens: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float, float]:
        """Consume one token for *key*.

        Returns ``(allowed, remaining, retry_after_seconds)``.
        """
        now = time.monotonic()
        refill_per_second = self.capacity / self.window if self.window > 0 else self.capacity

        with self._lock:
            self._evict(now)
            tokens, last = self._tokens.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * refill_per_second)

            if tokens >= 1.0:
                self._tokens[key] = (tokens - 1.0, now)
                return True, max(0.0, tokens - 1.0), 0.0

            self._tokens[key] = (tokens, now)
            retry_after = (1.0 - tokens) / refill_per_second if refill_per_second > 0 else self.window
            return False, 0.0, retry_after

    def _evict(self, now: float) -> None:
        """Drop keys that have refilled to full so the map cannot grow forever."""
        horizon = self.window * 2
        stale = [k for k, (_, last) in self._tokens.items() if now - last > horizon]
        for key in stale:
            del self._tokens[key]
        if len(self._tokens) > 10_000:
            # Unexpected flood of distinct IPs: drop the least recently used half.
            for key in list(self._tokens.keys())[: len(self._tokens) // 2]:
                del self._tokens[key]


class TTLCache:
    """Thread-safe cache of recent extraction results, keyed by URL."""

    def __init__(self, max_entries: int = 128, ttl: float = 300.0) -> None:
        self.max_entries = max_entries
        self.ttl = ttl
        self._entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> object | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if now - stored_at > self.ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: object) -> None:
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
