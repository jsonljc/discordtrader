"""Unit tests for audit/ledger_writer.py."""
from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from audit.ledger_writer import LedgerWriter, _compute_line_hash
from schemas.execution_receipt import ExecutionReceipt, OrderStatus


def _make_receipt() -> ExecutionReceipt:
    return ExecutionReceipt(
        correlation_id=uuid4(),
        source_decision_id=uuid4(),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("10"),
        avg_fill_price=Decimal("175.50"),
        is_paper=True,
        profile="discord_equities",
    )


def test_ledger_writer_no_op_when_path_none() -> None:
    """LedgerWriter does nothing when path is None."""
    writer = LedgerWriter(None)
    writer.append(_make_receipt())
    # No exception, no file created


def test_ledger_writer_no_op_when_path_empty() -> None:
    """LedgerWriter does nothing when path is empty string."""
    writer = LedgerWriter("")
    writer.append(_make_receipt())
    # No exception


def test_ledger_writer_appends_jsonl_line() -> None:
    """LedgerWriter appends a JSON line with prev_hash, event, line_hash."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        writer = LedgerWriter(path)
        receipt = _make_receipt()
        writer.append(receipt)

        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        line = json.loads(lines[0])
        assert "prev_hash" in line
        assert line["prev_hash"] == ""
        assert "event" in line
        assert "line_hash" in line
        assert line["event"]["status"] == "FILLED"
    finally:
        Path(path).unlink(missing_ok=True)


def test_ledger_writer_chain_links_lines() -> None:
    """Each line's prev_hash matches previous line's line_hash."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        writer = LedgerWriter(path)
        writer.append(_make_receipt())
        writer.append(_make_receipt())

        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f.readlines()]
        assert len(lines) == 2
        assert lines[0]["prev_hash"] == ""
        assert lines[1]["prev_hash"] == lines[0]["line_hash"]
    finally:
        Path(path).unlink(missing_ok=True)


def test_compute_line_hash_deterministic() -> None:
    """_compute_line_hash produces same output for same input."""
    h1 = _compute_line_hash("", {"a": 1})
    h2 = _compute_line_hash("", {"a": 1})
    assert h1 == h2


def test_compute_line_hash_changes_with_prev_hash() -> None:
    """_compute_line_hash changes when prev_hash changes."""
    h1 = _compute_line_hash("", {"a": 1})
    h2 = _compute_line_hash("x", {"a": 1})
    assert h1 != h2
