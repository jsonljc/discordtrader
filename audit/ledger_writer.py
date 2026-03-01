"""
Append-only JSONL ledger with hash chain for tamper-evident audit.

Writes ExecutionReceipt (and optionally other events) to a JSONL file.
Each line includes prev_hash to form a SHA-256 chain. Disabled when
ledger_path is None or empty.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from audit.logger import get_logger
from pydantic import BaseModel


def _to_serializable(obj: Any) -> Any:
    """Recursively convert to JSON-safe primitives."""
    if hasattr(obj, "model_dump"):
        return _to_serializable(obj.model_dump(mode="json"))
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    return obj


def _compute_line_hash(prev_hash: str, event_dict: dict[str, Any]) -> str:
    """SHA-256 of prev_hash + canonical event JSON."""
    payload = json.dumps(
        {"prev_hash": prev_hash, "event": event_dict},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LedgerWriter:
    """
    Append-only ledger for pipeline events.
    Each line: {"prev_hash": str, "event": dict, "line_hash": str}
    """

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path and str(path).strip() else None
        self._last_hash = ""
        self._log = get_logger("ledger_writer")
        self._file = None

    def append(self, event: BaseModel) -> None:
        """Append a stamped event to the ledger. No-op if path is not set."""
        if self._path is None:
            return
        try:
            event_dict = _to_serializable(event.model_dump(mode="json"))
            line_hash = _compute_line_hash(self._last_hash, event_dict)
            line = {
                "prev_hash": self._last_hash,
                "event": event_dict,
                "line_hash": line_hash,
            }
            self._last_hash = line_hash
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "ledger_append_failed",
                error=str(exc),
                path=str(self._path),
            )
