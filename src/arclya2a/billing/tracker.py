"""Record closed deals for success-based billing with affiliate attribution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ClosedDealRecord:
    deal_id: str
    close_type: str
    revenue_usd: float
    cost_usd: float
    margin_percent: float
    affiliate_code: str
    cta_url: str
    product_name: str
    billing_model: str = "success_based"
    partner_agent_id: str | None = None
    attribution_status: str = "lead_routing_committed"
    closed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("closed_at"):
            data["closed_at"] = datetime.now(timezone.utc).isoformat()
        return data


def _closed_deals_path(root: Path) -> Path:
    deals_dir = root / "data" / "closed_deals"
    deals_dir.mkdir(parents=True, exist_ok=True)
    return deals_dir / "closed_deals.jsonl"


def record_closed_deal(root: Path, record: ClosedDealRecord) -> dict[str, Any]:
    """Append a closed-deal billing record (idempotent per deal_id + close_type)."""
    path = _closed_deals_path(root)
    entry = record.to_dict()
    if not entry.get("closed_at"):
        entry["closed_at"] = datetime.now(timezone.utc).isoformat()

    existing = list_closed_deals(root, deal_id=record.deal_id)
    for row in existing:
        if row.get("close_type") == record.close_type and row.get("cta_url") == record.cta_url:
            return {**row, "duplicate": True}

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return {**entry, "duplicate": False}


def list_closed_deals(
    root: Path,
    *,
    deal_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List closed-deal billing records, newest first."""
    path = _closed_deals_path(root)
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if deal_id and row.get("deal_id") != deal_id:
            continue
        rows.append(row)

    rows.sort(key=lambda r: r.get("closed_at", ""), reverse=True)
    if limit is not None:
        return rows[:limit]
    return rows


def billing_summary(root: Path) -> dict[str, Any]:
    """Aggregate success-based billing totals from closed deals."""
    rows = list_closed_deals(root)
    total_revenue = sum(float(r.get("revenue_usd", 0)) for r in rows)
    total_cost = sum(float(r.get("cost_usd", 0)) for r in rows)
    margins = [float(r.get("margin_percent", 0)) for r in rows if r.get("margin_percent") is not None]
    affiliate_codes = sorted({r.get("affiliate_code", "") for r in rows if r.get("affiliate_code")})
    return {
        "deal_count": len(rows),
        "total_revenue_usd": round(total_revenue, 2),
        "total_cost_usd": round(total_cost, 2),
        "average_margin_percent": round(sum(margins) / len(margins), 2) if margins else None,
        "affiliate_codes": affiliate_codes,
        "billing_model": "success_based",
    }