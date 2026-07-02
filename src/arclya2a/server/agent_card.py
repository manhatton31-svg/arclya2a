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
from arclya2a.agents.accounts import DEFAULT_DIRECTORY_SORT, VALID_DIRECTORY_SORTS, count_agent_accounts
from arclya2a.agents.email_verification import directory_requires_email_verification
from arclya2a.agents.security import (
    DIRECTORY_MAX_CAPABILITY_FILTERS,
    DIRECTORY_MAX_LIMIT,
    DIRECTORY_SEARCH_MAX_LEN,
    agent_directory_rate_limit_per_minute,
    agent_recommended_rate_limit_per_minute,
    agent_register_rate_limit_per_minute,
    agent_rotate_key_rate_limit_per_minute,
)
from arclya2a.agents.onboarding_guide import (
    GITHUB_DOCS_AGENT_ONBOARDING,
    GITHUB_DOCS_PRODUCTION_READINESS,
    GUIDE_VERSION,
    SUGGESTED_CAPABILITIES,
)
from arclya2a.settings import public_url_source, resolve_public_base_url
from arclya2a.agents.terms import (
    ACCEPTABLE_USE_POLICY_PATH,
    TERMS_DOC_PATH,
    TERMS_OF_SERVICE_PATH,
    build_terms_info,
    current_terms_version,
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

    public_base = resolve_public_base_url(fallback=base_url)

    payments_block: dict[str, Any] | None = None
    if is_crypto_payments_configured():
        networks = list_accepted_crypto_networks() if is_crypto_payments_enabled() else []
        payments_block = build_agent_payments_discovery(
            base_url=public_base,
            networks=networks,
            root=root,
        )
        payments_block["enabled"] = is_crypto_payments_enabled()
    platform_block: dict[str, Any] = {
        "public_url": public_base,
        "public_url_source": public_url_source(),
        "phase": "1",
        "close_type": "lead_routing_commitment",
        "pricing_model": "success_based",
        "billing": "pay_on_close_with_affiliate_attribution",
        "features": [
            "signed_agent_cards",
            "cryptographic_agent_identity",
            "x402_v2_native",
            "x402_batch_settlement",
            "x402_deferred_payments",
            "x402_facilitator_routing",
            "agent_referral_program",
            "agent_hangout",
            "deal_rooms",
            "deal_room_micropayments",
            "collaboration_hubs",
            "agent_marketplace",
            "reputation_trust_scoring",
            "reputation_directory_ranking",
            "reputation_guardrail_strictness",
            "agent_account_registration",
            "agent_directory",
            "agent_directory_pagination",
            "agent_directory_discovery",
            "agent_directory_recommendations",
            "agent_endpoint_rate_limiting",
            "agent_profile_input_validation",
            "agent_action_audit",
            "agent_operator_moderation",
            "agent_email_verification",
            "agent_api_key_rotation",
            "agent_terms_acceptance",
            "agent_public_profiles",
            "agent_onboarding_guide",
            "agent_post_registration_flow",
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
        "agent_accounts": count_agent_accounts(root),
        "agent_directory_capabilities": {
            "pagination": True,
            "default_sort": DEFAULT_DIRECTORY_SORT,
            "sort_options": sorted(VALID_DIRECTORY_SORTS),
            "multi_capability_filter": True,
            "capability_filter_mode": "all_required",
            "text_search_fields": ["agent_name", "description", "capabilities"],
            "scoring_fields": ["relevance", "match_score"],
            "recommendations": {
                "endpoint": "GET /agents/recommended",
                "directory_flag": "recommended=true",
                "requires_auth": True,
                "basis": "overlapping_capabilities",
            },
            "filters": ["capability", "q", "recommended"],
            "query_params": [
                "offset",
                "limit",
                "sort",
                "capability",
                "q",
                "recommended",
            ],
            "limits": {
                "max_limit_per_request": DIRECTORY_MAX_LIMIT,
                "max_capability_filters": DIRECTORY_MAX_CAPABILITY_FILTERS,
                "search_max_length": DIRECTORY_SEARCH_MAX_LEN,
            },
        },
        "agent_email_verification": {
            "required_for_directory": directory_requires_email_verification(),
            "configurable": "ARCLYA_AGENT_REQUIRE_EMAIL_VERIFICATION",
            "default_verified": False,
            "verify_endpoint": "POST /agents/verify-email",
            "verify_link": "GET /agents/verify-email?token=ev_<token>",
            "resend_endpoint": "POST /agents/me/resend-verification",
            "token_expiry_hours_setting": "ARCLYA_AGENT_EMAIL_VERIFICATION_HOURS",
            "smtp_url_setting": "ARCLYA_AGENT_EMAIL_SMTP_URL",
            "from_address_setting": "ARCLYA_AGENT_EMAIL_FROM",
            "delivery_mode_setting": "ARCLYA_AGENT_EMAIL_DELIVERY",
            "delivery_modes": {
                "auto": "SMTP when URL + from configured; otherwise outbox",
                "smtp": "SMTP delivery (outbox audit trail retained)",
                "outbox": "Dev/CI — log only, no SMTP send",
            },
            "public_url_for_links": "ARCLYA_PUBLIC_URL or RENDER_EXTERNAL_URL",
        },
        "agent_terms_acceptance": {
            "current_version": current_terms_version(),
            "documentation": TERMS_DOC_PATH,
            "terms_of_service": TERMS_OF_SERVICE_PATH,
            "acceptable_use_policy": ACCEPTABLE_USE_POLICY_PATH,
            "metadata_endpoint": "GET /agents/terms",
            "required_at_registration": True,
            "required_for_directory": True,
            "accept_field": "terms_accepted",
            "accept_field_aliases": ["accept_terms"],
            "accept_via_profile": "PATCH /agents/me",
            "audit_event": "agent_terms_accepted",
        },
        "agent_api_key_rotation": {
            "self_service_endpoint": "POST /agents/me/rotate-key",
            "operator_endpoint": "POST /agents/{agent_id}/rotate-key",
            "requires_current_key": True,
            "old_key_revoked_immediately": True,
            "new_key_shown_once": True,
            "rate_limit_per_minute_setting": "ARCLYA_AGENT_ROTATE_KEY_RATE_LIMIT_PER_MINUTE",
            "rate_limit_per_minute": agent_rotate_key_rate_limit_per_minute(),
            "audit_event": "agent_api_key_rotated",
            "use_cases": {
                "compromised": "Agent rotates with POST /agents/me/rotate-key",
                "lost": "Operator force-rotates with POST /agents/{agent_id}/rotate-key",
            },
        },
        "agent_operator_management": {
            "requires_operator_key": True,
            "auth_header": "X-Arclya-Operator-Key",
            "endpoints": {
                "list_agents": "GET /agents/manage",
                "set_status": "PATCH /agents/{agent_id}/status",
                "rotate_key": "POST /agents/{agent_id}/rotate-key",
                "agent_audit": "GET /agents/{agent_id}/audit",
                "global_audit": "GET /agents/audit",
            },
            "list_filters": [
                "status",
                "publicly_listed",
                "q",
                "recently_active",
                "offset",
                "limit",
                "sort",
            ],
            "statuses": {
                "active": "Full API access and directory eligibility when publicly_listed",
                "suspended": "API key blocked; removed from directory",
                "pending_review": "API key blocked until operator approves; not in directory",
            },
            "ops_dashboard_section": "agents.management",
        },
        "agent_action_audit": {
            "enabled": True,
            "log_path": "data/audit/agent_actions.jsonl",
            "operator_endpoint": "GET /agents/audit",
            "ops_dashboard_section": "agents",
            "event_types": [
                "agent_registered",
                "agent_profile_updated",
                "agent_directory_opt_in",
                "agent_directory_opt_out",
                "agent_auth_failure",
                "agent_directory_search",
                "agent_directory_recommendation",
                "agent_directory_browse",
                "agent_api_key_rotated",
                "agent_terms_accepted",
                "agent_hangout_activity",
            ],
            "suspicious_activity_detection": True,
        },
        "agent_endpoint_security": {
            "rate_limiting": True,
            "rate_limits_per_minute": {
                "POST /agents/register": agent_register_rate_limit_per_minute(),
                "GET /agents": agent_directory_rate_limit_per_minute(),
                "GET /agents/directory": agent_directory_rate_limit_per_minute(),
                "GET /agents/recommended": agent_recommended_rate_limit_per_minute(),
                "POST /agents/me/rotate-key": agent_rotate_key_rate_limit_per_minute(),
            },
            "registration_ip_daily_cap": True,
            "directory_query_validation": True,
            "profile_sanitization": ["description", "capabilities"],
            "injection_scan_on_profile": True,
            "auth_error_details": [
                "missing_api_key",
                "invalid_key_format",
                "unknown_or_revoked_key",
                "wrong_key_type",
                "account_suspended",
                "account_pending_review",
            ],
        },
        "agent_hangout": {
            "discovery": "GET /agents/hangout",
            "deal_rooms": {
                "list": "GET /agents/hangout/deal-rooms",
                "create": "POST /agents/hangout/deal-rooms",
                "get": "GET /agents/hangout/deal-rooms/{room_id}",
                "message": "POST /agents/hangout/deal-rooms/{room_id}/messages",
                "close": "POST /agents/hangout/deal-rooms/{room_id}/close",
                "micropayment": "POST /agents/hangout/deal-rooms/{room_id}/micropayment",
                "close_type_default": "lead_routing_commitment",
            },
            "collaboration_hubs": {
                "list": "GET /agents/hangout/hubs",
                "join": "POST /agents/hangout/hubs",
                "search_by": ["topic", "capability", "vertical", "q"],
            },
            "marketplace": {
                "list": "GET /agents/hangout/marketplace",
                "create": "POST /agents/hangout/marketplace",
                "checkout_hint": "GET /agents/hangout/marketplace/{listing_id}/checkout",
                "currency": "USDC",
                "x402_compatible": True,
                "anti_duplication": True,
            },
            "reputation": "GET /agents/{agent_id}/reputation",
            "directory_sort_reputation": "trust_score_desc",
            "constitutional": {
                "inference": "xai_only",
                "living_prompts": True,
                "prompt_caching": True,
                "margin_guardrail": "profit_guardrail",
                "handoff_protocol": "strong_handoff_v1",
            },
        },
        "x402_v2": {
            "version": 2,
            "facilitators": "GET /payments/crypto/x402/facilitators",
            "deferred": "POST /payments/crypto/x402/deferred",
            "batch_settle": "POST /payments/crypto/x402/batch-settle",
            "schemes": ["exact", "deferred", "batch"],
        },
        "agent_referral_program": {
            "enabled": True,
            "discovery": "GET /agents/referrals/program",
            "referral_code": "GET /agents/me/referral-code",
            "dashboard": "GET /agents/me/referrals",
            "invite": "GET /agents/referrals/invite",
            "invite_create": "POST /agents/referrals/invite",
            "register_field": "referral_code",
            "reward_currency": "USDC",
            "qualification": "registration + email_verified + directory_opt_in",
        },
        "signed_agent_cards": {
            "platform_card": "GET /.well-known/agent-card.json",
            "per_agent_card": "GET /agents/{agent_id}/agent-card.json",
            "verify": "POST /.well-known/agent-card/verify",
            "algorithm": "HS256",
        },
        "external_agents": {
            "registration": "POST /agents/register",
            "registration_requires_terms": True,
            "accept_fields": ["terms_accepted", "accept_terms"],
            "authentication": "X-Arclya-Key (arclya_prod_*)",
            "profile": "GET /agents/me",
            "directory_opt_in": "PATCH /agents/me { publicly_listed: true }",
            "directory_prerequisites": [
                "terms_accepted (current version)",
                "email on file",
                "email verified",
                "account status active",
            ],
            "directory": "GET /agents/directory",
            "directory_recommended": "GET /agents/recommended",
            "onboarding_guide": "GET /agents/onboarding/guide",
            "onboarding_guide_version": GUIDE_VERSION,
            "terms_metadata": "GET /agents/terms",
            "platform_health": "GET /health (external_agents summary)",
            "platform_status": "GET /status (full external_agents metrics)",
            "platform_status_page": "GET /platform/status (HTML)",
            "production_readiness_checklist": GITHUB_DOCS_PRODUCTION_READINESS,
            "post_registration": {
                "summary": (
                    "Registration returns welcome_message, next_steps, terms, resources, "
                    "and api_key_reminder"
                ),
                "first_step": "Store api_key immediately (shown once)",
                "guide_path": "/agents/onboarding/guide",
                "documentation": GITHUB_DOCS_AGENT_ONBOARDING,
            },
            "suggested_capabilities": SUGGESTED_CAPABILITIES,
        },
        "agent_public_profile_fields": [
            "agent_id",
            "agent_name",
            "description",
            "capabilities",
            "capability_count",
            "created_at",
            "updated_at",
            "status",
            "publicly_listed",
            "has_email",
            "profile_url",
            "reputation",
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
            "self-service package checkout (x402-compatible). External agents self-register "
            "for persistent identity (ag_*), join the Agent Hangout (deal rooms, collaboration "
            "hubs, marketplace), build reputation, and close deals agent-to-agent with "
            "constitutional guardrails (xAI-only inference, living cached prompts, margin-positive closes)."
        ),
        "url": public_base,
        "version": version,
        "a2a": {
            "protocol_version": "1.0",
            "signed_agent_card": True,
            "identity_verification": "POST /.well-known/agent-card/verify",
            "interoperability": {
                "handoff_protocol": "strong_handoff_v1",
                "task_delegation": True,
                "state_transition_history": True,
                "confidence_scores": True,
                "structured_feedback": True,
                "role_cards": True,
                "memory_layer": True,
            },
            "inference": {
                "provider": "xai",
                "xai_only": True,
                "living_prompts": True,
                "prompt_caching": True,
                "self_improving": True,
            },
            "constitutional": {
                "margin_guardrail": "profit_guardrail",
                "qc_gate": "final_arbiter",
                "anti_spam": True,
                "anti_duplication": True,
                "crypto_first_payments": True,
                "zero_marginal_cost_goal": True,
            },
            "payments": {
                "x402_compatible": True,
                "currency": "USDC",
                "checkout": f"{public_base}/payments/crypto/checkout",
            },
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
            "taskDelegation": True,
            "secureHandoff": True,
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
                "href": f"{public_base}/health",
            },
            {
                "rel": "status",
                "type": "api",
                "title": "Operational status",
                "href": f"{public_base}/status",
            },
            {
                "rel": "ops-dashboard",
                "type": "api",
                "title": "Operational dashboard",
                "href": f"{public_base}/ops/dashboard",
            },
            {
                "rel": "onboarding-validate",
                "type": "api",
                "title": "Validate product profile before onboarding",
                "href": f"{public_base}/onboarding/validate",
            },
            {
                "rel": "agent-onboarding",
                "type": "markdown",
                "title": "External Agent Onboarding Guide",
                "href": GITHUB_DOCS_AGENT_ONBOARDING,
            },
            {
                "rel": "agent-production-readiness",
                "type": "markdown",
                "title": "External Agent Production Readiness Checklist",
                "href": GITHUB_DOCS_PRODUCTION_READINESS,
            },
            {
                "rel": "agent-terms",
                "type": "markdown",
                "title": "External Agent Terms of Service & Acceptable Use Policy",
                "href": f"{GITHUB_DOCS_BASE}/agent-terms.md",
            },
            {
                "rel": "agent-onboarding-guide",
                "type": "api",
                "title": "Guided external agent onboarding flow (JSON)",
                "href": f"{public_base}/agents/onboarding/guide",
            },
            {
                "rel": "agent-register",
                "type": "api",
                "title": "Register an external agent account (production API key)",
                "href": f"{public_base}/agents/register",
            },
            {
                "rel": "agent-profile",
                "type": "api",
                "title": "Authenticated agent profile (requires production API key)",
                "href": f"{public_base}/agents/me",
            },
            {
                "rel": "agent-directory",
                "type": "api",
                "title": "Public Agent Directory (search, multi-capability filter, pagination)",
                "href": f"{public_base}/agents/directory",
            },
            {
                "rel": "agent-directory-recommended",
                "type": "api",
                "title": "Recommended agents for authenticated viewer (capability overlap)",
                "href": f"{public_base}/agents/recommended",
            },
            {
                "rel": "agent-public-profile",
                "type": "api",
                "title": "Public agent profile by ID",
                "href": f"{public_base}/agents/{{agent_id}}",
            },
            {
                "rel": "agent-hangout",
                "type": "api",
                "title": "Agent Hangout discovery (deal rooms, hubs, marketplace, reputation)",
                "href": f"{public_base}/agents/hangout",
            },
            {
                "rel": "agent-reputation",
                "type": "api",
                "title": "Agent trust score and reputation factors",
                "href": f"{public_base}/agents/{{agent_id}}/reputation",
            },
            {
                "rel": "sandbox-register",
                "type": "api",
                "title": "Self-service sandbox API key for test partners",
                "href": f"{public_base}/partners/sandbox/register",
            },
            {
                "rel": "onboarding-guide",
                "type": "api",
                "title": "Guided test partner onboarding flow",
                "href": f"{public_base}/partners/onboarding/guide",
            },
            {
                "rel": "test-partners",
                "type": "api",
                "title": "List registered test partners (no keys)",
                "href": f"{public_base}/partners/test",
            },
            {
                "rel": "partner-progress",
                "type": "api",
                "title": "Sandbox partner journey progress (requires sandbox key)",
                "href": f"{public_base}/partners/me/progress",
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
                "href": f"{public_base}/payments/crypto/networks",
            },
            {
                "rel": "crypto-packages",
                "type": "api",
                "title": "List USDC service packages (Onboarding, Closer Access, Per Close)",
                "href": f"{public_base}/payments/crypto/packages",
            },
            {
                "rel": "crypto-checkout",
                "type": "api",
                "title": "Create package-based USDC checkout with payment instructions",
                "href": f"{public_base}/payments/crypto/checkout",
            },
            {
                "rel": "crypto-intent",
                "type": "api",
                "title": "Create USDC payment intent (custom amount, x402 checkout)",
                "href": f"{public_base}/payments/crypto/intent",
            },
            {
                "rel": "crypto-submit",
                "type": "api",
                "title": "Submit on-chain tx_hash proof for a crypto payment",
                "href": f"{public_base}/payments/crypto/{{payment_id}}/submit",
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
            "discovery": f"{public_base}/.well-known/agent-card.json",
            "handoff_chain": f"{public_base}/orchestrate/handoff-chain",
            "route_preview": f"{public_base}/orchestrate/route",
            "agent_onboarding_guide": f"{public_base}/agents/onboarding/guide",
            "agent_register": f"{public_base}/agents/register",
            "agent_profile": f"{public_base}/agents/me",
            "agent_rotate_key": f"{public_base}/agents/me/rotate-key",
            "agent_terms": f"{public_base}/agents/terms",
            "platform_status_page": f"{public_base}/platform/status",
            "agent_directory": f"{public_base}/agents/directory",
            "agent_directory_recommended": f"{public_base}/agents/recommended",
            "agent_directory_list": f"{public_base}/agents",
            "agent_public_profile": f"{public_base}/agents/{{agent_id}}",
            "agent_hangout": f"{public_base}/agents/hangout",
            "deal_rooms": f"{public_base}/agents/hangout/deal-rooms",
            "collaboration_hubs": f"{public_base}/agents/hangout/hubs",
            "agent_marketplace": f"{public_base}/agents/hangout/marketplace",
            "agent_reputation": f"{public_base}/agents/{{agent_id}}/reputation",
            "agent_card_per_agent": f"{public_base}/agents/{{agent_id}}/agent-card.json",
            "agent_card_verify": f"{public_base}/.well-known/agent-card/verify",
            "x402_facilitators": f"{public_base}/payments/crypto/x402/facilitators",
            "x402_deferred": f"{public_base}/payments/crypto/x402/deferred",
            "x402_batch_settle": f"{public_base}/payments/crypto/x402/batch-settle",
            "agent_referral_program": f"{public_base}/agents/referrals/program",
            "sandbox_register": f"{public_base}/partners/sandbox/register",
            "onboarding_guide": f"{public_base}/partners/onboarding/guide",
            "test_partners": f"{public_base}/partners/test",
            "partner_progress": f"{public_base}/partners/me/progress",
            "billing_deals": f"{public_base}/billing/deals",
            "learning_run": f"{public_base}/learning/run",
            "crypto_networks": f"{public_base}/payments/crypto/networks",
            "crypto_packages": f"{public_base}/payments/crypto/packages",
            "crypto_checkout": f"{public_base}/payments/crypto/checkout",
            "crypto_intent": f"{public_base}/payments/crypto/intent",
            "crypto_submit": f"{public_base}/payments/crypto/{{payment_id}}/submit",
            "crypto_status": f"{public_base}/payments/crypto/{{payment_id}}",
        },
    }
    if payments_block is not None:
        card["payments"] = payments_block
    return card