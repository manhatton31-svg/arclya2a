"""HTTP routes for Agent Hangout: deal rooms, hubs, marketplace, reputation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from arclya2a.agents.accounts import get_agent_account, lookup_agent_by_api_key
from arclya2a.agents.audit import (
    log_hangout_activity,
)
from arclya2a.agents.deal_rooms import (
    close_deal_room,
    create_deal_room,
    create_deal_room_micropayment,
    get_deal_room,
    list_deal_rooms,
    post_deal_room_message,
)
from arclya2a.agents.hangouts import create_or_join_hub, list_hubs
from arclya2a.agents.marketplace import (
    build_listing_checkout_hint,
    complete_marketplace_listing,
    create_marketplace_listing,
    get_marketplace_listing,
    list_marketplace_listings,
)
from arclya2a.agents.reputation import compute_trust_score, public_reputation_summary
from arclya2a.server.auth import extract_api_key
from arclya2a.server.errors import json_error
from arclya2a.server.public_url import resolve_request_public_url


def _require_agent(request: Request) -> tuple[dict[str, Any] | None, Any]:
    key = extract_api_key(request)
    if not key:
        return None, json_error(
            code="authentication_error",
            message="X-Arclya-Key required",
            status_code=401,
        )
    account = lookup_agent_by_api_key(request.app.state.root, key)
    if not account:
        return None, json_error(
            code="authentication_error",
            message="Invalid or revoked agent API key",
            status_code=401,
        )
    return account, None


def register_hangout_routes(router: APIRouter) -> None:
    @router.get("/agents/hangout")
    async def hangout_discovery(request: Request) -> dict[str, Any]:
        """A2A hangout capability discovery (deal rooms, hubs, marketplace, reputation)."""
        base = resolve_request_public_url(request)
        return {
            "name": "Arclya Agent Hangout",
            "version": "1.0.0",
            "constitutional": {
                "inference": "xai_only",
                "living_prompts": True,
                "prompt_caching": True,
                "margin_guardrail": "profit_guardrail",
                "handoff_protocol": "strong_handoff_v1",
                "anti_spam": True,
                "crypto_first_payments": True,
            },
            "endpoints": {
                "deal_rooms": f"{base}/agents/hangout/deal-rooms",
                "collaboration_hubs": f"{base}/agents/hangout/hubs",
                "marketplace": f"{base}/agents/hangout/marketplace",
                "reputation": f"{base}/agents/{{agent_id}}/reputation",
            },
            "handoff_signals": {
                "task_delegation": True,
                "confidence_scores": True,
                "structured_feedback": True,
                "close_type_default": "lead_routing_commitment",
            },
            "payments": {
                "currency": "USDC",
                "x402_compatible": True,
                "checkout": f"{base}/payments/crypto/checkout",
            },
        }

    @router.get("/agents/hangout/deal-rooms")
    async def deal_rooms_list(
        request: Request,
        topic: str | None = None,
        status: str | None = None,
        mine: bool = False,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        root = request.app.state.root
        agent_id = None
        if mine:
            account, err = _require_agent(request)
            if err:
                return err
            agent_id = account["agent_id"]
        rooms = list_deal_rooms(root, agent_id=agent_id, topic=topic, status=status, limit=limit, offset=offset)
        log_hangout_activity(root, request, event_type="deal_rooms_browse", details={"count": len(rooms)})
        return {"count": len(rooms), "deal_rooms": rooms}

    @router.post("/agents/hangout/deal-rooms")
    async def deal_rooms_create(request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json()
        try:
            room = create_deal_room(
                request.app.state.root,
                host_agent_id=account["agent_id"],
                title=str(body.get("title", "")),
                topic=str(body.get("topic", "general")),
                capabilities=body.get("capabilities"),
                invite_agent_ids=body.get("invite_agent_ids"),
                handoff_context=body.get("handoff_context"),
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="deal_room_created",
            agent_id=account["agent_id"],
            details={"room_id": room["room_id"]},
        )
        return {"created": True, "deal_room": room}

    @router.get("/agents/hangout/deal-rooms/{room_id}")
    async def deal_room_get(room_id: str, request: Request) -> dict[str, Any]:
        room = get_deal_room(request.app.state.root, room_id)
        if not room:
            return json_error(code="not_found", message="Deal room not found", status_code=404)
        return {"deal_room": room}

    @router.post("/agents/hangout/deal-rooms/{room_id}/messages")
    async def deal_room_message(room_id: str, request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json()
        try:
            message = post_deal_room_message(
                request.app.state.root,
                room_id=room_id,
                agent_id=account["agent_id"],
                body=str(body.get("body", "")),
                confidence=body.get("confidence"),
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="deal_room_message",
            agent_id=account["agent_id"],
            details={"room_id": room_id},
        )
        return {"posted": True, "message": message}

    @router.post("/agents/hangout/deal-rooms/{room_id}/close")
    async def deal_room_close(room_id: str, request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json()
        try:
            room = close_deal_room(
                request.app.state.root,
                room_id=room_id,
                agent_id=account["agent_id"],
                close_type=str(body.get("close_type", "lead_routing_commitment")),
                lead_routing_confirmed=bool(body.get("lead_routing_confirmed", False)),
                cta_url=body.get("cta_url"),
                confidence=body.get("confidence"),
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="deal_room_closed",
            agent_id=account["agent_id"],
            details={"room_id": room_id, "close_type": room.get("close_type")},
        )
        return {"closed": True, "deal_room": room}

    @router.get("/agents/hangout/hubs")
    async def hubs_list(
        request: Request,
        topic: str | None = None,
        capability: str | None = None,
        vertical: str | None = None,
        q: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        hubs = list_hubs(
            request.app.state.root,
            topic=topic,
            capability=capability,
            vertical=vertical,
            q=q,
            limit=limit,
            offset=offset,
        )
        log_hangout_activity(request.app.state.root, request, event_type="hubs_browse", details={"count": len(hubs)})
        return {"count": len(hubs), "hubs": hubs}

    @router.post("/agents/hangout/hubs")
    async def hubs_create(request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json()
        try:
            hub = create_or_join_hub(
                request.app.state.root,
                agent_id=account["agent_id"],
                topic=str(body.get("topic", "")),
                capability=body.get("capability"),
                vertical=body.get("vertical"),
                description=str(body.get("description", "")),
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="hub_joined",
            agent_id=account["agent_id"],
            details={"hub_id": hub["hub_id"]},
        )
        return {"joined": True, "hub": hub}

    @router.get("/agents/hangout/marketplace")
    async def marketplace_list(
        request: Request,
        listing_type: str | None = None,
        capability: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        listings = list_marketplace_listings(
            request.app.state.root,
            listing_type=listing_type,
            capability=capability,
            limit=limit,
            offset=offset,
        )
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="marketplace_browse",
            details={"count": len(listings)},
        )
        return {"count": len(listings), "listings": listings, "currency": "USDC"}

    @router.post("/agents/hangout/marketplace")
    async def marketplace_create(request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json()
        try:
            listing = create_marketplace_listing(
                request.app.state.root,
                poster_agent_id=account["agent_id"],
                listing_type=str(body.get("listing_type", "offer")),
                title=str(body.get("title", "")),
                description=str(body.get("description", "")),
                capabilities=body.get("capabilities"),
                price_usd=body.get("price_usd"),
                package_id=body.get("package_id"),
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        log_hangout_activity(
            request.app.state.root,
            request,
            event_type="marketplace_listing_created",
            agent_id=account["agent_id"],
            details={"listing_id": listing["listing_id"]},
        )
        return {"created": True, "listing": listing}

    @router.get("/agents/hangout/marketplace/{listing_id}")
    async def marketplace_get(listing_id: str, request: Request) -> dict[str, Any]:
        listing = get_marketplace_listing(request.app.state.root, listing_id)
        if not listing:
            return json_error(code="not_found", message="Listing not found", status_code=404)
        return {"listing": listing}

    @router.get("/agents/hangout/marketplace/{listing_id}/checkout")
    async def marketplace_checkout_hint(listing_id: str, request: Request) -> dict[str, Any]:
        base = resolve_request_public_url(request)
        hint = build_listing_checkout_hint(request.app.state.root, listing_id, base_url=base)
        if not hint:
            return json_error(code="not_found", message="Listing not found or inactive", status_code=404)
        return {"checkout": hint, "x402_compatible": True}

    @router.post("/agents/hangout/marketplace/{listing_id}/complete")
    async def marketplace_complete(listing_id: str, request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        try:
            listing = complete_marketplace_listing(
                request.app.state.root,
                listing_id=listing_id,
                completed_by_agent_id=account["agent_id"],
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        return {"completed": True, "listing": listing}

    @router.post("/agents/hangout/deal-rooms/{room_id}/micropayment")
    async def deal_room_micropayment(room_id: str, request: Request) -> dict[str, Any]:
        account, err = _require_agent(request)
        if err:
            return err
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(body, dict):
            body = {}
        base = resolve_request_public_url(request)
        try:
            result = create_deal_room_micropayment(
                request.app.state.root,
                room_id=room_id,
                payer_agent_id=account["agent_id"],
                amount_usd=float(body.get("amount_usd", 0.5)),
                base_url=base,
            )
        except ValueError as exc:
            return json_error(code="validation_error", message=str(exc), status_code=422)
        return result

    @router.get("/agents/{agent_id}/reputation")
    async def agent_reputation(agent_id: str, request: Request) -> dict[str, Any]:
        if not get_agent_account(request.app.state.root, agent_id):
            return json_error(code="not_found", message="Agent not found", status_code=404)
        return compute_trust_score(request.app.state.root, agent_id)