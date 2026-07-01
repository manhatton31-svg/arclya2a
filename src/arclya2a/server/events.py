"""Structured logging for key orchestration events."""

from __future__ import annotations

import logging
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.structured_log import log_event

logger = logging.getLogger("arclya2a.server")


def _request_snapshot(req: Any) -> dict[str, Any]:
    """Safe, loggable snapshot of an incoming handoff request."""
    return {
        "deal_id": req.deal_id,
        "customer_company": req.customer_company,
        "auto_route": req.auto_route,
        "entry_agent": req.entry_agent,
        "onboarding_complete": req.onboarding_complete,
        "lead_warmth": req.lead_warmth,
        "acquisition_stage": req.acquisition_stage,
        "has_product_profile": bool(req.product_profile),
        "has_initial_ssot": bool(req.initial_ssot),
        "task_context_preview": req.task_context[:160],
    }


def log_handoff_request_received(
    root,
    *,
    caller: dict[str, Any],
    request_snapshot: dict[str, Any],
    client_ip: str | None,
) -> None:
    log_event(
        logger,
        "handoff_request_received",
        client_id=caller.get("client_id"),
        caller_agent=caller.get("caller_agent"),
        ip=client_ip,
        deal_id=request_snapshot.get("deal_id"),
    )
    record_ops_event(
        root,
        "handoff_request_received",
        category="handoff",
        data={
            "deal_id": request_snapshot.get("deal_id"),
            "client_id": caller.get("client_id"),
        },
    )
    append_audit_record(
        root,
        agent_id=caller.get("caller_agent") or "external_agent",
        action="handoff_request_received",
        reasoning=f"Incoming handoff for deal {request_snapshot.get('deal_id')}",
        metadata={
            "client_id": caller.get("client_id"),
            "caller_agent": caller.get("caller_agent"),
            "client_ip": client_ip,
            "request": request_snapshot,
        },
    )


def log_handoff_chain_start(
    *,
    deal_id: str,
    auto_route: bool,
    entry_agent: str | None,
    task_context: str,
    caller: dict[str, Any] | None = None,
) -> None:
    log_event(
        logger,
        "handoff_chain_start",
        deal_id=deal_id,
        client_id=(caller or {}).get("client_id"),
        auto_route=auto_route,
        entry_agent=entry_agent,
    )


def log_handoff_chain_complete(
    root,
    *,
    deal_id: str,
    entry_agent: str | None,
    agents_executed: list[str],
    emergency_stop: bool,
    audit_ids: list[str],
    caller: dict[str, Any] | None = None,
    outcome_summary: dict[str, Any] | None = None,
) -> None:
    log_event(
        logger,
        "handoff_chain_complete",
        deal_id=deal_id,
        client_id=(caller or {}).get("client_id"),
        entry_agent=entry_agent,
        agents_executed=agents_executed,
        emergency_stop=emergency_stop,
        audit_count=len(audit_ids),
    )
    record_ops_event(
        root,
        "handoff_chain_complete",
        category="handoff",
        data={
            "deal_id": deal_id,
            "agents_executed": agents_executed,
            "emergency_stop": emergency_stop,
            "success": not emergency_stop,
        },
    )
    append_audit_record(
        root,
        agent_id="orchestrator",
        action="handoff_chain_complete",
        reasoning=f"Executed {len(agents_executed)} agents; emergency_stop={emergency_stop}",
        metadata={
            "deal_id": deal_id,
            "client_id": (caller or {}).get("client_id"),
            "caller_agent": (caller or {}).get("caller_agent"),
            "entry_agent": entry_agent,
            "agents_executed": agents_executed,
            "emergency_stop": emergency_stop,
            "audit_ids": audit_ids,
            "outcome": outcome_summary or {},
        },
    )


def log_handoff_chain_failed(
    root,
    *,
    deal_id: str,
    client_id: str | None,
    error: str,
) -> None:
    log_event(logger, "handoff_chain_failed", deal_id=deal_id, client_id=client_id, error=error)
    append_audit_record(
        root,
        agent_id="orchestrator",
        action="handoff_chain_failed",
        reasoning=error,
        metadata={"deal_id": deal_id, "client_id": client_id},
    )
    record_ops_event(
        root,
        "handoff_failed",
        category="handoff",
        data={"deal_id": deal_id, "error": error[:200]},
    )


def log_profile_saved(root, *, deal_id: str, agent_name: str, destination_cta: str | None) -> None:
    log_event(
        logger,
        "profile_saved",
        deal_id=deal_id,
        agent_name=agent_name,
        destination_cta=destination_cta,
    )
    append_audit_record(
        root,
        agent_id="onboarding_specialist",
        action="profile_saved",
        reasoning=f"Product profile saved for {agent_name}",
        metadata={"deal_id": deal_id, "agent_name": agent_name, "destination_cta": destination_cta},
    )


def log_deal_close(root, *, deal_id: str, close_type: str | None, cta_url: str | None) -> None:
    log_event(logger, "deal_close", deal_id=deal_id, close_type=close_type, cta_url=cta_url)
    append_audit_record(
        root,
        agent_id="closer",
        action="lead_routing_commitment",
        reasoning=f"Deal close recorded: {close_type}",
        metadata={"deal_id": deal_id, "close_type": close_type, "cta_url": cta_url},
    )


def build_handoff_summary(handoff_chain: list[dict[str, Any]], final_ssot: dict[str, Any]) -> dict[str, Any]:
    """Extract shareable summary fields from an orchestration result."""
    meta = final_ssot.get("metadata", {})
    summary: dict[str, Any] = {
        "agents_executed": [h.get("agent_id") for h in handoff_chain if h.get("agent_id")],
        "onboarding_complete": meta.get("onboarding_complete"),
        "profile_saved": bool(meta.get("product_profile_complete")),
        "destination_cta": meta.get("destination_cta"),
        "acquisition_stage": meta.get("acquisition_stage"),
    }

    for handoff in handoff_chain:
        agent_id = handoff.get("agent_id")
        payload = handoff.get("payload") or {}
        if agent_id == "closer":
            pkg = payload.get("close_package") or {}
            summary.update({
                "deal_closed": payload.get("deal_closed"),
                "lead_routing_confirmed": payload.get("lead_routing_confirmed"),
                "close_type": payload.get("close_type"),
                "cta_url": pkg.get("cta_url"),
            })
        if agent_id == "profit_guardrail":
            margin = payload.get("margin_check") or {}
            summary["margin_approved"] = margin.get("approved")
        if agent_id == "final_arbiter":
            qc = payload.get("qc_result") or {}
            summary["qc_passed"] = qc.get("passed")

    return summary