"""A2A Agent Card builder with platform capabilities and documentation links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arclya2a.payments.crypto import (
    is_crypto_payments_configured,
    is_crypto_payments_enabled,
    list_accepted_crypto_networks,
)
from arclya2a.payments.packages import (
    AGENT_PAYMENTS_DOC_URL,
    build_agent_payments_discovery,
)

GITHUB_DOCS_BASE = "https://github.com/manhatton31-svg/arclya2a/blob/master/docs"


def build_agent_card(*, root: Path, base_url: str, version: str, platform_name: str) -> dict[str, Any]:
    """Build A2A-compliant AgentCard with current platform capabilities."""
    with open(root / "agents" / "registry.json", encoding="utf-8") as f:
        registry = json.load(f)

    skills = [
        {
            "id": a["id"],
            "name": a["name"],
            "description": a["role_card"],
            "tags": a.get("capabilities", []),
        }
        for a in registry["agents"]
    ]

    payments_block: dict[str, Any] | None = None
    if is_crypto_payments_configured():
        networks = list_accepted_crypto_networks() if is_crypto_payments_enabled() else []
        payments_block = build_agent_payments_discovery(
            base_url=base_url,
            networks=networks,
            root=root,
        )
        payments_block["enabled"] = is_crypto_payments_enabled()

    platform_block: dict[str, Any] = {
        "phase": "1",
        "close_type": "lead_routing_commitment",
        "pricing_model": "success_based",
        "billing": "pay_on_close_with_affiliate_attribution",
        "features": [
            "seller_onboarding",
            "product_profile_validation",
            "partner_recruitment",
            "a2a_closing",
            "tool_execution",
            "constitutional_guardrails",
            "success_based_billing",
            "crypto_payments",
            "usdc_checkout",
            "background_learning_loop",
            "prompt_patch_review",
        ],
        "lifecycle": ["onboarding", "recruitment", "close"],
        "success_definition": (
            "Partner agent commits to route warm leads to the seller's tracked destination_link "
            "(destination_link + affiliate_code CTA). Deal closes on lead_routing_commitment, "
            "not on signup or payment."
        ),
    }
    if payments_block is not None:
        platform_block["payments"] = payments_block

    card: dict[str, Any] = {
        "name": platform_name,
        "description": (
            "Arclya A2A is a constitutional agent-to-agent platform for seller onboarding, "
            "partner recruitment, and lead routing commitment closes. Sellers onboard with a "
            "validated product profile, recruit partner agents for warm leads, and close with "
            "tracked CTAs. Pricing is success-based (pay-on-close) with affiliate attribution. "
            "The Closer can execute Gmail, Linear, Calendar, and Notion tools. A background "
            "learning loop analyzes execution data and applies safe prompt improvements. "
            "Agents can pay for services in USDC on Base, Ethereum, Solana, or BSC via "
            "self-service package checkout (x402-compatible)."
        ),
        "url": base_url,
        "version": version,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "platform": platform_block,
        "skills": skills,
        "authentication": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Arclya-Key",
            "alternate": "Authorization: Bearer <ARCLYA_API_KEY>",
        },
        "documentation": [
            {
                "rel": "test-partner-checklist",
                "type": "markdown",
                "title": "Test Partner Onboarding Checklist",
                "href": f"{GITHUB_DOCS_BASE}/test-partner-onboarding-checklist.md",
            },
            {
                "rel": "partnership-model",
                "type": "markdown",
                "title": "Partnership Model One-Pager",
                "href": f"{GITHUB_DOCS_BASE}/partnership-model-one-pager.md",
            },
            {
                "rel": "partner-outreach",
                "type": "markdown",
                "title": "Partner Outreach Value Proposition",
                "href": f"{GITHUB_DOCS_BASE}/partner-outreach-value-proposition.md",
            },
            {
                "rel": "partner-integration-guide",
                "type": "markdown",
                "title": "Partner Integration Guide",
                "href": f"{GITHUB_DOCS_BASE}/partner-integration-guide.md",
            },
            {
                "rel": "external-agent-integration",
                "type": "markdown",
                "title": "External Agent Integration (API reference)",
                "href": f"{GITHUB_DOCS_BASE}/external-agent-integration.md",
            },
            {
                "rel": "health",
                "type": "api",
                "title": "Health check",
                "href": f"{base_url}/health",
            },
            {
                "rel": "status",
                "type": "api",
                "title": "Operational status",
                "href": f"{base_url}/status",
            },
            {
                "rel": "ops-dashboard",
                "type": "api",
                "title": "Operational dashboard",
                "href": f"{base_url}/ops/dashboard",
            },
            {
                "rel": "onboarding-validate",
                "type": "api",
                "title": "Validate product profile before onboarding",
                "href": f"{base_url}/onboarding/validate",
            },
            {
                "rel": "sandbox-register",
                "type": "api",
                "title": "Self-service sandbox API key for test partners",
                "href": f"{base_url}/partners/sandbox/register",
            },
            {
                "rel": "onboarding-guide",
                "type": "api",
                "title": "Guided test partner onboarding flow",
                "href": f"{base_url}/partners/onboarding/guide",
            },
            {
                "rel": "test-partners",
                "type": "api",
                "title": "List registered test partners (no keys)",
                "href": f"{base_url}/partners/test",
            },
            {
                "rel": "partner-progress",
                "type": "api",
                "title": "Sandbox partner journey progress (requires sandbox key)",
                "href": f"{base_url}/partners/me/progress",
            },
            {
                "rel": "agent-payments",
                "type": "markdown",
                "title": "Pay Arclya with USDC (agent self-service guide)",
                "href": AGENT_PAYMENTS_DOC_URL,
            },
            {
                "rel": "crypto-networks",
                "type": "api",
                "title": "List accepted USDC networks and receive addresses",
                "href": f"{base_url}/payments/crypto/networks",
            },
            {
                "rel": "crypto-packages",
                "type": "api",
                "title": "List USDC service packages (Onboarding, Closer Access, Per Close)",
                "href": f"{base_url}/payments/crypto/packages",
            },
            {
                "rel": "crypto-checkout",
                "type": "api",
                "title": "Create package-based USDC checkout with payment instructions",
                "href": f"{base_url}/payments/crypto/checkout",
            },
            {
                "rel": "crypto-intent",
                "type": "api",
                "title": "Create USDC payment intent (custom amount, x402 checkout)",
                "href": f"{base_url}/payments/crypto/intent",
            },
            {
                "rel": "crypto-submit",
                "type": "api",
                "title": "Submit on-chain tx_hash proof for a crypto payment",
                "href": f"{base_url}/payments/crypto/{{payment_id}}/submit",
            },
            {
                "rel": "crypto-sales-guide",
                "type": "markdown",
                "title": "Pay with USDC / Crypto Sales (first 10 sales)",
                "href": f"{GITHUB_DOCS_BASE}/test-partner-onboarding-checklist.md#pay-with-usdc--crypto-sales-first-10-sales",
            },
            {
                "rel": "first-crypto-sale-runbook",
                "type": "markdown",
                "title": "Operator Runbook: First Partner + First Crypto Sale",
                "href": f"{GITHUB_DOCS_BASE}/first-crypto-sale-runbook.md",
            },
        ],
        "endpoints": {
            "discovery": f"{base_url}/.well-known/agent-card.json",
            "handoff_chain": f"{base_url}/orchestrate/handoff-chain",
            "route_preview": f"{base_url}/orchestrate/route",
            "sandbox_register": f"{base_url}/partners/sandbox/register",
            "onboarding_guide": f"{base_url}/partners/onboarding/guide",
            "test_partners": f"{base_url}/partners/test",
            "partner_progress": f"{base_url}/partners/me/progress",
            "billing_deals": f"{base_url}/billing/deals",
            "learning_run": f"{base_url}/learning/run",
            "crypto_networks": f"{base_url}/payments/crypto/networks",
            "crypto_packages": f"{base_url}/payments/crypto/packages",
            "crypto_checkout": f"{base_url}/payments/crypto/checkout",
            "crypto_intent": f"{base_url}/payments/crypto/intent",
            "crypto_submit": f"{base_url}/payments/crypto/{{payment_id}}/submit",
            "crypto_status": f"{base_url}/payments/crypto/{{payment_id}}",
        },
    }
    if payments_block is not None:
        card["payments"] = payments_block
    return card