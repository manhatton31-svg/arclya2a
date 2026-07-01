"""Guided test partner onboarding flow (JSON)."""

from __future__ import annotations

from typing import Any

from arclya2a.partners.progress import SUCCESS_DEFINITION
from arclya2a.partners.sandbox import sandbox_rate_limit
from arclya2a.partners.test_registry import GRADUATION_CRITERIA, SECURITY_GRADUATION_CRITERIA


def build_onboarding_guide() -> dict[str, Any]:
    """Step-by-step guide for new test partners."""
    return {
        "title": "Arclya Test Partner Onboarding",
        "mode": "sandbox",
        "estimated_minutes": 30,
        "steps": [
            {
                "step": 1,
                "id": "register",
                "title": "Get a sandbox API key",
                "action": "POST /partners/sandbox/register",
                "body_example": {
                    "agent_name": "Your Agent",
                    "agent_card_url": "https://your-agent.example/.well-known/agent-card.json",
                    "target_customer": "Who you sell to",
                },
                "success": "Response includes sandbox_key starting with arclya_sandbox_",
            },
            {
                "step": 2,
                "id": "validate_profile",
                "title": "Pre-validate product profile",
                "action": "POST /onboarding/validate",
                "auth": "Optional — sandbox key recommended via X-Arclya-Key",
                "success": "valid: true and destination_cta_preview set",
            },
            {
                "step": 3,
                "id": "smoke_handoff",
                "title": "Run sandbox handoff chain",
                "action": "POST /orchestrate/handoff-chain",
                "headers": ["X-Arclya-Key: <sandbox_key>", "X-Arclya-Agent-Id: <your_agent_id>"],
                "body_example": {
                    "deal_id": "sandbox_test_001",
                    "customer_company": "Test Partner Co",
                    "task_context": "Sandbox onboarding smoke test",
                    "auto_route": True,
                },
                "success": "Response includes sandbox_mode: true and emergency_stop: false",
            },
            {
                "step": 4,
                "id": "full_lifecycle",
                "title": "Complete lifecycle dry run",
                "actions": [
                    "Onboard until summary.profile_saved",
                    "Recruit with acquisition_stage: prospect",
                    "Close with lead_warmth: warm",
                ],
                "success": "summary.lead_routing_confirmed: true in sandbox",
            },
            {
                "step": 5,
                "id": "graduate",
                "title": "Graduate to production",
                "action": "Contact Arclya operator when graduation_ready is true on GET /partners/test",
                "success": "Production API key issued; sandbox key may be revoked",
            },
        ],
        "success_definition": SUCCESS_DEFINITION,
        "progress_endpoint": "GET /partners/me/progress",
        "graduation_criteria": GRADUATION_CRITERIA,
        "security_graduation_criteria": SECURITY_GRADUATION_CRITERIA,
        "sandbox_defaults": {
            "tools": "dry_run",
            "billing": "disabled",
            "high_risk_tools": "blocked",
            "rate_limit_per_minute": sandbox_rate_limit(),
            "test_marker": "[SANDBOX — dry-run tools, no production billing]",
        },
    }