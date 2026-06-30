"""Append-only audit log."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _audit_dir(root: Path) -> Path:
    d = root / "data" / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_audit_record(
    root: Path,
    *,
    agent_id: str,
    action: str,
    reasoning: str,
    handoff_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one audit record to data/audit/."""
    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "action": action,
        "reasoning": reasoning,
        "handoff_id": handoff_id,
        "metadata": metadata or {},
    }
    audit_dir = _audit_dir(root)
    day_file = audit_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with open(day_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_audit_records(root: Path, limit: int = 100) -> list[dict[str, Any]]:
    """Read recent audit records."""
    audit_dir = root / "data" / "audit"
    if not audit_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("*.jsonl"), reverse=True):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        if len(records) >= limit:
            break
    return records[:limit]