"""JSONL persistence for agent hangout features (deal rooms, hubs, marketplace)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def hangout_dir(root: Path) -> Path:
    path = root / "data" / "hangout"
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_record(root: Path, filename: str, record: dict[str, Any]) -> None:
    path = hangout_dir(root) / filename
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_records(root: Path, filename: str) -> list[dict[str, Any]]:
    path = hangout_dir(root) / filename
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latest_by_id(
    rows: list[dict[str, Any]],
    id_field: str,
    *,
    sort_key: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Keep the latest record per id (by updated_at or append order)."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get(id_field)
        if not key:
            continue
        prev = out.get(key)
        if prev is None:
            out[key] = row
            continue
        prev_ts = prev.get("updated_at") or prev.get("created_at") or ""
        cur_ts = row.get("updated_at") or row.get("created_at") or ""
        if cur_ts >= prev_ts:
            out[key] = row
    return out