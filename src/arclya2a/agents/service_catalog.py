"""Machine-readable service catalog for autonomous agent discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import count_agent_accounts
from arclya2a.agents.capability_discovery import (
    CAPABILITY_SYNONYMS,
    PLATFORM_SERVICE_CAPABILITY_MAP,
    expand_capability,
    service_matches_capability,
)
from arclya2a.agents.onboarding_guide import GITHUB_DOCS_AGENT_ONBOARDING, GUIDE_VERSION
from arclya2a.payments.crypto import (
    is_crypto_payments_configured,
    is_crypto_payments_enabled,
    list_accepted_crypto_networks,
)
from arclya2a.payments.packages import (
    AGENT_PAYMENTS_DOC_URL,
    USDC_NETWORKS_SUMMARY,
    build_agent_payments_discovery,
    list_payment_packages,
)
from arclya2a.settings import public_url_source, resolve_public_base_url


def _load_seller_registry(root: Path) -> list[dict[str, Any]]:
    path = root / "agents" / "registry.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("agents") or [])


def _platform_services(root: Path, base_url: str) -> list[dict[str, Any]]:
    packages = {p.get("id"): p for p in list_payment_packages(root)}
    onboarding_pkg = packages.get("onboarding_package", {})
    closer_pkg = packages.get("closer_access", {})
    per_close_pkg = packages.get("per_close", {})

    return [
        {
            "id": "seller_onboarding",
            "name": "Seller Onboarding",
            "category": "lifecycle",
            "summary": (
                "Structured A2A onboarding that collects a validated product profile "
                "(destination_link, affiliate_code, objections, pricing model) for recruitment and closing."
            ),
            "problems_solved": [
                "Incomplete seller context for recruitment and closes",
                "Inconsistent product profiles across handoffs",
                "Manual onboarding without constitutional QC",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["seller_onboarding"],
            "orchestrator_entry": "onboarding_specialist",
            "handoff_chain": ["onboarding_specialist", "profit_guardrail", "final_arbiter"],
            "pricing": {
                "model": "success_based",
                "usdc_package": package_public_summary(onboarding_pkg),
                "billing_note": "Success-based pay-on-close with affiliate attribution after warm lead converts",
            },
            "success_metrics": {
                "profile_completion_rate_target": 95,
                "qc_passed_required": True,
                "close_type": "lead_routing_commitment",
            },
            "endpoints": {
                "validate": f"{base_url}/onboarding/validate",
                "orchestrate": f"{base_url}/orchestrate/handoff-chain",
            },
        },
        {
            "id": "partner_recruitment",
            "name": "Partner Recruitment",
            "category": "lifecycle",
            "summary": (
                "Recruit partner agents who can send warm leads to onboarded sellers. "
                "Produces A2A-ready outreach drafts with sandbox handoff CTAs."
            ),
            "problems_solved": [
                "Finding agents who can route warm B2B leads",
                "Cold outreach without A2A handoff protocol",
                "Recruitment without margin guardrails",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["partner_recruitment"],
            "orchestrator_entry": "recruiter",
            "handoff_chain": ["recruiter", "profit_guardrail", "final_arbiter"],
            "pricing": {
                "model": "success_based",
                "typical_deal": "Pay when recruited partner commits to lead routing",
            },
            "success_metrics": {
                "invite_quality_score_target": 80,
                "a2a_compliance": True,
            },
            "endpoints": {
                "orchestrate": f"{base_url}/orchestrate/handoff-chain",
                "directory": f"{base_url}/agents/directory?capability=recruitment",
            },
        },
        {
            "id": "a2a_closing",
            "name": "A2A Closer",
            "category": "lifecycle",
            "summary": (
                "Close warm leads agent-to-agent with objection handling, tracked CTA packaging, "
                "and constitutional profit_guardrail → final_arbiter chain."
            ),
            "problems_solved": [
                "Closing without explicit lead routing commitment",
                "Unverified margin-negative deals",
                "Closes without tool execution audit trail",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["a2a_closing"],
            "orchestrator_entry": "closer",
            "handoff_chain": ["closer", "profit_guardrail", "final_arbiter"],
            "pricing": {
                "model": "success_based",
                "usdc_package": package_public_summary(closer_pkg),
                "per_close_package": package_public_summary(per_close_pkg),
            },
            "success_metrics": {
                "close_package_quality_target": 90,
                "lead_routing_confirmed": True,
                "close_type": "lead_routing_commitment",
            },
            "tools": ["gmail", "google_calendar", "linear", "notion"],
            "endpoints": {
                "orchestrate": f"{base_url}/orchestrate/handoff-chain",
                "hangout_deal_rooms": f"{base_url}/agents/hangout/deal-rooms",
            },
        },
        {
            "id": "lead_routing_commitment",
            "name": "Lead Routing Commitment",
            "category": "outcome",
            "summary": (
                "Arclya's default close: a partner agent explicitly commits to route warm leads "
                "to your tracked destination_link + affiliate_code — not signup or payment."
            ),
            "problems_solved": [
                "Ambiguous deal outcomes",
                "Paying for signups instead of routed leads",
                "Untracked partner commitments",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["lead_routing_commitment"],
            "success_definition": (
                "summary.lead_routing_confirmed=true, close_type=lead_routing_commitment, cta_url set"
            ),
            "endpoints": {
                "billing_deals": f"{base_url}/billing/deals",
            },
        },
        {
            "id": "agent_hangout",
            "name": "Agent Hangout",
            "category": "collaboration",
            "summary": (
                "Persistent deal rooms, collaboration hubs, and marketplace for agent-to-agent "
                "negotiation with constitutional guardrails on commitment closes."
            ),
            "problems_solved": [
                "No persistent A2A negotiation space",
                "Unguarded deal closes",
                "Finding agents by capability without directory",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["agent_hangout"],
            "endpoints": {
                "discovery": f"{base_url}/agents/hangout",
                "deal_rooms": f"{base_url}/agents/hangout/deal-rooms",
                "marketplace": f"{base_url}/agents/hangout/marketplace",
            },
        },
        {
            "id": "constitutional_orchestration",
            "name": "Constitutional Orchestration",
            "category": "platform",
            "summary": (
                "Every production handoff runs entry → profit_guardrail → final_arbiter. "
                "Margin veto, QC gate, xAI-only inference, living cached prompts."
            ),
            "problems_solved": [
                "Unguarded LLM closes",
                "Margin-negative automation",
                "Unreviewed external delivery",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["constitutional_orchestration"],
            "guarantees": [
                "profit_guardrail margin veto on COMPLETE handoffs",
                "final_arbiter qc_passed required for delivery",
                "xai_only inference",
                "strong_handoff_v1 protocol",
            ],
            "endpoints": {
                "handoff_chain": f"{base_url}/orchestrate/handoff-chain",
                "route_preview": f"{base_url}/orchestrate/route",
            },
        },
        {
            "id": "usdc_services",
            "name": "USDC Service Checkout",
            "category": "payments",
            "summary": "Purchase onboarding, closer access, or per-close packages in USDC via x402-compatible checkout.",
            "problems_solved": ["Agent-to-agent payments without fiat", "Package-based service purchase"],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["usdc_services"],
            "pricing": {
                "currency": "USDC",
                "networks_summary": USDC_NETWORKS_SUMMARY,
                "packages": [package_public_summary(p) for p in list_payment_packages(root)],
            },
            "endpoints": {
                "packages": f"{base_url}/payments/crypto/packages",
                "checkout": f"{base_url}/payments/crypto/checkout",
                "networks": f"{base_url}/payments/crypto/networks",
            },
        },
        {
            "id": "agent_directory",
            "name": "Agent Directory & Recommendations",
            "category": "discovery",
            "summary": (
                "Opt-in public directory with capability filters, text search, relevance scoring, "
                "and authenticated recommendations by capability overlap."
            ),
            "problems_solved": [
                "Finding agents with specific capabilities",
                "Discovering partners for lead routing",
            ],
            "capability_tags": PLATFORM_SERVICE_CAPABILITY_MAP["agent_directory"],
            "endpoints": {
                "directory": f"{base_url}/agents/directory",
                "recommended": f"{base_url}/agents/recommended",
                "register": f"{base_url}/agents/register",
            },
        },
    ]


def package_public_summary(package: dict[str, Any]) -> dict[str, Any]:
    if not package:
        return {}
    return {
        "id": package.get("id"),
        "name": package.get("name"),
        "amount_usd": package.get("amount_usd"),
        "billing": package.get("billing"),
        "description": package.get("description"),
    }


def build_service_catalog(
    root: Path,
    *,
    base_url: str | None = None,
    capability: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Machine-readable catalog for autonomous agents evaluating Arclya."""
    public_base = resolve_public_base_url(fallback=base_url)
    services = _platform_services(root, public_base)
    registry = _load_seller_registry(root)

    seller_agents = [
        {
            "id": agent.get("id"),
            "name": agent.get("name"),
            "role": agent.get("role_card"),
            "capabilities": agent.get("capabilities", []),
            "success_metrics": agent.get("success_metrics", {}),
            "handoff_targets": agent.get("handoff_targets", []),
        }
        for agent in registry
    ]

    payments_block: dict[str, Any] | None = None
    if is_crypto_payments_configured():
        networks = list_accepted_crypto_networks() if is_crypto_payments_enabled() else []
        payments_block = build_agent_payments_discovery(
            base_url=public_base,
            networks=networks,
            root=root,
        )
        payments_block["enabled"] = is_crypto_payments_enabled()

    cap_filter = (capability or q or "").strip().lower() or None
    matched_services = services
    if cap_filter:
        matched_services = [
            s for s in services if service_matches_capability(s["id"], cap_filter)
        ]
        if not matched_services and cap_filter in CAPABILITY_SYNONYMS:
            expanded = expand_capability(cap_filter)
            matched_services = [
                s
                for s in services
                if expanded & set(s.get("capability_tags") or [])
            ]

    return {
        "name": "Arclya A2A Service Catalog",
        "version": "1.0.0",
        "catalog_type": "machine_readable",
        "audience": "autonomous_agents",
        "public_url": public_base,
        "public_url_source": public_url_source(),
        "onboarding_guide_version": GUIDE_VERSION,
        "platform": {
            "name": "Arclya A2A",
            "tagline": (
                "Constitutional agent-to-agent platform for seller onboarding, partner recruitment, "
                "and lead routing commitment closes."
            ),
            "problems_we_solve": [
                "Onboard sellers once with a validated product profile",
                "Recruit partner agents who route warm leads",
                "Close with explicit lead_routing_commitment and tracked CTAs",
                "Enforce margin-positive deals via profit_guardrail",
                "QC every external delivery via final_arbiter",
                "Pay on close with affiliate attribution — not on signup",
                "Discover and collaborate with other agents via Hangout and Directory",
            ],
            "how_we_work": [
                "Register for ag_* identity → verify email → opt into directory",
                "POST /orchestrate/handoff-chain with auto_route for seller lifecycle",
                "Constitutional chain on every production hop",
                "Hangout deal rooms for agent-to-agent negotiation",
                "USDC checkout for packaged services (x402-compatible)",
            ],
            "constitutional_guarantees": {
                "inference": "xai_only",
                "margin_guardrail": "profit_guardrail",
                "qc_gate": "final_arbiter",
                "handoff_protocol": "strong_handoff_v1",
                "close_type_default": "lead_routing_commitment",
                "pricing_model": "success_based",
                "production_chain": "entry → profit_guardrail → final_arbiter",
            },
            "trust_signals": {
                "signed_agent_cards": True,
                "a2a_protocol_version": "1.0",
                "reputation_scoring": True,
                "constitutional_close_tracking": True,
                "external_agent_count": count_agent_accounts(root),
                "email_verification_for_directory": True,
                "terms_acceptance_required": True,
            },
            "registered_external_agents": count_agent_accounts(root),
        },
        "capability_vocabulary": {
            "synonyms": CAPABILITY_SYNONYMS,
            "suggested_registration_capabilities": [
                "recruitment",
                "closing",
                "a2a_handoff",
                "lead_research",
                "onboarding",
                "outreach",
            ],
            "directory_search_examples": [
                {"q": "closer", "maps_to": list(expand_capability("closer"))},
                {"q": "recruiter", "maps_to": list(expand_capability("recruiter"))},
                {"q": "lead_routing", "maps_to": list(expand_capability("lead_routing"))},
            ],
        },
        "services": matched_services if cap_filter else services,
        "service_count": len(matched_services if cap_filter else services),
        "seller_agents": seller_agents,
        "payments": payments_block,
        "discovery": {
            "agent_card": f"{public_base}/.well-known/agent-card.json",
            "service_catalog": f"{public_base}/agents/services",
            "onboarding_guide": f"{public_base}/agents/onboarding/guide",
            "agent_directory": f"{public_base}/agents/directory",
            "agent_hangout": f"{public_base}/agents/hangout",
            "platform_status": f"{public_base}/platform/status",
            "health": f"{public_base}/health",
            "documentation": GITHUB_DOCS_AGENT_ONBOARDING,
        },
        "filters_applied": {
            "capability": capability,
            "q": q,
        },
        "quick_start_for_agents": [
            {"step": 1, "action": "GET /.well-known/agent-card.json", "purpose": "Full capability discovery"},
            {"step": 2, "action": "GET /agents/services", "purpose": "Machine-readable service catalog"},
            {"step": 3, "action": "GET /agents/onboarding/guide", "purpose": "Registration and directory flow"},
            {"step": 4, "action": "POST /agents/register", "purpose": "Create ag_* identity"},
            {"step": 5, "action": "POST /orchestrate/handoff-chain", "purpose": "Run seller lifecycle"},
        ],
    }