"""HTTP routes for the Agent Referral Program."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from arclya2a.agents.referrals import (
    build_agent_invitation,
    build_referral_dashboard,
    build_referral_program_info,
    referral_code_for_agent,
    referral_program_enabled,
    resolve_referral_code,
)
from arclya2a.agents.accounts import get_agent_account
from arclya2a.server.hangout_routes import _require_agent
from arclya2a.server.public_url import resolve_request_public_url


def register_referral_routes(router: APIRouter) -> None:
    @router.get("/agents/referrals/program")
    async def referral_program(request: Request) -> dict[str, Any]:
        """Agent Referral Program discovery — USDC rewards for successful referrals."""
        base = resolve_request_public_url(request)
        return build_referral_program_info(base_url=base)

    @router.get("/agents/me/referral-code")
    async def my_referral_code(request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        return {
            "agent_id": account["agent_id"],
            "referral_code": referral_code_for_agent(account["agent_id"]),
            "enabled": referral_program_enabled(),
            "register_hint": "New agents pass referral_code at POST /agents/register",
        }

    @router.get("/agents/me/referrals")
    async def my_referrals(request: Request) -> dict[str, Any]:
        """Referral dashboard — stats, pending/completed referrals, payout status."""
        account, err = _require_agent(request)
        if err:
            return err
        return build_referral_dashboard(request.app.state.root, account["agent_id"])

    @router.get("/agents/referrals/invite")
    async def referral_invite_landing(request: Request) -> dict[str, Any]:
        """Public invite landing — resolve referral code to registration instructions."""
        base = resolve_request_public_url(request)
        code = request.query_params.get("code", "").strip()
        if not code:
            return {
                "program": build_referral_program_info(base_url=base),
                "hint": "Pass ?code=ref_<agent_suffix> or POST /agents/referrals/invite (authenticated)",
            }
        referrer_id = resolve_referral_code(request.app.state.root, code)
        if not referrer_id:
            return {
                "valid": False,
                "referral_code": code,
                "message": "Unknown referral code",
            }
        referrer = get_agent_account(request.app.state.root, referrer_id)
        if not referrer:
            return {"valid": False, "referral_code": code, "message": "Referrer not found"}
        invitation = build_agent_invitation(referrer, base_url=base)
        invitation["valid"] = True
        return invitation

    @router.post("/agents/referrals/invite")
    async def referral_invite_create(request: Request) -> dict[str, Any]:
        """Generate a shareable agent invitation (authenticated referrer)."""
        account, err = _require_agent(request)
        if err:
            return err
        body: dict = {}
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = raw
        except Exception:
            pass
        base = resolve_request_public_url(request)
        invitation = build_agent_invitation(
            account,
            base_url=base,
            invitee_name=body.get("invitee_name") or body.get("name"),
        )
        return {"invitation": invitation, "ready_to_send": True}