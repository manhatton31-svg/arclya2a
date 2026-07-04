"""Capability synonyms and discovery helpers for Agent Directory search."""

from __future__ import annotations

# Maps common agent search terms to platform/directory capability tokens.
CAPABILITY_SYNONYMS: dict[str, list[str]] = {
    "closer": ["closing", "a2a_closing", "objection_handling", "lead_routing_commitment"],
    "closing": ["a2a_closing", "closer", "objection_handling"],
    "a2a_closing": ["closing", "closer", "lead_routing_commitment"],
    "recruiter": ["recruitment", "agent_recruitment", "a2a_acquisition", "partner_recruitment"],
    "recruitment": ["agent_recruitment", "recruiter", "a2a_acquisition"],
    "agent_recruitment": ["recruitment", "recruiter"],
    "lead_routing": ["lead_routing_commitment", "a2a_handoff", "lead_research", "closing"],
    "lead_routing_commitment": ["lead_routing", "a2a_handoff", "closing"],
    "a2a_handoff": ["lead_routing", "lead_routing_commitment", "tool_use"],
    "onboarding": ["product_profile_collection", "seller_onboarding"],
    "seller_onboarding": ["onboarding", "product_profile_collection"],
    "outreach": ["draft_outreach"],
    "lead_research": ["lead_routing"],
    "objection_handling": ["closing", "a2a_closing", "closer"],
    "tool_use": ["a2a_handoff", "gmail", "linear", "notion"],
    "constitutional": ["constitutional_guardrails", "margin_check", "quality_control"],
    "guardrails": ["constitutional_guardrails", "margin_check", "profit_guardrail"],
    "hangout": ["deal_rooms", "agent_marketplace", "collaboration_hubs"],
    "deal_rooms": ["hangout", "lead_routing_commitment"],
    "marketplace": ["agent_marketplace", "hangout"],
    "reputation": ["reputation_trust_scoring", "trust_score"],
    "crypto": ["crypto_payments", "usdc_checkout", "x402"],
    "x402": ["crypto_payments", "usdc_checkout"],
}

# Platform service ids matched when agents search by capability.
PLATFORM_SERVICE_CAPABILITY_MAP: dict[str, list[str]] = {
    "seller_onboarding": ["onboarding", "seller_onboarding", "product_profile_collection"],
    "partner_recruitment": ["recruitment", "recruiter", "agent_recruitment", "a2a_acquisition"],
    "a2a_closing": ["closing", "closer", "a2a_closing", "objection_handling", "lead_routing"],
    "lead_routing_commitment": ["lead_routing", "lead_routing_commitment", "a2a_handoff"],
    "agent_hangout": ["hangout", "deal_rooms", "marketplace", "collaboration_hubs"],
    "constitutional_orchestration": ["constitutional", "guardrails", "margin_check"],
    "usdc_services": ["crypto", "x402", "usdc_checkout", "crypto_payments"],
    "agent_directory": ["directory", "discovery", "reputation"],
}


def expand_capability_one_way(capability: str) -> set[str]:
    """Map a filter/query token to itself plus direct synonyms (no reverse inference)."""
    raw = str(capability or "").strip().lower()
    if not raw:
        return set()
    return {raw, *(s.lower() for s in CAPABILITY_SYNONYMS.get(raw, []))}


def expand_capability(capability: str) -> set[str]:
    """Bidirectional expansion for search scoring (maps related terms)."""
    raw = str(capability or "").strip().lower()
    if not raw:
        return set()
    expanded = expand_capability_one_way(raw)
    for key, synonyms in CAPABILITY_SYNONYMS.items():
        if raw == key or raw in synonyms:
            expanded.add(key)
            expanded.update(s.lower() for s in synonyms)
    return expanded


def expand_capability_filters(capabilities: list[str]) -> list[str]:
    """Flatten capability filters with synonyms (deduped, stable order)."""
    seen: list[str] = []
    for cap in capabilities:
        for token in sorted(expand_capability(cap)):
            if token not in seen:
                seen.append(token)
    return seen


SEARCH_CAPABILITY_RELATIONS: dict[str, list[str]] = {
    "outreach": ["lead_research"],
    "lead_research": ["outreach"],
    "recruitment": ["a2a_acquisition"],
    "closing": ["objection_handling"],
}


def query_expands_to_capabilities(query: str) -> set[str]:
    """Map a free-text search query to related capability tokens (search scoring only)."""
    q = (query or "").strip().lower()
    if not q:
        return set()
    hits = expand_capability(q)
    for token in q.replace("-", "_").split():
        if len(token) >= 3:
            hits |= expand_capability(token)
            hits.update(SEARCH_CAPABILITY_RELATIONS.get(token, []))
    hits.update(SEARCH_CAPABILITY_RELATIONS.get(q, []))
    return hits


def service_matches_capability(service_id: str, capability: str) -> bool:
    """Whether a platform service id matches a capability search term."""
    expanded = expand_capability(capability)
    service_caps = set(PLATFORM_SERVICE_CAPABILITY_MAP.get(service_id, []))
    for cap in expanded:
        service_caps |= expand_capability(cap)
    return bool(expanded & service_caps) or capability.lower() in service_caps