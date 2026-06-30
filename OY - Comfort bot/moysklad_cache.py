"""
In-process TTL caching for MoySklad API lookups.
Asyncio-safe; single-process bot only.
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CacheEntry:
    """Single cache entry with expiration."""
    value: Any
    expires_at: float  # time.time() + ttl_seconds

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class TTLCache:
    """
    Thread-safe (via asyncio.Lock) in-process cache with TTL and size bounds.

    - On access: remove expired entries from the dict
    - On set: enforce size cap (remove entry with the earliest expiration)
    - No background cleanup task (single-process, simple TTL on access)
    """

    def __init__(self, ttl_seconds: int, max_size: int):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Fetch from cache if not expired; return None if expired or missing."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any) -> None:
        """Store value with expiration; enforce max_size cap."""
        async with self._lock:
            expires_at = time.time() + self.ttl_seconds
            self._cache[key] = CacheEntry(value=value, expires_at=expires_at)

            # Evict the entry with the earliest expiration if over size cap
            if len(self._cache) > self.max_size:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].expires_at,
                )
                del self._cache[oldest_key]

    async def invalidate(self, key: str) -> None:
        """Remove entry immediately (used after balance-changing webhook)."""
        async with self._lock:
            self._cache.pop(key, None)

    async def invalidate_all(self) -> None:
        """Clear entire cache (used for testing or reset)."""
        async with self._lock:
            self._cache.clear()

    async def size(self) -> int:
        """Current number of entries (for monitoring)."""
        async with self._lock:
            return len(self._cache)
