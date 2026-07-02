"""Aggregate operational health for /health and /status endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import read_audit_records
from arclya2a.billing.tracker import billing_summary
from arclya2a.payments.crypto import crypto_payments_summary
from arclya2a.learning.learning_scheduler import (
    interval_hours,
    load_scheduler_state,
    scheduler_enabled,
)
from arclya2a.learning.patch_generator import list_patches
from arclya2a.learning.patch_outcomes import list_learning_runs, patch_success_stats
from arclya2a.observability.ops_events import list_ops_events
from arclya2a.observability.security_events import build_security_metrics
from arclya2a.agents.component_health import build_component_health
from arclya2a.agents.platform_status import build_agent_platform_status
from arclya2a.tools.observability import execution_summary, list_tool_executions


def _handoff_metrics(root: Path, *, limit: int = 200) -> dict[str, Any]:
    records = read_audit_records(root, limit=limit)
    received = 0
    completed = 0
    emergency_stops = 0
    failed = 0

    for row in records:
        action = row.get("action")
        if action == "handoff_request_received":
            received += 1
        elif action == "handoff_chain_complete":
            completed += 1
            meta = row.get("metadata") or {}
            if meta.get("emergency_stop"):
                emergency_stops += 1
        elif action == "handoff_chain_failed":
            failed += 1

    success_rate = None
    if received:
        success_rate = round((completed - emergency_stops) / received, 4)

    return {
        "requests": received,
        "completed": completed,
        "failed": failed,
        "emergency_stops": emergency_stops,
        "success_rate": success_rate,
    }


def _tool_health(root: Path, *, limit: int = 100) -> dict[str, Any]:
    summary = execution_summary(root, limit=limit)
    total = summary.get("total", 0)
    failed = summary.get("failed", 0)
    failure_rate = round(failed / total, 4) if total else 0.0
    recent = list_tool_executions(root, limit=5)
    return {
        "summary": summary,
        "failure_rate": failure_rate,
        "recent_executions": [
            {
                "tool_id": r.get("tool_id"),
                "agent_id": r.get("agent_id"),
                "outcome": r.get("outcome"),
                "error_code": r.get("error_code"),
                "timestamp": r.get("timestamp"),
            }
            for r in recent
        ],
    }


def _learning_status(root: Path) -> dict[str, Any]:
    runs = list_learning_runs(root, limit=1)
    scheduler_state = load_scheduler_state(root)
    latest = runs[0] if runs else {}
    return {
        "scheduler_enabled": scheduler_enabled(),
        "last_run_at": scheduler_state.get("last_run_at") or latest.get("timestamp"),
        "last_trigger": latest.get("trigger"),
        "last_summary": {
            "issues_detected": latest.get("issues_detected", []),
            "issues_improved": latest.get("issues_improved", []),
            "issues_still_open": latest.get("issues_still_open", []),
            "patches_created": latest.get("patches_created", 0),
            "patches_applied": latest.get("patches_applied", 0),
            "pending_review": latest.get("pending_review", 0),
        } if latest else None,
        "patch_outcomes": patch_success_stats(root),
    }


def _pending_high_risk_patches(root: Path) -> list[dict[str, Any]]:
    pending = list_patches(root, status="pending")
    high_risk = [p for p in pending if p.get("risk_class") == "high_risk"]
    return [
        {
            "patch_id": p.get("patch_id"),
            "issue": p.get("issue"),
            "weakness": p.get("weakness"),
            "confidence": p.get("confidence"),
            "timestamp": p.get("timestamp"),
        }
        for p in high_risk[:20]
    ]


def _overall_status(
    *,
    tool_failure_rate: float,
    handoff_success_rate: float | None,
    pending_high_risk: int,
    scheduler_enabled_flag: bool,
    last_run_at: str | None,
) -> str:
    degraded_reasons: list[str] = []

    if tool_failure_rate > 0.2:
        degraded_reasons.append("high_tool_failure_rate")
    if handoff_success_rate is not None and handoff_success_rate < 0.8:
        degraded_reasons.append("low_handoff_success_rate")
    if pending_high_risk > 0:
        degraded_reasons.append("high_risk_patches_pending")

    if scheduler_enabled_flag and last_run_at:
        try:
            last_dt = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
            overdue = datetime.now(timezone.utc) - last_dt > timedelta(hours=interval_hours() * 2)
            if overdue:
                degraded_reasons.append("learning_scheduler_overdue")
        except ValueError:
            pass

    return "degraded" if degraded_reasons else "healthy"


def build_ops_status(root: Path) -> dict[str, Any]:
    """Build full operational status snapshot."""
    tool_health = _tool_health(root)
    handoffs = _handoff_metrics(root)
    learning = _learning_status(root)
    high_risk = _pending_high_risk_patches(root)
    billing = billing_summary(root)
    payments = crypto_payments_summary(root)
    security = build_security_metrics(root)

    status = _overall_status(
        tool_failure_rate=tool_health.get("failure_rate", 0.0),
        handoff_success_rate=handoffs.get("success_rate"),
        pending_high_risk=len(high_risk),
        scheduler_enabled_flag=learning.get("scheduler_enabled", False),
        last_run_at=learning.get("last_run_at"),
    )
    if security.get("counts_24h", {}).get("total", 0) >= 10:
        status = "degraded"

    external_agents = build_agent_platform_status(root)
    component_health = build_component_health(root)
    if external_agents.get("activity_24h", {}).get("suspicious_events", 0) >= 5:
        status = "degraded"
    if component_health.get("email", {}).get("status") == "misconfigured":
        status = "degraded"

    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "external_agents": external_agents,
        "component_health": component_health,
        "launch_readiness": {
            "ready": component_health.get("launch_ready", False),
            "overall": component_health.get("overall"),
            "blocking_issues": component_health.get("blocking_issues", []),
        },
        "learning": learning,
        "tools": tool_health,
        "handoffs": handoffs,
        "security": security,
        "pending_high_risk_patches": high_risk,
        "pending_high_risk_count": len(high_risk),
        "billing": {
            "deal_count": billing.get("deal_count", 0),
            "total_revenue_usd": billing.get("total_revenue_usd", 0),
        },
        "payments": payments,
        "recent_ops_events": list_ops_events(root, limit=10),
    }