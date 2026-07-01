"""Load and persist product profile configuration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_product_profile_template(root: Path) -> dict[str, Any]:
    path = root / "config" / "product_profile.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_profile_snapshot(root: Path, ssot: dict[str, Any]) -> dict[str, Any]:
    """Merge SSOT-stored profile with template defaults."""
    template = load_product_profile_template(root)
    meta = ssot.get("metadata", {})
    profile = meta.get("product_profile") or template.get("profile", {})
    return {
        "onboarding_status": meta.get("onboarding_status", template.get("onboarding_status", "incomplete")),
        "onboarding_complete": meta.get("onboarding_complete", False),
        "profile": profile,
    }


def save_agent_profile(root: Path, agent_id: str, profile: dict[str, Any]) -> Path:
    """Persist completed profile per agent under config/profiles/."""
    profiles_dir = root / "config" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    out = profiles_dir / f"{agent_id}.json"
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "onboarding_status": "complete",
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def validate_product_profile(profile: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (complete, missing_fields)."""
    required = [
        "agent_name", "product_name", "product_description", "target_customer",
        "typical_deal_size", "preferred_pricing_model", "destination_link",
    ]
    missing = [f for f in required if not profile.get(f)]
    objections = profile.get("common_objections", [])
    if not isinstance(objections, list) or len(objections) < 1:
        missing.append("common_objections")
    if profile.get("accepts_crypto") is None:
        missing.append("accepts_crypto")
    return len(missing) == 0, missing