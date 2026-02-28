"""Unit tests for Deduper — TTL-based message ID deduplication."""
from __future__ import annotations

import time

from agents.discord_listener.deduper import DEFAULT_TTL_SECONDS, Deduper


class TestCheckAndMark:
    def test_first_occurrence_not_duplicate(self) -> None:
        d = Deduper()
        assert d.check_and_mark("msg_001") is False

    def test_second_occurrence_is_duplicate(self) -> None:
        d = Deduper()
        d.check_and_mark("msg_001")
        assert d.check_and_mark("msg_001") is True

    def test_different_ids_are_independent(self) -> None:
        d = Deduper()
        assert d.check_and_mark("msg_001") is False
        assert d.check_and_mark("msg_002") is False
        assert d.check_and_mark("msg_003") is False

    def test_seen_after_first_check(self) -> None:
        d = Deduper()
        d.check_and_mark("msg_001")
        assert len(d) == 1

    def test_multiple_unique_ids_counted(self) -> None:
        d = Deduper()
        for i in range(5):
            d.check_and_mark(f"msg_{i:03d}")
        assert len(d) == 5


class TestTTLExpiry:
    def test_expired_entry_not_duplicate(self) -> None:
        """After TTL expires, the same ID should be treated as new."""
        d = Deduper(ttl_seconds=0)  # immediate expiry
        d.check_and_mark("msg_001")
        # Sleep a tiny bit so monotonic clock advances past expiry
        time.sleep(0.01)
        assert d.check_and_mark("msg_001") is False

    def test_non_expired_entry_still_duplicate(self) -> None:
        d = Deduper(ttl_seconds=DEFAULT_TTL_SECONDS)
        d.check_and_mark("msg_001")
        assert d.check_and_mark("msg_001") is True

    def test_eviction_removes_expired_entries(self) -> None:
        d = Deduper(ttl_seconds=0)
        d.check_and_mark("msg_001")
        d.check_and_mark("msg_002")
        time.sleep(0.01)
        # len() calls _evict_expired() internally; all entries have TTL=0
        assert len(d) == 0


class TestClear:
    def test_clear_empties_cache(self) -> None:
        d = Deduper()
        d.check_and_mark("msg_001")
        d.check_and_mark("msg_002")
        d.clear()
        assert len(d) == 0

    def test_after_clear_same_id_not_duplicate(self) -> None:
        d = Deduper()
        d.check_and_mark("msg_001")
        d.clear()
        assert d.check_and_mark("msg_001") is False


class TestLen:
    def test_len_zero_initially(self) -> None:
        d = Deduper()
        assert len(d) == 0

    def test_len_increments_on_new_ids(self) -> None:
        d = Deduper()
        d.check_and_mark("a")
        assert len(d) == 1
        d.check_and_mark("b")
        assert len(d) == 2

    def test_len_unchanged_on_duplicate(self) -> None:
        d = Deduper()
        d.check_and_mark("a")
        d.check_and_mark("a")  # duplicate — no new entry
        assert len(d) == 1
