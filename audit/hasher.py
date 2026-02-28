from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _to_serializable(obj: Any) -> Any:
    """Recursively convert pydantic/stdlib types to JSON-safe primitives."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    return obj


def compute_hash(model: BaseModel) -> str:
    """
    Compute a Blake2b-32 hex digest of the model, excluding the event_hash
    field itself so the computation is stable.

    The payload is canonical JSON (keys sorted, no extra whitespace) encoded
    as UTF-8 bytes.  The same field values always produce the same digest.
    """
    raw = model.model_dump(exclude={"event_hash"})
    canonical = _to_serializable(raw)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=32).hexdigest()


def stamp(model: BaseModel) -> BaseModel:
    """
    Return a copy of the model with event_hash populated.
    The original model is not modified.

    Usage:
        event = stamp(SignalEvent(...))
        assert event.event_hash != ""
    """
    hash_val = compute_hash(model)
    return model.model_copy(update={"event_hash": hash_val})


def verify(model: BaseModel) -> bool:
    """
    Return True if the model's event_hash matches its recomputed hash.
    A False result indicates the event was tampered with after stamping.
    """
    stored: str = getattr(model, "event_hash", "")
    if not stored:
        return False
    return stored == compute_hash(model)
