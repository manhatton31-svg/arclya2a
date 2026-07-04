"""Constitutional guardrail enforcement for Agent Hangout closes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arclya2a.pricing.margin import check_margin_guardrail


@dataclass
class HangoutGuardrailResult:
    passed: bool
    method: str
    qc_passed: bool
    margin_approved: bool
    veto_reason: str | None
    details: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "method": self.method,
            "qc_passed": self.qc_passed,
            "margin_approved": self.margin_approved,
            "veto_reason": self.veto_reason,
            "details": self.details,
        }


def _audit_dir(root: Path) -> Path:
    return root / "data" / "audit"


def find_handoff_chain_outcome(
    root: Path,
    *,
    deal_id: str | None = None,
    handoff_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Locate a completed orchestrator run with constitutional outcome metadata."""
    audit_dir = _audit_dir(root)
    if not audit_dir.exists():
        return None

    needle_deal = (deal_id or "").strip() or None
    needle_run = (handoff_run_id or "").strip() or None
    if not needle_deal and not needle_run:
        return None

    for path in sorted(audit_dir.glob("*.jsonl"), reverse=True):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("action") != "handoff_chain_complete":
                    continue
                meta = record.get("metadata") or {}
                if needle_run and record.get("id") == needle_run:
                    return record
                if needle_deal and meta.get("deal_id") == needle_deal:
                    return record
    return None


def _outcome_from_orchestrator_record(record: dict[str, Any]) -> HangoutGuardrailResult:
    meta = record.get("metadata") or {}
    outcome = meta.get("outcome") or {}
    emergency_stop = bool(meta.get("emergency_stop"))
    margin_approved = outcome.get("margin_approved") is True
    qc_passed = outcome.get("qc_passed") is True
    passed = not emergency_stop and margin_approved and qc_passed
    veto_reason = None
    if emergency_stop:
        veto_reason = "Orchestrator run ended in emergency_stop"
    elif not margin_approved:
        veto_reason = "Orchestrator run did not pass profit_guardrail"
    elif not qc_passed:
        veto_reason = "Orchestrator run did not pass final_arbiter (qc_passed=false)"

    return HangoutGuardrailResult(
        passed=passed,
        method="orchestrator_run",
        qc_passed=qc_passed,
        margin_approved=margin_approved,
        veto_reason=veto_reason,
        details={
            "audit_id": record.get("id"),
            "deal_id": meta.get("deal_id"),
            "agents_executed": meta.get("agents_executed"),
            "outcome": outcome,
        },
    )


def _lightweight_margin_check(
    root: Path,
    *,
    agent_id: str,
    revenue_usd: float,
    cost_usd: float,
    service_tier: str,
) -> tuple[bool, dict[str, Any]]:
    from arclya2a.agents.reputation import guardrail_strictness

    strict = guardrail_strictness(root, agent_id)
    multiplier = float(strict.get("margin_multiplier", 1.0))
    adjusted_cost = cost_usd * multiplier
    result = check_margin_guardrail(
        root,
        revenue_usd=revenue_usd,
        cost_usd=adjusted_cost,
        service_tier=service_tier,
    )
    details = {
        "margin_percent": result.margin_percent,
        "revenue_usd": result.revenue_usd,
        "cost_usd": result.cost_usd,
        "adjusted_cost_usd": adjusted_cost,
        "margin_multiplier": multiplier,
        "veto_reason": result.veto_reason,
    }
    return result.approved, details


def _lightweight_qc_check(
    *,
    message_count: int,
    close_confidence: float,
    min_close_confidence: float,
) -> tuple[bool, dict[str, Any]]:
    """Structural QC proxy — no LLM; requires negotiated context and confidence."""
    issues: list[str] = []
    if message_count < 1:
        issues.append("Deal room has no negotiation messages")
    if close_confidence < min_close_confidence:
        issues.append(f"Close confidence {close_confidence} below minimum {min_close_confidence}")
    passed = len(issues) == 0
    return passed, {"issues": issues, "message_count": message_count, "close_confidence": close_confidence}


def validate_hangout_guardrails(
    root: Path,
    *,
    agent_id: str,
    deal_id: str | None = None,
    handoff_run_id: str | None = None,
    revenue_usd: float | None = None,
    cost_usd: float | None = None,
    service_tier: str = "outreach_sequence",
    message_count: int = 0,
    close_confidence: float = 85.0,
    min_close_confidence: float = 85.0,
    payment_confirmed: bool = False,
) -> HangoutGuardrailResult:
    """
    Validate constitutional guardrails for a Hangout close.

    Prefers a completed orchestrator run (qc_passed + margin_approved). Falls back
    to a synchronous lightweight margin + structural QC check when economics are supplied.
    """
    orchestrator_record = find_handoff_chain_outcome(
        root,
        deal_id=deal_id,
        handoff_run_id=handoff_run_id,
    )
    if orchestrator_record:
        return _outcome_from_orchestrator_record(orchestrator_record)

    if revenue_usd is None or cost_usd is None:
        return HangoutGuardrailResult(
            passed=False,
            method="none",
            qc_passed=False,
            margin_approved=False,
            veto_reason=(
                "Lead routing commitment requires handoff_run_id, deal_id with a passed "
                "orchestrator run, or revenue_usd and cost_usd for lightweight guardrail check"
            ),
            details={"payment_confirmed": payment_confirmed},
        )

    margin_ok, margin_details = _lightweight_margin_check(
        root,
        agent_id=agent_id,
        revenue_usd=float(revenue_usd),
        cost_usd=float(cost_usd),
        service_tier=service_tier,
    )
    qc_ok, qc_details = _lightweight_qc_check(
        message_count=message_count,
        close_confidence=close_confidence,
        min_close_confidence=min_close_confidence,
    )
    passed = margin_ok and qc_ok
    veto_reason = None
    if not margin_ok:
        veto_reason = margin_details.get("veto_reason") or "Margin guardrail failed"
    elif not qc_ok:
        veto_reason = "; ".join(qc_details.get("issues") or ["QC check failed"])

    return HangoutGuardrailResult(
        passed=passed,
        method="lightweight_check",
        qc_passed=qc_ok,
        margin_approved=margin_ok,
        veto_reason=veto_reason,
        details={"margin": margin_details, "qc": qc_details, "payment_confirmed": payment_confirmed},
    )


def require_hangout_guardrails(
    root: Path,
    *,
    agent_id: str,
    context_label: str,
    **kwargs: Any,
) -> HangoutGuardrailResult:
    """Run guardrails and raise ValueError when constitutional requirements are not met."""
    result = validate_hangout_guardrails(root, agent_id=agent_id, **kwargs)
    if not result.passed:
        reason = result.veto_reason or "Constitutional guardrails not satisfied"
        raise ValueError(f"{context_label}: {reason}")
    return result