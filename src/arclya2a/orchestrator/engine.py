"""Multi-agent orchestration with Strong Handoff Protocol."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from arclya2a.audit.logger import append_audit_record
from arclya2a.handoff.validators import (
    HandoffValidationError,
    build_memory_summary,
    merge_ssot,
    validate_handoff,
    validate_preference_handshake,
    validate_role_card,
    validate_structured_feedback,
)
from arclya2a.pricing.margin import check_margin_guardrail
from arclya2a.xai.client import XAIClient, select_model, assemble_prompt


AgentRunner = Callable[[dict[str, Any], dict[str, Any], Path, dict[str, Any]], dict[str, Any]]


@dataclass
class OrchestrationResult:
    handoff_chain: list[dict[str, Any]]
    final_ssot: dict[str, Any]
    audit_ids: list[str] = field(default_factory=list)
    cost_records: list[dict[str, Any]] = field(default_factory=list)
    emergency_stop: bool = False


class Orchestrator:
    """Runs multi-agent chains with SSOT, audit, and cost tracking."""

    def __init__(self, root: Path):
        self.root = root
        self._load_registry()

    def _load_registry(self) -> None:
        with open(self.root / "agents" / "registry.json", encoding="utf-8") as f:
            data = json.load(f)
        self.agents = {a["id"]: a for a in data["agents"]}
        for agent in data["agents"]:
            validate_role_card(agent["role_card"])

    def run_chain(
        self,
        *,
        chain: list[str],
        initial_ssot: dict[str, Any],
        task_context: str,
        revenue_usd: float = 49.0,
        estimated_cost_usd: float = 5.0,
    ) -> OrchestrationResult:
        """Execute a multi-agent handoff chain."""
        handoff_id = str(uuid.uuid4())
        ssot = dict(initial_ssot)
        chain_results: list[dict[str, Any]] = []
        audit_ids: list[str] = []
        cost_records: list[dict[str, Any]] = []
        previous_agent: str | None = None
        previous_handoff: dict[str, Any] | None = None

        for agent_id in chain:
            if agent_id not in self.agents:
                raise HandoffValidationError(f"Unknown agent: {agent_id}")

            agent = self.agents[agent_id]
            runner = AGENT_RUNNERS.get(agent_id)
            if not runner:
                raise HandoffValidationError(f"No runner for agent: {agent_id}")

            context = {
                "task_context": task_context,
                "handoff_id": handoff_id,
                "previous_handoff": previous_handoff,
                "revenue_usd": revenue_usd,
                "estimated_cost_usd": estimated_cost_usd,
            }

            handoff = runner(agent, ssot, self.root, context)
            handoff["agent_id"] = agent_id
            handoff["handoff_id"] = handoff_id
            handoff["timestamp"] = datetime.now(timezone.utc).isoformat()

            if "preference_handshake" not in handoff:
                handoff["preference_handshake"] = validate_preference_handshake(None)

            handoff["ssot"] = merge_ssot(ssot, handoff.pop("ssot_updates", None))
            handoff["memory_summary"] = build_memory_summary(handoff["ssot"])
            ssot = handoff["ssot"]

            validate_handoff(handoff)
            chain_results.append(handoff)

            audit = append_audit_record(
                self.root,
                agent_id=agent_id,
                action=f"handoff_{handoff['status']}",
                reasoning=handoff.get("validation", {}).get("check", ""),
                handoff_id=handoff_id,
                metadata={"next_action": handoff.get("next_action")},
            )
            audit_ids.append(audit["id"])

            if previous_agent and previous_handoff:
                feedback = validate_structured_feedback(
                    _build_receiver_feedback(previous_agent, agent_id, handoff)
                )
                if feedback:
                    handoff["feedback"] = feedback

            if handoff["status"] == "EMERGENCY_STOP":
                return OrchestrationResult(
                    handoff_chain=chain_results,
                    final_ssot=ssot,
                    audit_ids=audit_ids,
                    cost_records=cost_records,
                    emergency_stop=True,
                )

            cost_record = _record_agent_cost(self.root, agent_id, agent.get("model_tier", "economy"))
            cost_records.append(cost_record)

            previous_agent = agent_id
            previous_handoff = handoff

        return OrchestrationResult(
            handoff_chain=chain_results,
            final_ssot=ssot,
            audit_ids=audit_ids,
            cost_records=cost_records,
        )


def _build_receiver_feedback(
    from_agent: str, to_agent: str, handoff: dict[str, Any]
) -> dict[str, Any]:
    conf = handoff.get("validation", {}).get("confidence", 0)
    severity = "info" if conf >= 70 else "warn"
    return {
        "from_agent": to_agent,
        "to_agent": from_agent,
        "message": f"Received handoff with confidence {conf}",
        "severity": severity,
    }


def _record_agent_cost(root: Path, agent_id: str, model_tier: str) -> dict[str, Any]:
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        core = json.load(f)
    model = select_model(model_tier, core)
    client = XAIClient(root)
    return client.record_cost(
        agent_id=agent_id,
        model=model,
        input_tokens=500,
        output_tokens=200,
        cached_input_tokens=400,
    )


def run_outreach_worker(
    agent: dict[str, Any], ssot: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Outreach worker agent turn."""
    draft = {
        "subject": f"Partnership opportunity for {ssot.get('customer', {}).get('company', 'your team')}",
        "body": f"Hi — tailored outreach for deal {ssot.get('deal_id')}. {context['task_context']}",
    }
    return {
        "status": "COMPLETE",
        "next_action": "handoff_to_profit_guardrail",
        "payload": {"draft": draft},
        "ssot_updates": {
            "stage": "draft_ready",
            "summary": f"Draft created: {draft['subject']}",
            "metadata": {"draft": draft},
        },
        "validation": {"confidence": 78, "check": "Draft includes CTA and personalization"},
        "preference_handshake": {"format": "json", "accepted": True},
    }


