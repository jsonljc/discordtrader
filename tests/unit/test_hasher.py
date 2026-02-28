"""Unit tests for audit.hasher (Blake2b tamper-evident hashing)."""
from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from audit.hasher import compute_hash, stamp, verify
from schemas.signal_event import SignalEvent
from schemas.trade_intent import AssetClass, ConfidenceBucket, Direction, TradeIntent


def _make_signal(**kwargs: object) -> SignalEvent:
    defaults: dict[str, object] = {
        "source_guild_id": "100",
        "source_channel_id": "200",
        "source_message_id": "300",
        "source_author_id": "400",
        "raw_text": "BUY AAPL @ 175",
    }
    defaults.update(kwargs)
    return SignalEvent(**defaults)  # type: ignore[arg-type]


class TestComputeHash:
    def test_returns_non_empty_hex_string(self) -> None:
        event = _make_signal()
        h = compute_hash(event)
        assert isinstance(h, str)
        assert len(h) == 64   # blake2b digest_size=32 → 64 hex chars
        int(h, 16)            # must be valid hex

    def test_deterministic_same_model(self) -> None:
        """Identical field values always produce the same hash."""
        from datetime import datetime
        from uuid import UUID

        fixed_id = UUID("12345678-1234-4234-8234-123456789abc")
        fixed_cid = UUID("abcdefab-cdef-4def-8def-abcdefabcdef")
        fixed_ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

        e1 = SignalEvent(
            event_id=fixed_id, correlation_id=fixed_cid, created_at=fixed_ts,
            source_guild_id="1", source_channel_id="2", source_message_id="3",
            source_author_id="4", raw_text="test",
        )
        e2 = SignalEvent(
            event_id=fixed_id, correlation_id=fixed_cid, created_at=fixed_ts,
            source_guild_id="1", source_channel_id="2", source_message_id="3",
            source_author_id="4", raw_text="test",
        )
        assert compute_hash(e1) == compute_hash(e2)

    def test_different_raw_text_changes_hash(self) -> None:
        from datetime import datetime
        from uuid import UUID

        base_kwargs = {
            "event_id": UUID("12345678-1234-4234-8234-123456789abc"),
            "correlation_id": UUID("abcdefab-cdef-4def-8def-abcdefabcdef"),
            "created_at": datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            "source_guild_id": "1", "source_channel_id": "2",
            "source_message_id": "3", "source_author_id": "4",
        }
        e1 = SignalEvent(**base_kwargs, raw_text="BUY AAPL")  # type: ignore[arg-type]
        e2 = SignalEvent(**base_kwargs, raw_text="SELL AAPL")  # type: ignore[arg-type]
        assert compute_hash(e1) != compute_hash(e2)

    def test_event_hash_field_excluded_from_computation(self) -> None:
        """Pre-populating event_hash must not change the computed hash."""
        event = _make_signal()
        h_before = compute_hash(event)
        event_with_hash = event.model_copy(update={"event_hash": "some_prior_hash"})
        h_after = compute_hash(event_with_hash)
        assert h_before == h_after


class TestStamp:
    def test_stamp_sets_event_hash(self) -> None:
        event = _make_signal()
        assert event.event_hash == ""
        stamped = stamp(event)
        assert isinstance(stamped.event_hash, str)  # type: ignore[attr-defined]
        assert len(stamped.event_hash) == 64        # type: ignore[attr-defined]

    def test_stamp_does_not_mutate_original(self) -> None:
        event = _make_signal()
        stamp(event)
        assert event.event_hash == ""

    def test_stamp_returns_correct_hash(self) -> None:
        event = _make_signal()
        stamped = stamp(event)
        expected = compute_hash(event)
        assert stamped.event_hash == expected  # type: ignore[attr-defined]

    def test_stamped_model_passes_verify(self) -> None:
        event = stamp(_make_signal())
        assert verify(event)


class TestVerify:
    def test_verify_unstamped_returns_false(self) -> None:
        event = _make_signal()
        assert not verify(event)

    def test_verify_stamped_returns_true(self) -> None:
        event = stamp(_make_signal())
        assert verify(event)

    def test_verify_tampered_field_returns_false(self) -> None:
        """Changing a field after stamping should break verification."""
        event = stamp(_make_signal())
        tampered = event.model_copy(update={"raw_text": "SELL NVDA @ 999"})
        assert not verify(tampered)

    def test_verify_with_trade_intent(self) -> None:
        """Hasher works across schema types."""
        from uuid import uuid4
        intent = TradeIntent(
            correlation_id=uuid4(),
            source_signal_id=uuid4(),
            ticker="NVDA",
            asset_class=AssetClass.EQUITY,
            direction=Direction.LONG,
            entry_price=Decimal("800"),
            stop_price=Decimal("780"),
            confidence=ConfidenceBucket.HIGH,
            template_name="standard_equity_buy",
        )
        stamped = stamp(intent)
        assert verify(stamped)
        tampered = stamped.model_copy(update={"ticker": "MSFT"})
        assert not verify(tampered)
