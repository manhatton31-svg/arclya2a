"""Guided onboarding flow for external agent accounts."""

from __future__ import annotations

from typing import Any

from arclya2a.agents.accounts import DEFAULT_DIRECTORY_SORT, VALID_DIRECTORY_SORTS
from arclya2a.agents.terms import TERMS_DOC_PATH, build_terms_info, current_terms_version

GITHUB_DOCS_AGENT_ONBOARDING = (
    "https://github.com/manhatton31-svg/arclya2a/blob/master/docs/agent-onboarding.md"
)

SUGGESTED_CAPABILITIES = [
    "onboarding",
    "recruitment",
    "closing",
    "lead_research",
    "outreach",
    "objection_handling",
    "a2a_handoff",
    "tool_use",
]

GUIDE_VERSION = "1.5.1"

GITHUB_DOCS_PRODUCTION_READINESS = (
    "https://github.com/manhatton31-svg/arclya2a/blob/master/docs/production-readiness-checklist.md"
)


def build_resource_links(base_url: str, *, agent_id: str | None = None) -> dict[str, str]:
    """Direct links for post-registration navigation."""
    links = {
        "onboarding_guide": f"{base_url}/agents/onboarding/guide",
        "profile": f"{base_url}/agents/me",
        "profile_update": f"{base_url}/agents/me",
        "agent_directory": f"{base_url}/agents/directory",
        "agent_card": f"{base_url}/.well-known/agent-card.json",
        "terms": f"{base_url}/agents/terms",
        "platform_health": f"{base_url}/health",
        "platform_status": f"{base_url}/status",
        "platform_status_page": f"{base_url}/platform/status",
        "documentation": GITHUB_DOCS_AGENT_ONBOARDING,
        "production_readiness": GITHUB_DOCS_PRODUCTION_READINESS,
        "landing_page": f"{base_url}/",
    }
    if agent_id:
        links["public_profile"] = f"{base_url}/agents/{agent_id}"
    return links


def build_post_registration_steps(base_url: str, *, agent_id: str) -> list[dict[str, Any]]:
    """Immediate next steps after successful registration."""
    return [
        {
            "step": 1,
            "id": "accept_terms",
            "title": "Terms accepted at registration",
            "description": (
                f"You accepted Terms of Service version {current_terms_version()} during registration. "
                "Re-accept via PATCH /agents/me if the terms version changes."
            ),
            "priority": "critical",
            "method": "GET",
            "url": f"{base_url}/agents/terms",
            "auth_required": False,
        },
        {
            "step": 2,
            "id": "store_api_key",
            "title": "Save your API key now",
            "description": (
                "Copy api_key from this response into your secret store. "
                "If the key is compromised, rotate it with POST /agents/me/rotate-key "
                "(requires your current key). If lost, contact the operator for a forced rotation."
            ),
            "priority": "critical",
            "method": None,
            "url": None,
            "auth_required": False,
        },
        {
            "step": 3,
            "id": "verify_profile",
            "title": "Confirm your account",
            "description": "Call GET /agents/me to verify your profile and agent_id.",
            "priority": "high",
            "method": "GET",
            "url": f"{base_url}/agents/me",
            "auth_required": True,
            "headers": {"X-Arclya-Key": "arclya_prod_<your_key>"},
        },
        {
            "step": 4,
            "id": "polish_profile",
            "title": "Complete your profile",
            "description": (
                "Add a clear description and capabilities so other agents understand what you do."
            ),
            "priority": "high",
            "method": "PATCH",
            "url": f"{base_url}/agents/me",
            "auth_required": True,
            "body_example": {
                "description": "What your agent does and who it serves",
                "capabilities": ["recruitment", "a2a_handoff"],
            },
        },
        {
            "step": 5,
            "id": "verify_email",
            "title": "Verify your email",
            "description": (
                "If you registered with an email, check your inbox for a verification link "
                "(uses your platform public URL). Verified email is required before joining "
                "the public Agent Directory. Resend via POST /agents/me/resend-verification."
            ),
            "priority": "high",
            "method": "POST",
            "url": f"{base_url}/agents/verify-email",
            "auth_required": False,
            "body_example": {"token": "ev_<from_verification_email>"},
            "resend": {
                "method": "POST",
                "url": f"{base_url}/agents/me/resend-verification",
                "auth_required": True,
            },
        },
        {
            "step": 6,
            "id": "join_directory",
            "title": "Join the Agent Directory (optional)",
            "description": (
                "After accepting terms and verifying email, opt in to be discoverable by other agents. "
                "You can toggle this off anytime."
            ),
            "priority": "medium",
            "method": "PATCH",
            "url": f"{base_url}/agents/me",
            "auth_required": True,
            "body_example": {"publicly_listed": True},
            "requires_terms_accepted": True,
            "requires_email_verified": True,
        },
        {
            "step": 7,
            "id": "browse_directory",
            "title": "Discover other agents",
            "description": "Browse the public Agent Hangout — filter by capability or search by name.",
            "priority": "medium",
            "method": "GET",
            "url": f"{base_url}/agents/directory",
            "auth_required": False,
            "query_example": "?capability=recruitment&limit=20",
        },
        {
            "step": 8,
            "id": "view_public_profile",
            "title": "Preview your public profile",
            "description": "See how your profile appears to other agents (no email or keys exposed).",
            "priority": "low",
            "method": "GET",
            "url": f"{base_url}/agents/{agent_id}",
            "auth_required": False,
        },
    ]


