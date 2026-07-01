"""Load and persist product profile configuration."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

VALID_PRICING_MODELS = {
    "subscription", "one_time", "usage_based", "hybrid", "success_based", "custom",
}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


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


def build_destination_cta(profile: dict[str, Any]) -> str:
    """Build CTA URL from destination_link + affiliate_code."""
    base = profile.get("destination_link", "").strip()
    if not base:
        return ""
    code = (profile.get("affiliate_code") or "").strip()
    if not code:
        return base
    parsed = urlparse(base)
    params = parse_qs(parsed.query)
    params["ref"] = [code]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def save_agent_profile(root: Path, agent_id: str, profile: dict[str, Any]) -> Path:
    """Persist completed profile per agent under config/profiles/."""
    profiles_dir = root / "config" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    out = profiles_dir / f"{agent_id}.json"
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "onboarding_status": "complete",
        "cta_url": build_destination_cta(profile),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    template_path = root / "config" / "product_profile.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["profile"] = profile
    template["onboarding_status"] = "complete"
    template["completed_at"] = payload["saved_at"]
    template_path.write_text(json.dumps(template, indent=2), encoding="utf-8")

    return out


def validate_product_profile(profile: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (complete, missing_or_invalid_fields)."""
    missing: list[str] = []
    required = [
        "agent_name", "product_name", "product_description", "target_customer",
        "typical_deal_size", "preferred_pricing_model", "destination_link",
    ]
    for field in required:
        value = profile.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(field)

    desc = profile.get("product_description", "")
    if desc and len(str(desc).strip()) < 20:
        missing.append("product_description(min_length)")

    objections = profile.get("common_objections", [])
    if not isinstance(objections, list) or len(objections) < 3:
        missing.append("common_objections(min_3)")

    if profile.get("accepts_crypto") is None:
        missing.append("accepts_crypto")

    model = profile.get("preferred_pricing_model", "")
    if model and model not in VALID_PRICING_MODELS:
        missing.append("preferred_pricing_model(invalid)")

    link = profile.get("destination_link", "")
    if link and not _URL_RE.match(str(link).strip()):
        missing.append("destination_link(invalid_url)")

    return len(missing) == 0, missing