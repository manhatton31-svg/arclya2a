"""Route incoming agents to the correct entry agent in the A2A flow."""

from __future__ import annotations

from typing import Any


def is_onboarding_complete(ssot: dict[str, Any]) -> bool:
    """True when product profile is fully collected and marked complete."""
    meta = ssot.get("metadata", {})
    if meta.get("onboarding_complete") is True:
        return True
    if meta.get("product_profile_complete") is True:
        return True
    profile = meta.get("product_profile", {})
    required = [
        "agent_name", "product_name", "product_description", "target_customer",
        "typical_deal_size", "preferred_pricing_model", "destination_link",
    ]
    if not profile:
        return False
    if not all(profile.get(f) for f in required):
        return False
    objections = profile.get("common_objections", [])
    return isinstance(objections, list) and len(objections) >= 1


def is_warm_lead(ssot: dict[str, Any]) -> bool:
    """True when lead is marked warm and ready for closing."""
    meta = ssot.get("metadata", {})
    return meta.get("lead_warmth") == "warm" or ssot.get("stage") == "warm_lead"


def route_entry_agent(ssot: dict[str, Any], *, explicit: str | None = None) -> str:
    """
    Determine the first agent in a chain.

    Rules:
    - New / incomplete onboarding → onboarding_specialist
    - Onboarded + warm lead → closer
    - Onboarded + acquisition prospect → recruiter
    - Default fallback → outreach_worker (legacy outreach chain)
    """
    if explicit:
        return explicit

    if not is_onboarding_complete(ssot):
        return "onboarding_specialist"

    if is_warm_lead(ssot):
        return "closer"

    meta = ssot.get("metadata", {})
    if meta.get("acquisition_stage") in ("prospect", "invited", "recruiting"):
        return "recruiter"

    return "outreach_worker"


def resolve_flow_chain(
    agents: dict[str, Any],
    entry_agent: str,
    *,
    include_guardrails: bool = True,
) -> list[str]:
    """Build full chain from entry agent through registry handoff_targets."""
    from arclya2a.orchestrator.agent_runner import resolve_chain_from_registry

    chain = resolve_chain_from_registry(agents, entry_agent)
    if include_guardrails and entry_agent in ("onboarding_specialist", "closer", "recruiter", "outreach_worker"):
        tail = ["profit_guardrail", "final_arbiter"]
        for agent_id in tail:
            if agent_id not in chain and agent_id in agents:
                chain.append(agent_id)
    return chain