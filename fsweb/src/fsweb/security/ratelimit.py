"""Rate limiting (05 §1: rate limits instead of CAPTCHA).

Fixed-window counters. In-memory for a single node; Redis (``FSWEB_REDIS_URL``)
when several stateless API nodes share the limit (HA profile, E-ARCH-02).
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_s: int) -> bool:
        """Record one hit; return False when the limit is exceeded."""
        ...

    def reset(self, key: str) -> None: ...


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, limit: int, window_s: int) -> bool:
        window = int(time.time() // window_s)
        with self._lock:
            w, n = self._counts.get(key, (window, 0))
            if w != window:
                w, n = window, 0
            n += 1
            self._counts[key] = (w, n)
            if len(self._counts) > 100_000:  # bound memory under attack
                self._counts = {k: v for k, v in self._counts.items() if v[0] == window}
            return n <= limit

    def reset(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)


class RedisRateLimiter:  # pragma: no cover - needs a Redis server
    def __init__(self, url: str) -> None:
        import redis  # type: ignore[import-not-found]

        self._r = redis.Redis.from_url(url)

    def hit(self, key: str, limit: int, window_s: int) -> bool:
        window = int(time.time() // window_s)
        k = f"rl:{key}:{window}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_s + 1)
        n, _ = pipe.execute()
        return int(n) <= limit

    def reset(self, key: str) -> None:
        for k in self._r.scan_iter(f"rl:{key}:*"):
            self._r.delete(k)


def make_rate_limiter(redis_url: str | None) -> RateLimiter:
    if redis_url:
        try:
            return RedisRateLimiter(redis_url)
        except Exception:
            return MemoryRateLimiter()
    return MemoryRateLimiter()
