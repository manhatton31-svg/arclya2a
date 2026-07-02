"""Simple agent marketplace — post offers/requests, pay via USDC checkout."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.hangout_store import append_record, latest_by_id, load_records
from arclya2a.agents.security import is_valid_capability_token, sanitize_profile_text

MARKETPLACE_FILE = "marketplace_listings.jsonl"
LISTING_ID_PREFIX = "mp_"
VALID_LISTING_TYPES = frozenset({"offer", "request"})
MAX_LISTINGS_PER_AGENT = 10
MAX_ACTIVE_PER_AGENT = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup_hash(agent_id: str, title: str, listing_type: str) -> str:
    raw = f"{agent_id}|{listing_type}|{title.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_marketplace_listing(
    root: Path,
    *,
    poster_agent_id: str,
    listing_type: str,
    title: str,
    description: str,
    capabilities: list[str] | None = None,
    price_usd: float | None = None,
    package_id: str | None = None,
) -> dict[str, Any]:
    """Post an offer or request (anti-duplication via content hash)."""
    if listing_type not in VALID_LISTING_TYPES:
        raise ValueError(f"listing_type must be one of: {sorted(VALID_LISTING_TYPES)}")

    clean_title = sanitize_profile_text(title, max_len=128)
    clean_desc = sanitize_profile_text(description, max_len=2000)
    if not clean_title or not clean_desc:
        raise ValueError("title and description are required")

    caps = []
    for c in capabilities or []:
        tok = str(c).strip().lower()
        if is_valid_capability_token(tok):
            caps.append(tok)

    poster_rows = [
        r
        for r in latest_by_id(load_records(root, MARKETPLACE_FILE), "listing_id").values()
        if r.get("poster_agent_id") == poster_agent_id
    ]
    active = [r for r in poster_rows if r.get("status") == "active"]
    if len(active) >= MAX_ACTIVE_PER_AGENT:
        raise ValueError(f"maximum {MAX_ACTIVE_PER_AGENT} active listings per agent")

    dedup = _dedup_hash(poster_agent_id, clean_title, listing_type)
    for row in poster_rows:
        if row.get("dedup_hash") == dedup and row.get("status") == "active":
            raise ValueError("duplicate listing — update or cancel the existing offer first")

    total = len(poster_rows)
    if total >= MAX_LISTINGS_PER_AGENT:
        raise ValueError(f"maximum {MAX_LISTINGS_PER_AGENT} total listings per agent")

    listing_id = f"{LISTING_ID_PREFIX}{secrets.token_hex(8)}"
    record = {
        "listing_id": listing_id,
        "listing_type": listing_type,
        "title": clean_title,
        "description": clean_desc,
        "capabilities": caps,
        "poster_agent_id": poster_agent_id,
        "price_usd": round(float(price_usd), 2) if price_usd is not None else None,
        "package_id": package_id,
        "status": "active",
        "dedup_hash": dedup,
        "payment": {
            "currency": "USDC",
            "checkout_endpoint": "POST /payments/crypto/checkout",
            "x402_compatible": True,
            "margin_positive_required": True,
        },
        "created_at": _now(),
        "updated_at": _now(),
    }
    append_record(root, MARKETPLACE_FILE, record)
    return _public_listing(record)


def list_marketplace_listings(
    root: Path,
    *,
    listing_type: str | None = None,
    capability: str | None = None,
    poster_id: str | None = None,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = list(latest_by_id(load_records(root, MARKETPLACE_FILE), "listing_id").values())
    if listing_type:
        rows = [r for r in rows if r.get("listing_type") == listing_type]
    if poster_id:
        rows = [r for r in rows if r.get("poster_agent_id") == poster_id]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if capability:
        c = capability.strip().lower()
        rows = [r for r in rows if c in (r.get("capabilities") or [])]
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return [_public_listing(r) for r in rows[offset : offset + limit]]


def get_marketplace_listing(root: Path, listing_id: str) -> dict[str, Any] | None:
    row = latest_by_id(load_records(root, MARKETPLACE_FILE), "listing_id").get(listing_id)
    if not row:
        return None
    return _public_listing(row)


def build_listing_checkout_hint(
    root: Path,
    listing_id: str,
    *,
    base_url: str,
) -> dict[str, Any] | None:
    """Return crypto checkout instructions for a marketplace listing."""
    listing = get_marketplace_listing(root, listing_id)
    if not listing or listing.get("status") != "active":
        return None

    from arclya2a.payments.packages import get_payment_package, list_payment_packages

    package_id = listing.get("package_id")
    pkg = get_payment_package(package_id, root) if package_id else None
    packages = list_payment_packages(root)

    hint: dict[str, Any] = {
        "listing_id": listing_id,
        "listing_type": listing.get("listing_type"),
        "poster_agent_id": listing.get("poster_agent_id"),
        "price_usd": listing.get("price_usd"),
        "currency": "USDC",
        "checkout_url": f"{base_url.rstrip('/')}/payments/crypto/checkout",
        "packages_url": f"{base_url.rstrip('/')}/payments/crypto/packages",
        "x402_intent_url": f"{base_url.rstrip('/')}/payments/crypto/intent",
        "recommended_package_id": package_id,
        "available_packages": [p.get("id") for p in packages if p.get("id")],
    }
    if pkg:
        hint["package"] = {
            "id": pkg.get("id"),
            "name": pkg.get("name"),
            "amount_usd": pkg.get("amount_usd"),
            "service_type": pkg.get("service_type"),
        }
    return hint


def complete_marketplace_listing(
    root: Path,
    *,
    listing_id: str,
    completed_by_agent_id: str,
) -> dict[str, Any]:
    listings = latest_by_id(load_records(root, MARKETPLACE_FILE), "listing_id")
    row = listings.get(listing_id)
    if not row:
        raise ValueError("listing not found")
    row["status"] = "completed"
    row["completed_by"] = completed_by_agent_id
    row["updated_at"] = _now()
    append_record(root, MARKETPLACE_FILE, row)

    from arclya2a.agents.reputation import record_reputation_event

    record_reputation_event(
        root,
        agent_id=row.get("poster_agent_id"),
        event_type="marketplace_complete",
        delta=4.0,
        source="marketplace",
        metadata={"listing_id": listing_id},
    )
    return _public_listing(row)


def _public_listing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "listing_id": row.get("listing_id"),
        "listing_type": row.get("listing_type"),
        "title": row.get("title"),
        "description": row.get("description"),
        "capabilities": row.get("capabilities", []),
        "poster_agent_id": row.get("poster_agent_id"),
        "price_usd": row.get("price_usd"),
        "package_id": row.get("package_id"),
        "status": row.get("status"),
        "payment": row.get("payment"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }