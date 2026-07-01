"""Append-only operational event stream for metrics and dashboards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ops_events_path(root: Path) -> Path:
    return root / "data" / "ops" / "ops_events.jsonl"


def record_ops_event(
    root: Path,
    event: str,
    *,
    category: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a lightweight operational event for dashboards and metrics."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "category": category,
        "data": data or {},
    }
    path = _ops_events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def list_ops_events(
    root: Path,
    *,
    category: str | None = None,
    event: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List recent ops events, newest first."""
    path = _ops_events_path(root)
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if category and row.get("category") != category:
            continue
        if event and row.get("event") != event:
            continue
        rows.append(row)

    rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return rows[:limit]