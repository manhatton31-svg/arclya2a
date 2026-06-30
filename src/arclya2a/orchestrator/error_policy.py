"""Predefined error-handling policies from agent registry."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from arclya2a.audit.logger import append_audit_record
from pathlib import Path

T = TypeVar("T")


class AgentExecutionError(Exception):
    """Agent turn failed after policy handling."""

    def __init__(self, message: str, *, policy: str, escalated: bool = False):
        super().__init__(message)
        self.policy = policy
        self.escalated = escalated


def execute_with_error_policy(
    policy: str,
    fn: Callable[[], T],
    *,
    root: Path,
    agent_id: str,
) -> T:
    """Run agent work under registry error_policy."""
    try:
        return fn()
    except Exception as first_error:
        append_audit_record(
            root,
            agent_id=agent_id,
            action="error_policy_triggered",
            reasoning=str(first_error),
            metadata={"policy": policy, "attempt": 1},
        )

        if policy == "retry_once_then_escalate":
            try:
                return fn()
            except Exception as retry_error:
                raise AgentExecutionError(
                    str(retry_error), policy=policy, escalated=True
                ) from retry_error

        if policy == "fail_closed_veto":
            raise AgentExecutionError(str(first_error), policy=policy, escalated=True) from first_error

        if policy == "reject_and_return":
            raise AgentExecutionError(str(first_error), policy=policy, escalated=False) from first_error

        if policy == "log_and_continue":
            append_audit_record(
                root,
                agent_id=agent_id,
                action="error_logged_continue",
                reasoning=str(first_error),
                metadata={"policy": policy},
            )
            raise AgentExecutionError(str(first_error), policy=policy, escalated=False) from first_error

        raise AgentExecutionError(str(first_error), policy=policy, escalated=True) from first_error


def handoff_from_policy_error(
    agent: dict[str, Any],
    error: AgentExecutionError,
) -> dict[str, Any]:
    """Build handoff payload when error policy exhausts."""
    policy = error.policy
    targets = agent.get("handoff_targets", [])

    if policy == "fail_closed_veto" or (policy == "retry_once_then_escalate" and error.escalated):
        return {
            "status": "EMERGENCY_STOP",
            "next_action": "halt_error_escalation",
            "ssot_updates": {"stage": "error_stopped"},
            "validation": {"confidence": 95, "check": f"Error policy {policy}: {error}"},
            "payload": {"error": str(error)},
        }

    if policy == "reject_and_return":
        return {
            "status": "COMPLETE",
            "next_action": "return_to_sender",
            "ssot_updates": {"stage": "rejected"},
            "validation": {"confidence": 40, "check": f"Rejected per {policy}: {error}"},
            "payload": {"error": str(error)},
        }

    if policy == "log_and_continue":
        return {
            "status": "COMPLETE",
            "next_action": targets[0] if targets else "continue",
            "ssot_updates": {"stage": "degraded_continue"},
            "validation": {"confidence": 50, "check": f"Degraded continue after {policy}"},
            "payload": {"error": str(error), "degraded": True},
        }

    return {
        "status": "EMERGENCY_STOP",
        "next_action": "halt_unknown_policy",
        "ssot_updates": {"stage": "error_stopped"},
        "validation": {"confidence": 90, "check": str(error)},
        "payload": {"error": str(error)},
    }