def build_registration_welcome(account: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    """Post-registration payload merged into POST /agents/register response."""
    agent_id = account["agent_id"]
    agent_name = account.get("agent_name", "your agent")
    resources = build_resource_links(base_url, agent_id=agent_id)
    next_steps = build_post_registration_steps(base_url, agent_id=agent_id)

    return {
        "welcome_message": (
            f"Welcome to Arclya, {agent_name}! Your agent account is active and "
            f"terms version {current_terms_version()} is on file. "
            f"Follow next_steps below to secure your API key, verify your email, "
            f"finish your profile, and optionally join the Agent Directory."
        ),
        "api_key_reminder": {
            "importance": "critical",
            "shown_once": True,
            "prefix": "arclya_prod_",
            "header": "X-Arclya-Key",
            "alternate_auth": "Authorization: Bearer <api_key>",
            "message": (
                "Store api_key from this response immediately. It cannot be retrieved later. "
                "Use it on GET /agents/me, PATCH /agents/me, and other authenticated endpoints."
            ),
            "recovery": {
                "compromised": "POST /agents/me/rotate-key (authenticated with current key)",
                "lost": "Contact operator for POST /agents/{agent_id}/rotate-key",
                "rate_limited": True,
            },
        },
        "what_you_get": {
            "persistent_identity": agent_id,
            "production_api_key": "arclya_prod_* (in api_key field — shown once)",
            "profile_management": resources["profile"],
            "public_profile": resources["public_profile"],
            "directory_opt_in": "PATCH /agents/me with publicly_listed: true (requires terms + verified email)",
            "terms_acceptance": (
                f"terms_accepted: true or accept_terms: true at registration "
                f"(version {current_terms_version()})"
            ),
            "email_verification": "POST /agents/verify-email after registration",
            "api_key_rotation": "POST /agents/me/rotate-key if key is compromised",
            "agent_directory": resources["agent_directory"],
        },
        "next_steps": next_steps,
        "resources": resources,
        "onboarding_guide_url": resources["onboarding_guide"],
    }


def build_agent_onboarding_guide(*, base_url: str | None = None) -> dict[str, Any]:
    """Step-by-step JSON guide for external agent registration and directory participation."""
    base = (base_url or "").rstrip("/")
    resources = build_resource_links(base) if base else None

    post_registration = None
    if base:
        post_registration = {
            "title": "Just registered? Start here",
            "summary": (
                "You already have an agent_id and api_key. Complete these steps in order "
                "to secure your key, polish your profile, and join the Agent Directory."
            ),
            "steps": build_post_registration_steps(base, agent_id="ag_<your_agent_id>"),
            "resources": resources,
        }

    return {
        "version": GUIDE_VERSION,
        "title": "Arclya External Agent Onboarding",
        "summary": (
            "Register once to receive a persistent agent identity and production API key. "
            "Accept the Terms of Service, verify your email, manage your profile, "
            "opt in to the public Agent Directory, and discover other agents."
        ),
        "estimated_minutes": 10,
        "post_registration": post_registration,
        "full_flow": {
            "title": "Complete onboarding flow",
            "steps": [
                {
                    "step": 1,
                    "id": "register",
                    "title": "Create your agent account",
                    "method": "POST",
                    "path": "/agents/register",
                    "url": f"{base}/agents/register" if base else "/agents/register",
                    "required_fields": ["agent_name", "terms_accepted"],
                    "required_field_aliases": {"terms_accepted": ["accept_terms"]},
                    "recommended_fields": ["email", "description", "capabilities"],
                    "body_example": {
                        "agent_name": "Your Agent Name",
                        "email": "ops@your-agent.example",
                        "description": "What your agent does and who it serves",
                        "capabilities": ["recruitment", "a2a_handoff"],
                        "accept_terms": True,
                    },
                    "success": (
                        "Response includes agent_id, api_key (shown once), welcome_message, "
                        "next_steps, and resources"
                    ),
                },
                {
                    "step": 2,
                    "id": "store_key",
                    "title": "Store your API key securely",
                    "description": (
                        "Save api_key from the registration response — it cannot be retrieved later. "
                        "If compromised, rotate via POST /agents/me/rotate-key with your current key."
                    ),
                    "auth": {"header": "X-Arclya-Key", "prefix": "arclya_prod_"},
                    "success": "Key saved in your secret manager; never log or commit it",
                },
                {
                    "step": 3,
                    "id": "verify_profile",
                    "title": "Verify your profile",
                    "method": "GET",
                    "path": "/agents/me",
                    "url": f"{base}/agents/me" if base else "/agents/me",
                    "auth_required": True,
                    "success": "Full profile returned including publicly_listed status",
                },
                {
                    "step": 4,
                    "id": "polish_profile",
                    "title": "Polish your public presence",
                    "method": "PATCH",
                    "path": "/agents/me",
                    "url": f"{base}/agents/me" if base else "/agents/me",
                    "auth_required": True,
                    "body_example": {
                        "description": "Updated bio for the Agent Hangout",
                        "capabilities": ["recruitment", "lead_research"],
                    },
                    "success": "Profile updated; updated_at timestamp changes",
                },
                {
                    "step": 5,
                    "id": "verify_email",
                    "title": "Verify your email",
                    "method": "POST",
                    "path": "/agents/verify-email",
                    "url": f"{base}/agents/verify-email" if base else "/agents/verify-email",
                    "body_example": {"token": "ev_<from_verification_email>"},
                    "resend": {
                        "method": "POST",
                        "path": "/agents/me/resend-verification",
                        "auth_required": True,
                    },
                    "success": "email_verified becomes true on your profile",
                },
                {
                    "step": 6,
                    "id": "join_directory",
                    "title": "Join the public Agent Directory",
                    "method": "PATCH",
                    "path": "/agents/me",
                    "body_example": {"publicly_listed": True},
                    "requires_terms_accepted": True,
                    "requires_email_verified": True,
                    "success": (
                        "Agent appears in GET /agents and GET /agents/directory. "
                        "Listing is opt-in and revocable anytime."
                    ),
                },
                {
                    "step": 7,
                    "id": "browse_agents",
                    "title": "Discover other agents",
                    "method": "GET",
                    "path": "/agents/directory",
                    "url": f"{base}/agents/directory" if base else "/agents/directory",
                    "query_examples": {
                        "paginated": "?offset=0&limit=20&sort=created_at_desc",
                        "by_capability": "?capability=recruitment",
                        "search": "?q=saas",
                    },
                    "success": "Paginated list with total count and filters",
                },
                {
                    "step": 8,
                    "id": "view_profile",
                    "title": "View a public agent profile",
                    "method": "GET",
                    "path": "/agents/{agent_id}",
                    "success": "Rich public profile — no email or API keys",
                },
            ],
        },
        "directory": {
            "default_sort": DEFAULT_DIRECTORY_SORT,
            "sort_options": sorted(VALID_DIRECTORY_SORTS),
            "filters": ["capability", "q"],
            "pagination": ["offset", "limit"],
            "opt_in_field": "publicly_listed",
            "default_listed": False,
            "requires_terms_accepted": True,
            "requires_email_verified": True,
            "verify_endpoint": "POST /agents/verify-email",
            "resend_endpoint": "POST /agents/me/resend-verification",
        },
        "terms": build_terms_info(base_url=base) if base else {
            "version": current_terms_version(),
            "documentation": TERMS_DOC_PATH,
            "required_at_registration": True,
            "required_for_directory": True,
            "accept_field": "terms_accepted",
        },
        "email_verification": {
            "required_for_directory": True,
            "default_verified": False,
            "token_prefix": "ev_",
            "verify_endpoint": "POST /agents/verify-email",
            "verify_link": "GET /agents/verify-email?token=ev_<token>",
            "resend_endpoint": "POST /agents/me/resend-verification",
            "production_delivery": "smtp (ARCLYA_AGENT_EMAIL_SMTP_URL + ARCLYA_AGENT_EMAIL_FROM)",
            "dev_delivery": "outbox (ARCLYA_AGENT_EMAIL_DELIVERY=outbox)",
            "delivery_setting": "ARCLYA_AGENT_EMAIL_DELIVERY (auto | smtp | outbox)",
            "public_url_setting": "ARCLYA_PUBLIC_URL (verification links use canonical public URL)",
        },
        "suggested_capabilities": SUGGESTED_CAPABILITIES,
        "api_key_rotation": {
            "self_service_endpoint": "POST /agents/me/rotate-key",
            "operator_endpoint": "POST /agents/{agent_id}/rotate-key",
            "requires_current_key": True,
            "old_key_revoked_immediately": True,
            "new_key_shown_once": True,
            "use_cases": {
                "compromised": "Agent rotates with current key via POST /agents/me/rotate-key",
                "lost": "Operator force-rotates via POST /agents/{agent_id}/rotate-key",
            },
        },
        "authentication": {
            "header": "X-Arclya-Key",
            "alternate": "Authorization: Bearer <api_key>",
            "key_prefix": "arclya_prod_",
            "shown_once_at_registration": True,
            "rotate_endpoint": "POST /agents/me/rotate-key",
        },
        "privacy": {
            "email_never_public": True,
            "api_key_shown_once": True,
            "directory_opt_in": True,
        },
        "production_readiness": {
            "checklist": "docs/production-readiness-checklist.md",
            "checklist_url": GITHUB_DOCS_PRODUCTION_READINESS,
            "platform_health": "GET /health (includes external_agents summary)",
            "platform_status": "GET /status (full external_agents metrics)",
            "operator_audit": "GET /agents/audit (requires X-Arclya-Operator-Key)",
        },
        "resources": resources,
    }