"""Profit margin math and guardrail checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MarginCheckResult:
    approved: bool
    margin_percent: float
    veto_reason: str | None
    revenue_usd: float
    cost_usd: float


def load_pricing_menu(root: Path) -> dict[str, Any]:
    path = root / "pricing" / "pricing_menu.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_margin_percent(revenue_usd: float, cost_usd: float) -> float:
    if revenue_usd <= 0:
        return -100.0
    return ((revenue_usd - cost_usd) / revenue_usd) * 100.0


def check_margin_guardrail(
    root: Path,
    *,
    revenue_usd: float,
    cost_usd: float,
    service_tier: str = "outreach_sequence",
) -> MarginCheckResult:
    """Check margin against pricing_menu thresholds. Fail closed on veto."""
    menu = load_pricing_menu(root)
    margin = compute_margin_percent(revenue_usd, cost_usd)
    targets = menu["margin_targets"]
    tier = menu["service_tiers"].get(service_tier, {})
    min_margin = tier.get("min_margin_percent", targets["minimum_percent"])
    veto_threshold = targets["veto_threshold_percent"]

    if margin < veto_threshold:
        return MarginCheckResult(
            approved=False,
            margin_percent=margin,
            veto_reason=f"Margin {margin:.1f}% below veto threshold {veto_threshold}%",
            revenue_usd=revenue_usd,
            cost_usd=cost_usd,
        )
    if margin < min_margin:
        return MarginCheckResult(
            approved=False,
            margin_percent=margin,
            veto_reason=f"Margin {margin:.1f}% below tier minimum {min_margin}%",
            revenue_usd=revenue_usd,
            cost_usd=cost_usd,
        )
    return MarginCheckResult(
        approved=True,
        margin_percent=margin,
        veto_reason=None,
        revenue_usd=revenue_usd,
        cost_usd=cost_usd,
    )