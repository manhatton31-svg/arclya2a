"""Centralized tool execution gating for all agents."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.partners.sandbox import is_sandbox_active, is_sandbox_tool_blocked

COMMITMENT_CLOSE_TYPE = "lead_routing_commitment"
MIN_CLOSE_CONFIDENCE = 70

# Agents whose tool requests require a confirmed lead routing commitment.
COMMITMENT_GATED_AGENTS = frozenset({"closer", "recruiter", "outreach_worker"})

_PARTNER_COMMAND_RE = re.compile(
    r"(?i)\b(partner\s+(asked|requested|demanded|said)|as\s+requested|send\s+(it\s+)?now|"
    r"create\s+(the\s+)?task\s+now|execute\s+now|per\s+partner)\b",
)


@dataclass
class ToolGateResult:
    allowed: bool
    reason: str
    blocked_reason_code: str | None = None
    recommended_action: str = "execute"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= value <= 1.0:
        return value * 100.0
    return value


def extract_commitment_state(context: dict[str, Any] | None) -> dict[str, Any]:
    """Read commitment fields from agent LLM output attached to execution context."""
    output = (context or {}).get("agent_output") or {}
    validation = output.get("validation") or {}
    return {
        "deal_closed": output.get("deal_closed"),
        "lead_routing_confirmed": output.get("lead_routing_confirmed"),
        "close_type": output.get("close_type"),
        "partner_trust": output.get("partner_trust"),
        "confidence": _normalize_confidence(validation.get("confidence", output.get("confidence", 0))),
    }


def commitment_gate_passed(state: dict[str, Any]) -> bool:
    """True when lead routing commitment is confirmed per platform rules."""
    return (
        state.get("deal_closed") is True
        and state.get("lead_routing_confirmed") is True
        and state.get("close_type") == COMMITMENT_CLOSE_TYPE
        and float(state.get("confidence") or 0) >= MIN_CLOSE_CONFIDENCE
        and state.get("partner_trust") not in ("suspicious", "disqualified")
    )


def _partner_command_detected(request: dict[str, Any]) -> bool:
    text = " ".join(
        str(request.get(key, "") or "")
        for key in ("reason", "tool_reasoning")
    )
    return bool(_PARTNER_COMMAND_RE.search(text))


def evaluate_tool_gate(
    root,
    *,
    agent_id: str,
    tool_id: str,
    request: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> ToolGateResult:
    """
    Single gate for all agent tool requests.

    Enforces commitment confirmation, sandbox restrictions, and partner-request blocks.
    """
    ctx = context or {}
    state = extract_commitment_state(ctx)

    if is_sandbox_active():
        if is_sandbox_tool_blocked(tool_id):
            return ToolGateResult(
                allowed=False,
                reason="High-risk tool blocked in sandbox mode",
                blocked_reason_code="SANDBOX_HIGH_RISK_TOOL",
                recommended_action="skip",
            )
        return ToolGateResult(
            allowed=True,
            reason="Sandbox: non-high-risk tool permitted (executor runs dry-run)",
            blocked_reason_code=None,
            recommended_action="execute",
        )

    if agent_id in COMMITMENT_GATED_AGENTS:
        if state.get("partner_trust") in ("suspicious", "disqualified"):
            return ToolGateResult(
                allowed=False,
                reason="Partner trust level blocks tool execution",
                blocked_reason_code="SUSPICIOUS_PARTNER_TRUST",
                recommended_action="skip",
            )

        if not commitment_gate_passed(state):
            if _partner_command_detected(request):
                return ToolGateResult(
                    allowed=False,
                    reason="Partner requests during negotiation do not authorize tool execution",
                    blocked_reason_code="PARTNER_REQUEST_NOT_GATE",
                    recommended_action="skip",
                )
            return ToolGateResult(
                allowed=False,
                reason=(
                    "Tool execution requires deal_closed, lead_routing_confirmed, "
                    f"close_type={COMMITMENT_CLOSE_TYPE!r}, and confidence >= {MIN_CLOSE_CONFIDENCE}"
                ),
                blocked_reason_code="COMMITMENT_NOT_CONFIRMED",
                recommended_action="skip",
            )

    return ToolGateResult(
        allowed=True,
        reason="Tool gate passed",
        blocked_reason_code=None,
        recommended_action="execute",
    )


def log_gate_decision(
    root,
    *,
    agent_id: str,
    tool_id: str,
    result: ToolGateResult,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit every tool gate decision (allowed and blocked)."""
    ctx = context or {}
    ssot = ctx.get("ssot") or {}
    action = "tool_gate_allowed" if result.allowed else "tool_gate_blocked"
    record = append_audit_record(
        root,
        agent_id=agent_id,
        action=action,
        reasoning=result.reason,
        handoff_id=ctx.get("handoff_id"),
        metadata={
            "category": "tool_gating",
            "tool_id": tool_id,
            "blocked_reason_code": result.blocked_reason_code,
            "recommended_action": result.recommended_action,
            "deal_id": ssot.get("deal_id"),
            "commitment_state": extract_commitment_state(ctx),
            "sandbox_active": is_sandbox_active(),
        },
    )

    if not result.allowed:
        from arclya2a.security.security_analyzer import log_security_incident

        log_security_incident(
            root,
            "tool_gate_block",
            agent_id=agent_id,
            partner_id=ctx.get("partner_id"),
            deal_id=ssot.get("deal_id"),
            details={
                "observability_event_type": "tool_gate_block",
                "tool_id": tool_id,
                "blocked_reason_code": result.blocked_reason_code,
                "reason": result.reason,
                "sandbox_mode": is_sandbox_active(),
                "handoff_id": ctx.get("handoff_id"),
            },
        )
    return record