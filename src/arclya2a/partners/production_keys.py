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


def revoke_production_key(
    root: Path,
    key: str,
    *,
    reason: str,
) -> bool:
    """Deactivate a single production key. Returns True when the key existed and was active."""
    keys = load_production_keys(root)
    entry = keys.get(key)
    if not entry or not entry.get("active", True):
        return False
    entry["active"] = False
    entry["revoked_at"] = datetime.now(timezone.utc).isoformat()
    entry["revoked_reason"] = reason
    save_production_keys(root, keys)
    return True


def revoke_production_keys_for_partner(
    root: Path,
    partner_id: str,
    *,
    reason: str,
    except_key: str | None = None,
) -> list[str]:
    """Deactivate all active production keys for a partner. Returns revoked key prefixes."""
    keys = load_production_keys(root)
    revoked: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for key, entry in keys.items():
        if entry.get("partner_id") != partner_id:
            continue
        if not entry.get("active", True):
            continue
        if except_key and key == except_key:
            continue
        entry["active"] = False
        entry["revoked_at"] = now
        entry["revoked_reason"] = reason
        revoked.append(key[:20] + "…")
    if revoked:
        save_production_keys(root, keys)
    return revoked


def rotate_production_key_for_partner(
    root: Path,
    *,
    partner_id: str,
    agent_name: str,
    rotated_by: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Revoke all active keys for a partner and issue a new production key."""
    revoked = revoke_production_keys_for_partner(
        root,
        partner_id,
        reason=reason or "key_rotation",
    )
    key_metadata = {"account_type": "external_agent", **(metadata or {})}
    new_key = issue_production_key(
        root,
        partner_id=partner_id,
        agent_name=agent_name,
        graduated_by=rotated_by,
        metadata=key_metadata,
    )
    return new_key, revoked