def run_profit_guardrail(
    agent: dict[str, Any], ssot: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Profit Guardrail with veto power."""
    result = check_margin_guardrail(
        root,
        revenue_usd=context["revenue_usd"],
        cost_usd=context["estimated_cost_usd"],
    )
    if not result.approved:
        return {
            "status": "EMERGENCY_STOP",
            "next_action": "halt_deal_margin_violation",
            "payload": {"margin_check": result.__dict__},
            "ssot_updates": {"stage": "margin_vetoed"},
            "validation": {"confidence": 95, "check": result.veto_reason or "Vetoed"},
        }
    return {
        "status": "COMPLETE",
        "next_action": "handoff_to_final_arbiter",
        "payload": {"margin_check": result.__dict__},
        "ssot_updates": {"stage": "margin_approved", "metadata": {"margin_percent": result.margin_percent}},
        "validation": {"confidence": 92, "check": f"Margin {result.margin_percent:.1f}% approved"},
    }


def run_final_arbiter(
    agent: dict[str, Any], ssot: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Final Arbiter QC gate."""
    draft = ssot.get("metadata", {}).get("draft", {})
    issues = []
    if not draft.get("subject"):
        issues.append("Missing subject line")
    if not draft.get("body"):
        issues.append("Missing body")
    passed = len(issues) == 0
    return {
        "status": "COMPLETE",
        "next_action": "deliver_to_customer" if passed else "return_to_outreach_worker",
        "payload": {"qc_result": {"passed": passed, "issues": issues}, "draft": draft},
        "ssot_updates": {"stage": "qc_passed" if passed else "qc_failed"},
        "validation": {"confidence": 94 if passed else 55, "check": "QC gate review complete"},
    }


AGENT_RUNNERS: dict[str, AgentRunner] = {
    "outreach_worker": run_outreach_worker,
    "profit_guardrail": run_profit_guardrail,
    "final_arbiter": run_final_arbiter,
}