"""Per-partner production API keys issued at graduation."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRODUCTION_KEY_PREFIX = "arclya_prod_"


def _keys_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "production_keys.json"


def generate_production_key() -> str:
    return f"{PRODUCTION_KEY_PREFIX}{secrets.token_urlsafe(24)}"


def load_production_keys(root: Path) -> dict[str, dict[str, Any]]:
    path = _keys_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_production_keys(root: Path, keys: dict[str, dict[str, Any]]) -> None:
    path = _keys_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def issue_production_key(
    root: Path,
    *,
    partner_id: str,
    agent_name: str,
    graduated_by: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create and persist a new per-partner production API key."""
    key = generate_production_key()
    keys = load_production_keys(root)
    keys[key] = {
        "partner_id": partner_id,
        "agent_name": agent_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "graduated_by": graduated_by,
        "active": True,
        "metadata": metadata or {},
    }
    save_production_keys(root, keys)
    return key


def lookup_production_key(root: Path, provided_key: str) -> dict[str, Any] | None:
    if not provided_key.startswith(PRODUCTION_KEY_PREFIX):
        return None
    entry = load_production_keys(root).get(provided_key)
    if not entry or not entry.get("active", True):
        return None
    return {**entry, "production_key_prefix": provided_key[:20] + "…"}


def list_production_keys_for_partner(root: Path, partner_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, entry in load_production_keys(root).items():
        if entry.get("partner_id") == partner_id:
            rows.append({**entry, "key_prefix": key[:20] + "…"})
    return rows