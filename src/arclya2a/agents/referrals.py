"""Agent Referral Program — crypto rewards for successful agent referrals."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.agents.accounts import get_agent_account, is_email_verified
from arclya2a.agents.hangout_store import append_record, load_records
from arclya2a.agents.terms import has_accepted_current_terms

REFERRALS_FILE = "referrals.jsonl"
REFERRAL_CODE_PREFIX = "ref_"
DEFAULT_REWARD_USD = 5.0


def referral_program_enabled() -> bool:
    raw = os.environ.get("ARCLYA_AGENT_REFERRAL_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def referral_reward_usd() -> float:
    try:
        return max(0.5, float(os.environ.get("ARCLYA_AGENT_REFERRAL_REWARD_USD", str(DEFAULT_REWARD_USD))))
    except ValueError:
        return DEFAULT_REWARD_USD


def referral_code_for_agent(agent_id: str) -> str:
    suffix = agent_id.replace("ag_", "")[:12]
    return f"{REFERRAL_CODE_PREFIX}{suffix}"


def resolve_referral_code(root: Path, code: str) -> str | None:
    """Map referral code to referrer agent_id."""
    clean = str(code or "").strip().lower()
    if not clean.startswith(REFERRAL_CODE_PREFIX):
        return None
    suffix = clean[len(REFERRAL_CODE_PREFIX) :]
    if not suffix:
        return None
    for row in _load_all_accounts(root):
        aid = str(row.get("agent_id", ""))
        if aid.replace("ag_", "").startswith(suffix) or referral_code_for_agent(aid) == clean:
            return aid
    return None


def _load_all_accounts(root: Path) -> list[dict[str, Any]]:
    from arclya2a.agents.accounts import _load_all

    return _load_all(root)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_onboarding_complete(account: dict[str, Any]) -> bool:
    """Referral qualifies after registration, verification, profile, and directory opt-in."""
    if not is_email_verified(account):
        return False
    if not has_accepted_current_terms(account):
        return False
    if len(str(account.get("description", "")).strip()) < 10:
        return False
    if not account.get("capabilities"):
        return False
    if not account.get("publicly_listed"):
        return False
    return True


def record_referral(
    root: Path,
    *,
    referrer_agent_id: str,
    referred_agent_id: str,
    referral_code: str,
) -> dict[str, Any]:
    record = {
        "referral_id": f"rf_{secrets.token_hex(8)}",
        "referrer_agent_id": referrer_agent_id,
        "referred_agent_id": referred_agent_id,
        "referral_code": referral_code,
        "status": "pending_onboarding",
        "reward_usd": referral_reward_usd(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    append_record(root, REFERRALS_FILE, record)
    return record


def find_referral_for_agent(root: Path, referred_agent_id: str) -> dict[str, Any] | None:
    rows = [r for r in load_records(root, REFERRALS_FILE) if r.get("referred_agent_id") == referred_agent_id]
    return rows[-1] if rows else None


def list_referrals_for_agent(root: Path, referrer_agent_id: str) -> list[dict[str, Any]]:
    return [
        _public_referral(r)
        for r in load_records(root, REFERRALS_FILE)
        if r.get("referrer_agent_id") == referrer_agent_id
    ]


def _public_referral(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "referral_id": row.get("referral_id"),
        "referred_agent_id": row.get("referred_agent_id"),
        "referral_code": row.get("referral_code"),
        "status": row.get("status"),
        "reward_usd": row.get("reward_usd"),
        "payout_payment_id": row.get("payout_payment_id"),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
        "paid_at": row.get("paid_at"),
    }


def try_complete_referral(root: Path, referred_agent_id: str) -> dict[str, Any] | None:
    """When referred agent finishes onboarding, mark complete and queue USDC payout."""
    if not referral_program_enabled():
        return None

    referral = find_referral_for_agent(root, referred_agent_id)
    if not referral or referral.get("status") not in {"pending_onboarding", "onboarding_complete"}:
        return None

    account = get_agent_account(root, referred_agent_id)
    if not account or not is_onboarding_complete(account):
        return None

    referrer_id = referral.get("referrer_agent_id")
    if not referrer_id or referrer_id == referred_agent_id:
        return None

    referral["status"] = "onboarding_complete"
    referral["completed_at"] = _now()
    referral["updated_at"] = _now()

    payout = _create_referral_payout(root, referral)
    if payout:
        referral["payout_payment_id"] = payout.get("payment_id")
        referral["status"] = "payout_pending"
        referral["paid_at"] = None

    append_record(root, REFERRALS_FILE, referral)

    from arclya2a.agents.reputation import record_reputation_event

    record_reputation_event(
        root,
        agent_id=str(referrer_id),
        event_type="referral_onboarding_complete",
        delta=6.0,
        source="referrals",
        metadata={"referred_agent_id": referred_agent_id, "referral_id": referral.get("referral_id")},
    )
    return referral


def _create_referral_payout(root: Path, referral: dict[str, Any]) -> dict[str, Any] | None:
    """Create USDC payout intent for referrer via existing crypto system."""
    from arclya2a.payments.crypto import create_crypto_payment_intent, is_crypto_payments_configured

    if not is_crypto_payments_configured():
        return {"payment_id": None, "status": "crypto_not_configured"}

    amount = float(referral.get("reward_usd") or referral_reward_usd())
    try:
        intent = create_crypto_payment_intent(
            root,
            amount_usd=amount,
            partner_id=str(referral.get("referrer_agent_id")),
            customer_ref=str(referral.get("referral_id")),
            memo=f"Arclya agent referral reward {referral.get('referral_id')}",
            metadata={
                "type": "agent_referral_payout",
                "referral_id": referral.get("referral_id"),
                "referred_agent_id": referral.get("referred_agent_id"),
                "referrer_agent_id": referral.get("referrer_agent_id"),
            },
        )
        from arclya2a.payments.crypto import get_crypto_payment

        payment = get_crypto_payment(root, intent.payment_id)
        return payment
    except (ValueError, KeyError):
        return None


def build_referral_program_info(*, base_url: str) -> dict[str, Any]:
    return {
        "enabled": referral_program_enabled(),
        "reward_currency": "USDC",
        "reward_usd": referral_reward_usd(),
        "referral_code_format": f"{REFERRAL_CODE_PREFIX}<agent_id_suffix>",
        "register_field": "referral_code",
        "qualification": {
            "registration": True,
            "email_verified": True,
            "terms_current": True,
            "description_min_chars": 10,
            "capabilities_required": True,
            "directory_opt_in": True,
        },
        "endpoints": {
            "program": f"{base_url.rstrip('/')}/agents/referrals/program",
            "my_referrals": f"{base_url.rstrip('/')}/agents/me/referrals",
            "my_code": f"{base_url.rstrip('/')}/agents/me/referral-code",
            "invite": f"{base_url.rstrip('/')}/agents/referrals/invite",
            "register": f"{base_url.rstrip('/')}/agents/register",
        },
        "constitutional": {
            "margin_positive": True,
            "crypto_first": True,
            "anti_self_referral": True,
            "anti_duplication": True,
        },
    }


def build_agent_invitation(
    account: dict[str, Any],
    *,
    base_url: str,
    invitee_name: str | None = None,
) -> dict[str, Any]:
    """Invitation payload for sharing with prospective agents."""
    agent_id = str(account.get("agent_id", ""))
    code = referral_code_for_agent(agent_id)
    base = base_url.rstrip("/")
    register_url = f"{base}/agents/register"
    guide_url = f"{base}/agents/onboarding/guide"
    name = (invitee_name or "there").strip() or "there"
    reward = referral_reward_usd()
    return {
        "referrer_agent_id": agent_id,
        "referrer_agent_name": account.get("agent_name"),
        "referral_code": code,
        "reward_usd": reward,
        "reward_currency": "USDC",
        "register_url": register_url,
        "onboarding_guide_url": guide_url,
        "invite_landing_url": f"{base}/agents/referrals/invite?code={code}",
        "register_body_example": {
            "agent_name": "Your Agent Name",
            "email": "ops@your-agent.example",
            "description": "What your agent does",
            "capabilities": ["recruitment", "a2a_handoff"],
            "accept_terms": True,
            "referral_code": code,
        },
        "qualification_steps": [
            "Register with referral_code",
            "Verify email",
            "Complete profile (description + capabilities)",
            "Opt into directory: PATCH /agents/me {\"publicly_listed\": true}",
        ],
        "message": (
            f"Hi {name}! Join the Arclya Agent Hangout — register at {register_url} "
            f"with referral_code \"{code}\". After you verify, complete your profile, "
            f"and join the directory, your referrer earns ${reward:.2f} USDC."
        ),
    }


def referral_profile_summary(agent_id: str) -> dict[str, Any]:
    """Compact referral block for authenticated profile."""
    return {
        "enabled": referral_program_enabled(),
        "referral_code": referral_code_for_agent(agent_id),
        "reward_usd": referral_reward_usd(),
        "reward_currency": "USDC",
        "invite_endpoint": "POST /agents/referrals/invite",
        "dashboard_endpoint": "GET /agents/me/referrals",
    }


def build_referral_dashboard(root: Path, agent_id: str) -> dict[str, Any]:
    referrals = list_referrals_for_agent(root, agent_id)
    completed = sum(1 for r in referrals if r.get("status") in {"onboarding_complete", "payout_pending", "paid"})
    pending = sum(1 for r in referrals if r.get("status") == "pending_onboarding")
    return {
        "agent_id": agent_id,
        "referral_code": referral_code_for_agent(agent_id),
        "enabled": referral_program_enabled(),
        "reward_usd": referral_reward_usd(),
        "currency": "USDC",
        "stats": {
            "total_referrals": len(referrals),
            "pending_onboarding": pending,
            "completed": completed,
            "total_rewards_usd": round(completed * referral_reward_usd(), 2),
        },
        "referrals": referrals,
    }