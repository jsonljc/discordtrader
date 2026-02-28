from __future__ import annotations

import time

DEFAULT_TTL_SECONDS: int = 3600  # 1 hour — covers any reasonable reconnect window


class Deduper:
    """
    Idempotency filter for Discord message IDs.

    Uses a TTL dict (message_id → expiry_timestamp) to detect re-delivered
    messages within a rolling window.  Discord snowflakes are monotonically
    increasing, so collisions across time windows are impossible.

    Thread-safe within a single asyncio event loop (no locks needed).
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}  # message_id → expiry_timestamp

    def check_and_mark(self, message_id: str) -> bool:
        """
        Return True if this message_id was seen within the TTL window (duplicate).
        Always marks the ID as seen, so the second call returns True.
        """
        self._evict_expired()
        now = time.monotonic()
        if message_id in self._seen:
            return True
        self._seen[message_id] = now + self._ttl
        return False

    def _evict_expired(self) -> None:
        """Remove entries whose TTL has elapsed.  Called before every lookup."""
        now = time.monotonic()
        expired = [k for k, exp in self._seen.items() if exp < now]
        for k in expired:
            del self._seen[k]

    def __len__(self) -> int:
        """Number of active (non-expired) entries."""
        self._evict_expired()
        return len(self._seen)

    def clear(self) -> None:
        """Flush the entire cache (useful in tests)."""
        self._seen.clear()
