"""HTTP routes for external agent account registration and profiles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse

from arclya2a.agents.accounts import (
    DEFAULT_DIRECTORY_SORT,
    VALID_DIRECTORY_SORTS,
    detailed_public_profile,
    get_agent_account,
    is_active_agent_status,
    is_valid_agent_id,
    list_directory_agents,
    list_recommended_agents,
    lookup_agent_by_api_key,
    normalize_agent_status,
    private_profile,
    public_profile,
    register_agent_account,
    rotate_agent_api_key,
    set_agent_status,
    update_agent_profile,
    validate_registration_input,
)
from arclya2a.agents.moderation import (
    list_agent_accounts_for_operator,
    operator_agent_entry,
)
from arclya2a.agents.onboarding_guide import (
    build_agent_onboarding_guide,
    build_registration_welcome,
)
from arclya2a.agents.audit import (
    build_agent_audit_summary,
    log_agent_auth_failure,
    log_agent_directory_activity,
    log_agent_directory_listing_change,
    log_agent_api_key_rotated,
    log_agent_email_verified,
    log_agent_feedback_submitted,
    log_agent_preferences_updated,
    log_agent_profile_update,
    log_agent_registration,
    log_agent_status_change,
    log_agent_terms_accepted,
    read_agent_audit_events,
)
from arclya2a.agents.feedback import (
    build_feedback_ops_summary,
    list_agent_feedback,
    submit_agent_feedback,
    validate_feedback_input,
)
from arclya2a.agents.preferences import (
    account_preferences,
    update_agent_preferences,
    validate_preferences_patch,
)
from arclya2a.agents.terms import build_terms_info
from arclya2a.agents.email_verification import (
    VERIFICATION_ERROR_HINTS,
    build_email_verification_status,
    classify_verification_error,
    directory_requires_email_verification,
    operator_verification_outbox_summary,
    queue_agent_email_verification,
    run_background_verification_email,
    verification_email_uses_background_delivery,
    verify_email_token,
)
from arclya2a.agents.security import (
    build_agent_auth_error,
    check_agent_registration_allowed,
    log_agent_registration_attempt,
    validate_directory_query,
)
from arclya2a.server.auth import extract_api_key
from arclya2a.server.errors import json_error
from arclya2a.server.operator_auth import load_operator_key, verify_operator_key
from arclya2a.server.public_url import resolve_request_public_url


def _agent_auth_error_response(request: Request) -> JSONResponse:
    auth_err = build_agent_auth_error(
        request.url.path,
        provided_key=extract_api_key(request),
        root=request.app.state.root,
    )
    log_agent_auth_failure(request.app.state.root, request, auth_err)
    return json_error(
        code=auth_err["code"],
        message=auth_err["message"],
        details=auth_err.get("details"),
        status_code=auth_err["status_code"],
    )


def _require_operator(request: Request) -> JSONResponse | None:
    if not verify_operator_key(request, configured_key=load_operator_key()):
        return json_error(
            code="operator_authentication_error",
            message="Valid operator key required (X-Arclya-Operator-Key)",
            details={"hint": "Set ARCLYA_OPERATOR_KEY and send it on this request"},
            status_code=401,
        )
    return None


def _operator_id(request: Request) -> str:
    return (
        request.headers.get("X-Arclya-Operator-Id", "").strip()
        or "operator"
    )


def _schedule_email_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    *,
    account: dict[str, Any],
    base_url: str,
) -> dict[str, Any] | None:
    """Queue verification email; SMTP delivery runs after the HTTP response."""
    queued = queue_agent_email_verification(
        request.app.state.root,
        account=account,
        base_url=base_url,
        deliver_smtp_in_background=verification_email_uses_background_delivery(),
    )
    if not queued:
        return None
    token = queued.pop("_token", None)
    if queued.get("queued") and token:
        background_tasks.add_task(
            run_background_verification_email,
            request.app.state.root,
            account=account,
            token=token,
            base_url=base_url,
        )
    return queued


def _email_verification_api_payload(
    account: dict[str, Any],
    email_verification: dict[str, Any],
) -> dict[str, Any]:
    """Public email_verification block for register / resend / profile responses."""
    payload: dict[str, Any] = {
        "required_for_directory": directory_requires_email_verification(),
        "sent": email_verification.get("sent", False),
        "delivery": email_verification.get("delivery"),
        "delivery_mode": email_verification.get("delivery_mode"),
        "verify_endpoint": "POST /agents/verify-email",
        "resend_endpoint": "POST /agents/me/resend-verification",
        "expires_in_hours": email_verification.get("expires_in_hours"),
        "message": email_verification.get("message"),
        "status": email_verification.get("status") or build_email_verification_status(account),
    }
    if email_verification.get("queued"):
        payload["queued"] = True
    if email_verification.get("verify_link"):
        payload["verify_link"] = email_verification["verify_link"]
    if email_verification.get("smtp_error"):
        payload["smtp_error"] = email_verification["smtp_error"]
    if email_verification.get("error_code"):
        payload["error_code"] = email_verification["error_code"]
    if email_verification.get("next_step"):
        payload["next_step"] = email_verification["next_step"]
    if email_verification.get("delivery_blockers"):
        payload["delivery_blockers"] = email_verification["delivery_blockers"]
    if email_verification.get("operator_hint"):
        payload["operator_hint"] = email_verification["operator_hint"]
    if email_verification.get("production_delivery") is not None:
        payload["production_delivery"] = email_verification["production_delivery"]
    return payload


def _resolve_authenticated_account(request: Request) -> dict[str, Any] | None:
    caller = getattr(request.state, "caller", None) or {}
    agent_id = caller.get("agent_id")
    if agent_id:
        account = get_agent_account(request.app.state.root, agent_id)
        if account and is_active_agent_status(account.get("status")):
            return account

    api_key = extract_api_key(request)
    if api_key:
        return lookup_agent_by_api_key(request.app.state.root, api_key)
    return None


def _directory_response(
    root,
    *,
    capabilities: list[str] | None,
    search: str | None,
    offset: int,
    limit: int,
    sort: str,
    recommended_for: dict[str, Any] | None = None,
    exclude_agent_id: str | None = None,
) -> dict[str, Any]:
    requested_sort = sort
    result = list_directory_agents(
        root,
        capabilities=capabilities,
        search=search,
        offset=offset,
        limit=limit,
        sort=sort,
        recommended_for=recommended_for,
        exclude_agent_id=exclude_agent_id,
    )
    response: dict[str, Any] = {
        "total": result["total"],
        "count": len(result["agents"]),
        "agents": result["agents"],
        "mode": result["mode"],
        "scoring_active": result["scoring_active"],
        "pagination": {
            "offset": result["offset"],
            "limit": result["limit"],
            "sort": result["sort"],
        },
        "filters": {
            "capabilities": result["capability_filters"],
            "q": search,
            "recommended": recommended_for is not None,
        },
    }
    if requested_sort not in VALID_DIRECTORY_SORTS:
        response["pagination"]["sort_fallback"] = (
            f"Unknown sort '{requested_sort}' — using {result['sort']}"
        )
    elif requested_sort != result["sort"]:
        response["pagination"]["sort_fallback"] = (
            f"Sort '{requested_sort}' not applicable — using {result['sort']}"
        )
    return response


def register_agent_account_routes(router: APIRouter) -> None:
    """Register external agent account endpoints."""

    @router.get("/agents/onboarding/guide")
    async def agents_onboarding_guide(request: Request) -> dict[str, Any]:
        """Guided JSON flow for external agent registration and directory participation."""
        base_url = resolve_request_public_url(request)
        return build_agent_onboarding_guide(base_url=base_url)

    @router.get("/agents/services")
    @router.get("/discovery/services")
    async def agents_service_catalog(
        request: Request,
        capability: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Machine-readable service catalog for autonomous agent discovery."""
        from arclya2a.agents.service_catalog import build_service_catalog

        base_url = resolve_request_public_url(request)
        return build_service_catalog(
            request.app.state.root,
            base_url=base_url,
            capability=capability,
            q=q,
        )

    @router.get("/agents/terms")
    async def agents_terms(request: Request) -> dict[str, Any]:
        """Current Terms of Service / Acceptable Use Policy metadata for external agents."""
        base_url = resolve_request_public_url(request)
        return build_terms_info(base_url=base_url)

    def _browse_directory(
        request: Request,
        *,
        capability: list[str] | None,
        q: str | None,
        offset: int,
        limit: int,
        sort: str,
        recommended: bool,
    ) -> dict[str, Any] | JSONResponse:
        normalized, issues = validate_directory_query(
            capabilities=capability,
            search=q,
            offset=offset,
            limit=limit,
            sort=sort,
        )
        if issues:
            return json_error(
                code="validation_error",
                message="Directory query validation failed",
                details={"fields": issues},
                status_code=422,
            )
        assert normalized is not None

        viewer: dict[str, Any] | None = None
        if recommended:
            viewer = _resolve_authenticated_account(request)
            if not viewer:
                return _agent_auth_error_response(request)
        response = _directory_response(
            request.app.state.root,
            capabilities=normalized["capabilities"],
            search=normalized["search"],
            offset=normalized["offset"],
            limit=normalized["limit"],
            sort=normalized["sort"],
            recommended_for=viewer,
            exclude_agent_id=viewer.get("agent_id") if viewer else None,
        )
        log_agent_directory_activity(
            request.app.state.root,
            request,
            mode=response["mode"],
            viewer_agent_id=viewer.get("agent_id") if viewer else None,
            filters={
                **response["filters"],
                "pagination": response["pagination"],
            },
            result_count=response["count"],
            total=response["total"],
        )
        return response

    @router.post("/agents/verify-email")
    async def agents_verify_email(request: Request) -> dict[str, Any]:
        """Verify agent email using a token from the verification email."""
        token: str | None = None
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                token = str(body.get("token") or "").strip() or None
        if not token:
            token = request.query_params.get("token")
        if not token:
            return json_error(
                code="validation_error",
                message="Verification token required",
                details={
                    "hint": "POST {\"token\": \"ev_...\"} or GET ?token=ev_...",
                    "resend": "POST /agents/me/resend-verification (authenticated)",
                },
                status_code=422,
            )

        updated, err = verify_email_token(request.app.state.root, token)
        if err:
            error_code = classify_verification_error(err)
            hint = VERIFICATION_ERROR_HINTS.get(error_code, {})
            return json_error(
                code="verification_failed",
                message=hint.get("message") or err,
                details={
                    "error_code": error_code,
                    "reason": err,
                    "next_step": hint.get("next_step"),
                    "resend_endpoint": "POST /agents/me/resend-verification",
                },
                status_code=422,
            )

        log_agent_email_verified(request.app.state.root, account=updated)

        from arclya2a.agents.referrals import try_complete_referral

        referral_result = try_complete_referral(request.app.state.root, str(updated.get("agent_id", "")))

        response: dict[str, Any] = {
            "verified": True,
            "agent_id": updated.get("agent_id"),
            "email_verified": True,
            "message": (
                "Email verified. You may now opt in to the Agent Directory with "
                "PATCH /agents/me {\"publicly_listed\": true}"
            ),
            "email_verification": build_email_verification_status(updated),
            "profile": public_profile(updated),
        }
        if referral_result:
            response["referral_completion"] = {
                "status": referral_result.get("status"),
                "referral_id": referral_result.get("referral_id"),
                "payout_payment_id": referral_result.get("payout_payment_id"),
            }
        return response

    @router.get("/agents/verify-email")
    async def agents_verify_email_link(request: Request) -> dict[str, Any]:
        """Verify agent email via link (?token=ev_...)."""
        return await agents_verify_email(request)

    @router.post("/agents/me/resend-verification")
    async def agents_resend_verification(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        """Resend the email verification message for the authenticated agent."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)
        if not account.get("email"):
            return json_error(
                code="validation_error",
                message="No email on file — add one via PATCH /agents/me",
                status_code=422,
            )
        if account.get("email_verified"):
            return {
                "resent": False,
                "already_verified": True,
                "email_verified": True,
                "message": "Email is already verified",
            }
        base_url = resolve_request_public_url(request)
        delivery = _schedule_email_verification(
            request,
            background_tasks,
            account=account,
            base_url=base_url,
        )
        queued = bool((delivery or {}).get("queued"))
        sent = bool((delivery or {}).get("sent"))
        message = (delivery or {}).get("message") or (
            "Verification email is being sent — check your inbox"
            if queued
            else ("Verification email sent — check your inbox" if sent else "Verification email was not delivered")
        )
        response: dict[str, Any] = {
            "resent": sent or queued,
            "queued": queued,
            "email": account.get("email"),
            "verification": delivery,
            "message": message,
            "email_verification": build_email_verification_status(account),
        }
        if delivery and delivery.get("smtp_error"):
            response["smtp_error"] = delivery["smtp_error"]
        if delivery and delivery.get("error_code"):
            response["error_code"] = delivery["error_code"]
        if delivery and delivery.get("next_step"):
            response["next_step"] = delivery["next_step"]
        if delivery and delivery.get("delivery_blockers"):
            response["delivery_blockers"] = delivery["delivery_blockers"]
        if delivery and delivery.get("operator_hint"):
            response["operator_hint"] = delivery["operator_hint"]
        return response

    @router.get("/agents/manage")
    async def agents_manage(
        request: Request,
        status: str | None = None,
        publicly_listed: bool | None = None,
        q: str | None = None,
        recently_active: bool = False,
        offset: int = 0,
        limit: int = 50,
        sort: str = "created_at_desc",
    ) -> dict[str, Any]:
        """Operator-only list of all external agent accounts with moderation filters."""
        denied = _require_operator(request)
        if denied:
            return denied
        return list_agent_accounts_for_operator(
            request.app.state.root,
            status=status,
            publicly_listed=publicly_listed,
            q=q,
            recently_active=recently_active,
            offset=max(0, offset),
            limit=limit,
            sort=sort,
        )

    @router.get("/agents/audit")
    async def agents_audit(
        request: Request,
        limit: int = 100,
        event_type: str | None = None,
        agent_id: str | None = None,
        suspicious_only: bool = False,
    ) -> dict[str, Any]:
        """Operator-only view of recent external agent audit events."""
        denied = _require_operator(request)
        if denied:
            return denied
        events = read_agent_audit_events(
            request.app.state.root,
            limit=limit,
            event_type=event_type,
            agent_id=agent_id,
            suspicious_only=suspicious_only,
        )
        summary = build_agent_audit_summary(request.app.state.root, recent_limit=min(limit, 15))
        return {
            "count": len(events),
            "events": events,
            "summary": {
                "total_events": summary["total_events"],
                "counts_24h": summary["counts_24h"],
                "suspicious_24h": summary["suspicious_24h"],
                "audit_log": summary["audit_log"],
            },
            "filters": {
                "limit": limit,
                "event_type": event_type,
                "agent_id": agent_id,
                "suspicious_only": suspicious_only,
            },
        }

    @router.get("/agents/recommended")
    async def agents_recommended(
        request: Request,
        capability: list[str] | None = Query(None),
        q: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort: str = "match_score",
    ) -> dict[str, Any]:
        """Recommended agents for the authenticated viewer (capability overlap)."""
        normalized, issues = validate_directory_query(
            capabilities=capability,
            search=q,
            offset=offset,
            limit=limit,
            sort=sort,
        )
        if issues:
            return json_error(
                code="validation_error",
                message="Directory query validation failed",
                details={"fields": issues},
                status_code=422,
            )
        assert normalized is not None

        viewer = _resolve_authenticated_account(request)
        if not viewer:
            return _agent_auth_error_response(request)
        result = list_recommended_agents(
            request.app.state.root,
            viewer,
            capabilities=normalized["capabilities"],
            search=normalized["search"],
            offset=normalized["offset"],
            limit=normalized["limit"],
            sort=normalized["sort"],
        )
        response = {
            "total": result["total"],
            "count": len(result["agents"]),
            "agents": result["agents"],
            "mode": result["mode"],
            "scoring_active": result["scoring_active"],
            "viewer_agent_id": viewer.get("agent_id"),
            "viewer_capabilities": viewer.get("capabilities", []),
            "pagination": {
                "offset": result["offset"],
                "limit": result["limit"],
                "sort": result["sort"],
            },
            "filters": {
                "capabilities": result["capability_filters"],
                "q": q,
                "recommended": True,
            },
        }
        log_agent_directory_activity(
            request.app.state.root,
            request,
            mode=result["mode"],
            viewer_agent_id=viewer.get("agent_id"),
            filters={
                **response["filters"],
                "pagination": response["pagination"],
            },
            result_count=response["count"],
            total=response["total"],
        )
        return response

    @router.get("/agents")
    async def agents_directory(
        request: Request,
        capability: list[str] | None = Query(None),
        q: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort: str = DEFAULT_DIRECTORY_SORT,
        recommended: bool = False,
    ) -> dict[str, Any]:
        """Public Agent Directory — lists agents who opted in to being discoverable."""
        return _browse_directory(
            request,
            capability=capability,
            q=q,
            offset=offset,
            limit=limit,
            sort=sort,
            recommended=recommended,
        )

    @router.get("/agents/directory")
    async def agents_directory_alias(
        request: Request,
        capability: list[str] | None = Query(None),
        q: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort: str = DEFAULT_DIRECTORY_SORT,
        recommended: bool = False,
    ) -> dict[str, Any]:
        """Alias for GET /agents — public Agent Hangout directory."""
        return _browse_directory(
            request,
            capability=capability,
            q=q,
            offset=offset,
            limit=limit,
            sort=sort,
            recommended=recommended,
        )

    @router.post("/agents/register")
    async def agents_register(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Self-service registration for external agents (no auth required)."""
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                details={"hint": "Send Content-Type: application/json with agent_name and optional profile fields"},
                status_code=422,
            )

        if not isinstance(body, dict):
            return json_error(
                code="validation_error",
                message="Request body must be a JSON object",
                status_code=422,
            )

        client_ip = request.client.host if request.client else None
        allowed, deny_reason = check_agent_registration_allowed(
            request.app.state.root,
            client_ip=client_ip,
        )
        if not allowed:
            log_agent_registration_attempt(
                request.app.state.root,
                agent_name=str(body.get("agent_name") or body.get("display_name") or ""),
                client_ip=client_ip,
                success=False,
                reason=deny_reason,
            )
            return json_error(
                code="registration_denied",
                message=deny_reason or "Agent registration not allowed",
                details={"hint": "Try again tomorrow or contact support"},
                status_code=429,
            )

        agent_name = (body.get("agent_name") or body.get("display_name") or "")
        email = body.get("email")
        description = body.get("description") or body.get("bio")
        capabilities = body.get("capabilities")

        terms_accepted = body.get("terms_accepted")
        if terms_accepted is None and "accept_terms" in body:
            terms_accepted = body.get("accept_terms")

        field_issues = validate_registration_input(
            request.app.state.root,
            agent_name=agent_name,
            email=email,
            description=description,
            capabilities=capabilities,
            terms_accepted=terms_accepted,
        )
        if field_issues:
            log_agent_registration_attempt(
                request.app.state.root,
                agent_name=agent_name,
                client_ip=client_ip,
                success=False,
                reason="validation_failed",
            )
            return json_error(
                code="validation_error",
                message="Registration validation failed",
                details={"fields": field_issues},
                status_code=422,
            )

        referral_code = body.get("referral_code")

        account, api_key, err = register_agent_account(
            request.app.state.root,
            agent_name=agent_name,
            email=email,
            description=description,
            capabilities=capabilities,
            terms_accepted=terms_accepted,
            referral_code=str(referral_code).strip() if referral_code else None,
        )
        if err:
            log_agent_registration_attempt(
                request.app.state.root,
                agent_name=agent_name,
                client_ip=client_ip,
                success=False,
                reason=err,
            )
            return json_error(code="validation_error", message=err, status_code=422)

        log_agent_registration_attempt(
            request.app.state.root,
            agent_name=agent_name,
            client_ip=client_ip,
            success=True,
            agent_id=account["agent_id"],
        )
        log_agent_registration(
            request.app.state.root,
            account=account,
            client_ip=client_ip,
        )
        log_agent_terms_accepted(
            request.app.state.root,
            account=account,
            path="/agents/register",
            method="POST",
        )
        base_url = resolve_request_public_url(request)
        welcome = build_registration_welcome(account, base_url=base_url)
        email_verification = None
        if account.get("email"):
            email_verification = _schedule_email_verification(
                request,
                background_tasks,
                account=account,
                base_url=base_url,
            )

        response: dict[str, Any] = {
            "registered": True,
            "agent_id": account["agent_id"],
            "agent_name": account["agent_name"],
            "api_key": api_key,
            "api_key_prefix": account["api_key_prefix"],
            "status": account["status"],
            "created_at": account["created_at"],
            "email_verified": False,
            "terms_version": account.get("terms_version"),
            "terms_accepted": True,
            "terms": build_terms_info(base_url=base_url),
            "profile": public_profile(account),
            **welcome,
        }
        if email_verification:
            response["email_verification"] = _email_verification_api_payload(account, email_verification)
        elif directory_requires_email_verification():
            response["email_verification"] = {
                **build_email_verification_status(account),
                "sent": False,
                "message": (
                    "Add an email via PATCH /agents/me to receive a verification link "
                    "before joining the directory."
                ),
            }
        return response

    @router.get("/agents/me")
    async def agents_me(request: Request) -> dict[str, Any]:
        """Return the authenticated agent's profile (requires production API key)."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)
        from arclya2a.agents.referrals import referral_profile_summary

        profile = private_profile(account)
        profile["referral_program"] = referral_profile_summary(account["agent_id"])
        return profile

    @router.post("/agents/me/rotate-key")
    async def agents_rotate_key(request: Request) -> dict[str, Any]:
        """Rotate the authenticated agent's API key (current key required; shown once)."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)

        current_key = extract_api_key(request)
        new_key, updated, err = rotate_agent_api_key(
            request.app.state.root,
            account["agent_id"],
            current_key=current_key,
            rotated_by="agent",
        )
        if err:
            code = "validation_error"
            status = 422
            if err == "Agent account not found":
                status = 404
            elif err in {"Agent account is suspended", "Agent account is pending review"}:
                code = "forbidden"
                status = 403
            return json_error(code=code, message=err, status_code=status)

        revoked = updated.pop("_revoked_key_prefixes", [])
        log_agent_api_key_rotated(
            request.app.state.root,
            account=updated,
            rotated_by="agent",
            revoked_key_prefixes=revoked,
        )
        return {
            "rotated": True,
            "agent_id": updated.get("agent_id"),
            "api_key": new_key,
            "api_key_prefix": updated.get("api_key_prefix"),
            "api_key_reminder": {
                "importance": "critical",
                "shown_once": True,
                "message": (
                    "Store this new api_key immediately. Your previous key is revoked and "
                    "can no longer authenticate."
                ),
            },
            "revoked_key_count": len(revoked),
            "message": (
                "API key rotated successfully. Update your secret store before discarding "
                "the old key."
            ),
            "profile": private_profile(updated),
        }

    @router.patch("/agents/me/preferences")
    async def agents_me_preferences_update(request: Request) -> dict[str, Any]:
        """Update feature preferences for the authenticated agent."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                status_code=422,
            )
        if not isinstance(body, dict):
            return json_error(
                code="validation_error",
                message="Request body must be a JSON object",
                status_code=422,
            )

        patch, err = validate_preferences_patch(body)
        if err:
            return json_error(
                code="validation_error",
                message=err,
                details={
                    "allowed_fields": ["wants_human_closing", "preferred_closing_method"],
                    "closing_methods": ["agent_only", "human_only", "hybrid"],
                },
                status_code=422,
            )
        assert patch is not None

        updated, changed, update_err = update_agent_preferences(
            request.app.state.root,
            account["agent_id"],
            wants_human_closing=patch.get("wants_human_closing"),
            preferred_closing_method=patch.get("preferred_closing_method"),
        )
        if update_err:
            code = "validation_error"
            status = 422
            if update_err == "Agent account not found":
                status = 404
            elif update_err in {"Agent account is suspended", "Agent account is pending review"}:
                code = "forbidden"
                status = 403
            return json_error(code=code, message=update_err, status_code=status)

        client_ip = request.client.host if request.client else None
        log_agent_preferences_updated(
            request.app.state.root,
            account=updated,
            changed_fields=changed,
            client_ip=client_ip,
        )
        return {
            "updated": True,
            "changed_fields": changed,
            "preferences": account_preferences(updated),
            "preferences_updated_at": updated.get("preferences_updated_at"),
            "profile": private_profile(updated),
        }

    @router.post("/agents/feedback")
    async def agents_submit_feedback(request: Request) -> dict[str, Any]:
        """Submit structured feedback (feature interest, human closing, etc.)."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                status_code=422,
            )
        if not isinstance(body, dict):
            return json_error(
                code="validation_error",
                message="Request body must be a JSON object",
                status_code=422,
            )

        payload, err = validate_feedback_input(body)
        if err:
            return json_error(
                code="validation_error",
                message=err,
                details={
                    "categories": ["feature_request", "closing_preference", "general", "bug_report"],
                    "feature_interests": [
                        "human_closing", "deal_rooms", "marketplace", "referrals", "crypto_payments", "other",
                    ],
                },
                status_code=422,
            )
        assert payload is not None

        pref_body: dict[str, Any] = {}
        if "wants_human_closing" in payload:
            pref_body["wants_human_closing"] = payload["wants_human_closing"]
        if payload.get("preferred_closing_method"):
            pref_body["preferred_closing_method"] = payload["preferred_closing_method"]
        if pref_body:
            pref_patch, _ = validate_preferences_patch(pref_body)
            if pref_patch:
                update_agent_preferences(
                    request.app.state.root,
                    account["agent_id"],
                    wants_human_closing=pref_patch.get("wants_human_closing"),
                    preferred_closing_method=pref_patch.get("preferred_closing_method"),
                )
                account = get_agent_account(request.app.state.root, account["agent_id"]) or account

        feedback = submit_agent_feedback(
            request.app.state.root,
            agent_id=account["agent_id"],
            agent_name=account.get("agent_name"),
            payload=payload,
        )
        client_ip = request.client.host if request.client else None
        log_agent_feedback_submitted(
            request.app.state.root,
            account=account,
            feedback=feedback,
            client_ip=client_ip,
        )
        return {
            "submitted": True,
            "feedback_id": feedback.get("feedback_id"),
            "category": feedback.get("category"),
            "learning_signal_id": feedback.get("learning_signal_id"),
            "message": "Thank you — your feedback was recorded and sent to the learning system",
            "preferences": account_preferences(account),
        }

    @router.patch("/agents/me")
    async def agents_me_update(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Update basic profile fields for the authenticated agent."""
        account = _resolve_authenticated_account(request)
        if not account:
            return _agent_auth_error_response(request)
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                status_code=422,
            )

        if not isinstance(body, dict):
            return json_error(
                code="validation_error",
                message="Request body must be a JSON object",
                status_code=422,
            )

        if not body:
            return json_error(
                code="validation_error",
                message="At least one profile field is required",
                details={
                    "allowed_fields": [
                        "agent_name",
                        "email",
                        "description",
                        "bio",
                        "capabilities",
                        "publicly_listed",
                        "terms_accepted",
                        "accept_terms",
                    ],
                },
                status_code=422,
            )

        terms_accepted = body.get("terms_accepted") if "terms_accepted" in body else None
        if terms_accepted is None and "accept_terms" in body:
            terms_accepted = body.get("accept_terms")
        if terms_accepted is not None and not isinstance(terms_accepted, bool):
            return json_error(
                code="validation_error",
                message="terms_accepted must be a boolean",
                details={"field": "terms_accepted", "received_type": type(terms_accepted).__name__},
                status_code=422,
            )

        publicly_listed = body.get("publicly_listed") if "publicly_listed" in body else None
        if publicly_listed is not None and not isinstance(publicly_listed, bool):
            return json_error(
                code="validation_error",
                message="publicly_listed must be a boolean",
                details={"field": "publicly_listed", "received_type": type(publicly_listed).__name__},
                status_code=422,
            )

        changed_fields = [
            field
            for field in (
                "agent_name",
                "email",
                "description",
                "capabilities",
                "publicly_listed",
                "terms_accepted",
            )
            if field in body or (field == "description" and "bio" in body)
        ]
        if "accept_terms" in body and "terms_accepted" not in changed_fields:
            changed_fields.append("terms_accepted")

        previous_terms = account.get("terms_version")
        updated, err = update_agent_profile(
            request.app.state.root,
            account["agent_id"],
            agent_name=body.get("agent_name") if "agent_name" in body else None,
            email=body.get("email") if "email" in body else None,
            description=(
                body.get("description")
                if "description" in body
                else (body.get("bio") if "bio" in body else None)
            ),
            capabilities=body.get("capabilities") if "capabilities" in body else None,
            publicly_listed=publicly_listed,
            terms_accepted=terms_accepted if isinstance(terms_accepted, bool) else None,
        )
        if err:
            code = "validation_error"
            status = 422
            if err == "Agent account not found":
                status = 404
            elif err == "Agent account is suspended":
                code = "forbidden"
                status = 403
            return json_error(code=code, message=err, status_code=status)

        client_ip = request.client.host if request.client else None
        if terms_accepted is True and updated.get("terms_version") != previous_terms:
            log_agent_terms_accepted(
                request.app.state.root,
                account=updated,
                path="/agents/me",
                method="PATCH",
            )
        verification_queued = None
        if "email" in body and updated.get("email") and not updated.get("email_verified"):
            verification_queued = _schedule_email_verification(
                request,
                background_tasks,
                account=updated,
                base_url=resolve_request_public_url(request),
            )

        if changed_fields:
            log_agent_profile_update(
                request.app.state.root,
                account=updated,
                changed_fields=changed_fields,
                client_ip=client_ip,
            )
        if publicly_listed is not None:
            log_agent_directory_listing_change(
                request.app.state.root,
                account=updated,
                publicly_listed=bool(publicly_listed),
                client_ip=client_ip,
            )

        listing_note = None
        if publicly_listed is True:
            listing_note = "Your agent is now visible in GET /agents and GET /agents/directory"
        elif publicly_listed is False:
            listing_note = "Your agent has been removed from the public directory"

        result: dict[str, Any] = {
            "updated": True,
            "profile": private_profile(updated),
            "listing_note": listing_note,
        }
        if verification_queued:
            result["email_verification"] = _email_verification_api_payload(updated, verification_queued)
        return result

    @router.get("/agents/{agent_id}/audit")
    async def agents_agent_audit(
        agent_id: str,
        request: Request,
        limit: int = 50,
        event_type: str | None = None,
        suspicious_only: bool = False,
    ) -> dict[str, Any]:
        """Operator-only audit trail for a single external agent."""
        denied = _require_operator(request)
        if denied:
            return denied
        if not is_valid_agent_id(agent_id):
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        account = get_agent_account(request.app.state.root, agent_id)
        if not account:
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        events = read_agent_audit_events(
            request.app.state.root,
            limit=limit,
            event_type=event_type,
            agent_id=agent_id,
            suspicious_only=suspicious_only,
        )
        return {
            "agent_id": agent_id,
            "agent_name": account.get("agent_name"),
            "status": normalize_agent_status(account.get("status")),
            "count": len(events),
            "events": events,
        }

    @router.post("/agents/{agent_id}/rotate-key")
    async def agents_operator_rotate_key(agent_id: str, request: Request) -> dict[str, Any]:
        """Operator-only: force-rotate an agent's API key (e.g. lost or compromised key recovery)."""
        denied = _require_operator(request)
        if denied:
            return denied
        if not is_valid_agent_id(agent_id):
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )

        reason: str | None = None
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            reason = str(body.get("reason") or "").strip() or None

        existing = get_agent_account(request.app.state.root, agent_id)
        if not existing:
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )

        new_key, updated, err = rotate_agent_api_key(
            request.app.state.root,
            agent_id,
            rotated_by="operator",
            operator_id=_operator_id(request),
            reason=reason,
        )
        if err:
            return json_error(code="validation_error", message=err, status_code=422)

        revoked = updated.pop("_revoked_key_prefixes", [])
        log_agent_api_key_rotated(
            request.app.state.root,
            account=updated,
            rotated_by="operator",
            revoked_key_prefixes=revoked,
            operator_id=_operator_id(request),
            reason=reason,
            path=f"/agents/{agent_id}/rotate-key",
        )
        return {
            "rotated": True,
            "agent_id": agent_id,
            "api_key": new_key,
            "api_key_prefix": updated.get("api_key_prefix"),
            "revoked_key_count": len(revoked),
            "operator_id": _operator_id(request),
            "reason": reason,
            "message": (
                "Operator key rotation complete. Deliver the new api_key to the agent securely — "
                "it is shown only in this response."
            ),
            "agent": operator_agent_entry(updated),
        }

    @router.get("/agents/operator/feedback")
    async def agents_operator_feedback(
        request: Request,
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        """Operator-only: agent feedback and preference summary."""
        denied = _require_operator(request)
        if denied:
            return denied
        summary = build_feedback_ops_summary(request.app.state.root)
        entries = list_agent_feedback(
            request.app.state.root,
            limit=limit,
            agent_id=agent_id,
        )
        return {
            "summary": summary,
            "entries": entries,
            "agent_id_filter": agent_id,
        }

    @router.get("/agents/operator/verification-outbox")
    async def agents_operator_verification_outbox(
        request: Request,
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=5, ge=1, le=20),
    ) -> dict[str, Any]:
        """Operator-only: recent verification email outbox (launch testing / support)."""
        denied = _require_operator(request)
        if denied:
            return denied
        if agent_id and not is_valid_agent_id(agent_id):
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        summary = operator_verification_outbox_summary(
            request.app.state.root,
            agent_id=agent_id,
            limit=limit,
        )
        latest = summary.get("latest")
        return {
            "agent_id": agent_id,
            "count": summary["count"],
            "latest": latest,
            "entries": summary["entries"],
            "pending_verifications": summary.get("pending_verifications", []),
            "pending_count": summary.get("pending_count", 0),
            "delivery_stats": summary.get("delivery_stats", {}),
            "delivery_mode_effective": summary.get("delivery_mode_effective"),
            "delivery_blockers": summary.get("delivery_blockers", []),
            "hint": (
                "Use latest.verify_link or latest.token for POST /agents/verify-email "
                "during launch smoke tests. pending_verifications lists agents awaiting verify."
            ),
        }

    @router.patch("/agents/{agent_id}/status")
    async def agents_set_status(agent_id: str, request: Request) -> dict[str, Any]:
        """Operator-only: suspend, reactivate, or mark an agent pending review."""
        denied = _require_operator(request)
        if denied:
            return denied
        if not is_valid_agent_id(agent_id):
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                status_code=422,
            )
        if not isinstance(body, dict) or "status" not in body:
            return json_error(
                code="validation_error",
                message="status is required",
                details={
                    "allowed_statuses": ["active", "suspended", "pending_review"],
                    "optional_fields": ["reason"],
                },
                status_code=422,
            )

        existing = get_agent_account(request.app.state.root, agent_id)
        if not existing:
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        previous_status = normalize_agent_status(existing.get("status"))
        updated, err = set_agent_status(
            request.app.state.root,
            agent_id,
            str(body.get("status")),
            reason=body.get("reason"),
            operator_id=_operator_id(request),
        )
        if err:
            return json_error(code="validation_error", message=err, status_code=422)

        new_status = normalize_agent_status(updated.get("status"))
        log_agent_status_change(
            request.app.state.root,
            account=updated,
            previous_status=previous_status,
            new_status=new_status,
            operator_id=_operator_id(request),
            reason=body.get("reason"),
        )
        return {
            "updated": True,
            "agent_id": agent_id,
            "previous_status": previous_status,
            "status": new_status,
            "agent": operator_agent_entry(updated),
            "directory_note": (
                "Agent removed from public directory"
                if new_status in {"suspended", "pending_review"}
                else None
            ),
        }

    @router.get("/agents/{agent_id}/agent-card.json")
    async def agents_signed_agent_card(agent_id: str, request: Request) -> dict[str, Any]:
        """Signed per-agent Agent Card (A2A v1.0 cryptographic identity)."""
        if not is_valid_agent_id(agent_id):
            return json_error(code="not_found", message="Agent account not found", status_code=404)
        account = get_agent_account(request.app.state.root, agent_id)
        if not account or not is_active_agent_status(account.get("status")):
            return json_error(code="not_found", message="Agent account not found", status_code=404)
        from arclya2a.agents.agent_identity import build_per_agent_card

        base_url = resolve_request_public_url(request)
        return build_per_agent_card(account, base_url=base_url, root=request.app.state.root)

    @router.get("/agents/{agent_id}")
    async def agents_public_profile(agent_id: str, request: Request) -> dict[str, Any]:
        """Public profile view for a registered external agent."""
        if not is_valid_agent_id(agent_id):
            return json_error(
                code="not_found",
                message="Agent account not found",
                details={"hint": "agent_id must match format ag_<12 hex chars>"},
                status_code=404,
            )

        account = get_agent_account(request.app.state.root, agent_id)
        if not account:
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        if not is_active_agent_status(account.get("status")):
            return json_error(
                code="not_found",
                message="Agent account not found",
                status_code=404,
            )
        base_url = resolve_request_public_url(request)
        return detailed_public_profile(
            account,
            profile_url=f"{base_url}/agents/{agent_id}",
            root=request.app.state.root,
        )