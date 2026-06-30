"""Multi-agent orchestration with Strong Handoff Protocol."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from arclya2a.orchestrator.agent_runner import resolve_chain_from_registry, run_registry_agent
from arclya2a.xai.client import XAIClient


@dataclass
class OrchestrationResult:
    handoff_chain: list[dict[str, Any]]
    final_ssot: dict[str, Any]
    audit_ids: list[str] = field(default_factory=list)
    cost_records: list[dict[str, Any]] = field(default_factory=list)
    emergency_stop: bool = False


class Orchestrator:
    """Runs multi-agent chains with SSOT, audit, and cost tracking."""

    def __init__(self, root: Path, xai_client: XAIClient | None = None):
        self.root = root
        self.xai_client = xai_client
        self._load_registry()

    def _load_registry(self) -> None:
        with open(self.root / "agents" / "registry.json", encoding="utf-8") as f:
            data = json.load(f)
        self.agents = {a["id"]: a for a in data["agents"]}
        for agent in data["agents"]:
            validate_role_card(agent["role_card"])

    def resolve_chain(self, start_agent: str = "outreach_worker") -> list[str]:
        """Resolve chain dynamically from registry handoff_targets."""
        return resolve_chain_from_registry(self.agents, start_agent)

    def run_chain(
        self,
        *,
        chain: list[str] | None = None,
        initial_ssot: dict[str, Any],
        task_context: str,
        revenue_usd: float = 49.0,
        estimated_cost_usd: float = 5.0,
    ) -> OrchestrationResult:
        """Execute a multi-agent handoff chain via registry dispatch."""
        if chain is None:
            chain = self.resolve_chain("outreach_worker")

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
            context = {
                "task_context": task_context,
                "handoff_id": handoff_id,
                "previous_handoff": previous_handoff,
                "revenue_usd": revenue_usd,
                "estimated_cost_usd": estimated_cost_usd,
                "memory_summary": build_memory_summary(ssot),
            }

            handoff = run_registry_agent(
                agent, ssot, self.root, context, xai_client=self.xai_client
            )
            handoff["agent_id"] = agent_id
            handoff["handoff_id"] = handoff_id
            handoff["timestamp"] = datetime.now(timezone.utc).isoformat()

            if not handoff.get("preference_handshake"):
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
                metadata={
                    "next_action": handoff.get("next_action"),
                    "model_tier": agent.get("model_tier"),
                    "inference": handoff.get("inference"),
                },
            )
            audit_ids.append(audit["id"])

            if previous_agent and previous_handoff:
                feedback = validate_structured_feedback(
                    _build_receiver_feedback(
                        sender_agent=previous_agent,
                        receiver_agent=agent_id,
                        handoff=handoff,
                    )
                )
                if feedback:
                    handoff["feedback"] = feedback

            if handoff.get("inference", {}).get("prompt_assembled"):
                cost_records.append(
                    {"agent_id": agent_id, "source": "xai_chat_completion", "recorded": True}
                )

            if handoff["status"] == "EMERGENCY_STOP":
                return OrchestrationResult(
                    handoff_chain=chain_results,
                    final_ssot=ssot,
                    audit_ids=audit_ids,
                    cost_records=cost_records,
                    emergency_stop=True,
                )

            previous_agent = agent_id
            previous_handoff = handoff

        return OrchestrationResult(
            handoff_chain=chain_results,
            final_ssot=ssot,
            audit_ids=audit_ids,
            cost_records=cost_records,
        )


def _build_receiver_feedback(
    *,
    sender_agent: str,
    receiver_agent: str,
    handoff: dict[str, Any],
) -> dict[str, Any]:
    """Structured feedback from receiver (current agent) back to sender."""
    conf = handoff.get("validation", {}).get("confidence", 0)
    severity = "info" if conf >= 70 else "warn"
    return {
        "from_agent": receiver_agent,
        "to_agent": sender_agent,
        "message": f"Received handoff with confidence {conf}",
        "severity": severity,
    }