"""Persistence for x402 V2 deferred and batch settlement records."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


X402_EXT_FILE = "x402_extensions.jsonl"


def _payments_data_dir(root: Path) -> Path:
    path = root / "data" / "payments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(root: Path, record: dict[str, Any]) -> None:
    path = _payments_data_dir(root) / X402_EXT_FILE
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def record_deferred_payment(
    root: Path,
    *,
    payment_id: str,
    settle_after_hours: int,
    facilitator_id: str,
) -> dict[str, Any]:
    record = {
        "extension_id": f"x402d_{secrets.token_hex(6)}",
        "type": "deferred",
        "payment_id": payment_id,
        "settle_after_hours": settle_after_hours,
        "facilitator_id": facilitator_id,
        "status": "authorized",
        "created_at": _now(),
    }
    _append(root, record)
    return record


def record_batch_settlement(
    root: Path,
    *,
    payment_ids: list[str],
    facilitator_id: str,
) -> dict[str, Any]:
    batch_id = f"x402b_{secrets.token_hex(8)}"
    record = {
        "extension_id": batch_id,
        "type": "batch_settlement",
        "payment_ids": payment_ids,
        "facilitator_id": facilitator_id,
        "status": "pending_settlement",
        "created_at": _now(),
    }
    _append(root, record)
    return record