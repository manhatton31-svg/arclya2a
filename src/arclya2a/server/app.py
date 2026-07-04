"""HTTP entry: A2A agent-card discovery + orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from arclya2a.config.product_profile import (
    build_destination_cta,
    format_validation_errors,
    validate_product_profile,
    validation_summary,
)
from arclya2a.server.agent_card import build_agent_card as build_agent_card_doc
from arclya2a.server.landing import LANDING_HTML

from arclya2a.handoff.validators import HandoffValidationError
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.server.auth import (
    extract_api_key,
    load_api_key,
    load_rate_limit_per_minute,
    path_requires_auth,
    verify_api_key,
)
from arclya2a.server.operator_auth import load_operator_key, verify_operator_key
from arclya2a.server.errors import json_error, unhandled_exception_handler
from arclya2a.observability.dashboard import build_ops_dashboard
from arclya2a.observability.ops_events import record_ops_event
from arclya2a.observability.ops_status import build_ops_status
from arclya2a.observability.structured_log import configure_logging, log_event
from arclya2a.server.events import (
    _request_snapshot,
    build_handoff_summary,
    log_deal_close,
    log_handoff_chain_complete,
    log_handoff_chain_failed,
    log_handoff_chain_start,
    log_handoff_request_received,
    log_profile_saved,
)
from arclya2a.partners.graduation import (
    GraduationError,
    assess_graduation_readiness,
    graduate_partner,
    resolve_partner_identifier,
)
from arclya2a.partners.onboarding_guide import build_onboarding_guide
from arclya2a.partners.progress import (
    SUCCESS_DEFINITION,
    build_partner_funnel_metrics,
    build_partner_progress,
    recommend_next_step,
)
from arclya2a.partners.sandbox import (
    apply_sandbox_markers,
    check_registration_allowed,
    is_sandbox_path_blocked,
    log_registration_attempt,
    log_sandbox_audit,
    record_sandbox_security_event,
    register_sandbox_key,
    sandbox_rate_limit,
    set_sandbox_active,
    validate_agent_card_url,
    validate_agent_name,
)
from arclya2a.partners.test_registry import (
    list_test_partners,
    record_partner_activity,
    register_test_partner,
)
from arclya2a.server.rate_limit import RateLimiter
from arclya2a.billing.tracker import billing_summary, list_closed_deals
from arclya2a.tools.observability import execution_summary, list_tool_executions
from arclya2a.tools.registry import ToolRegistry
from arclya2a.learning.demo_analyzer import emit_demo_learning_signal, load_latest_demo_signal
from arclya2a.learning.execution_analyzer import emit_execution_learning_signal
from arclya2a.learning.patch_generator import apply_patch_by_id, list_patches
from arclya2a.learning.learning_scheduler import (
    BackgroundLearningScheduler,
    run_learning_cycle,
    scheduler_enabled,
    should_run_learning,
)
from arclya2a.learning.patch_outcomes import build_dashboard, list_learning_runs
from arclya2a.agents.audit import log_agent_auth_failure
from arclya2a.agents.security import (
    build_agent_auth_error,
    is_agent_account_path,
    rate_limit_for_bucket,
    resolve_agent_rate_limit_bucket,
)
from arclya2a.server.agent_account_routes import register_agent_account_routes
from arclya2a.server.hangout_routes import register_hangout_routes
from arclya2a.server.referral_routes import register_referral_routes
from arclya2a.agents.agent_identity import attach_platform_signature, verify_signature
from arclya2a.server.crypto_checkout import register_crypto_checkout_routes
from arclya2a.server.crypto_test_payer_routes import register_crypto_test_payer_routes
from arclya2a.server.schemas import HandoffChainRequest, HandoffChainResponse, HandoffChainSummary
from arclya2a.xai.client import XAIClient
from arclya2a.xai.prompt_helpers import assemble_agent_prompt, assembly_to_response

from arclya2a.settings import project_root

ROOT = project_root()

logger = logging.getLogger("arclya2a.server")


def load_core_config() -> dict[str, Any]:
    with open(ROOT / "config" / "core.json", encoding="utf-8") as f:
        return json.load(f)


def resolve_public_base_url() -> str:
    """Public URL for Agent Card discovery (custom domain, Render, or local config)."""
    from arclya2a.settings import resolve_public_base_url as _resolve

    return _resolve(fallback=load_core_config()["server"]["base_url"])


def resolve_server_bind() -> tuple[str, int]:
    """Host/port for uvicorn; Render sets PORT and requires 0.0.0.0."""
    from arclya2a.settings import get_settings

    core = load_core_config()
    settings = get_settings()
    if settings.port is not None:
        return "0.0.0.0", settings.port
    return core["server"]["host"], int(core["server"]["port"])


def build_agent_card() -> dict[str, Any]:
    """Build A2A-compliant AgentCard."""
    core = load_core_config()
    return build_agent_card_doc(
        root=ROOT,
        base_url=resolve_public_base_url(),
        version=core["version"],
        platform_name=core["platform_name"],
    )


def build_initial_ssot(req: HandoffChainRequest) -> dict[str, Any]:
    """Construct SSOT from request fields or honor a full external override."""
    if req.initial_ssot:
        ssot = dict(req.initial_ssot)
        ssot.setdefault("deal_id", req.deal_id)
        if req.task_context and not ssot.get("summary"):
            ssot["summary"] = req.task_context[:256]
        return ssot

    metadata: dict[str, Any] = dict(req.metadata or {})
    if req.onboarding_complete or req.product_profile_complete:
        metadata["onboarding_complete"] = True
        metadata["product_profile_complete"] = True
    if req.product_profile:
        metadata["product_profile"] = req.product_profile
    if req.lead_warmth:
        metadata["lead_warmth"] = req.lead_warmth
    if req.acquisition_stage:
        metadata["acquisition_stage"] = req.acquisition_stage

    stage = "new"
    if req.lead_warmth == "warm":
        stage = "warm_lead"
    elif req.acquisition_stage in ("prospect", "invited", "recruiting", "qualified"):
        stage = "recruiting"

    return {
        "deal_id": req.deal_id,
        "summary": f"New deal with {req.customer_company}",
        "customer": {"company": req.customer_company},
        "deal_value_usd": req.revenue_usd,
        "stage": stage,
        "metadata": metadata,
    }


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        def _sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            if isinstance(obj, BaseException):
                return str(obj)
            return obj

        return json_error(
            code="validation_error",
            message="Request validation failed",
            details=_sanitize(exc.errors()),
            status_code=422,
        )

    @app.exception_handler(HandoffValidationError)
    async def handoff_validation_handler(request: Request, exc: HandoffValidationError) -> JSONResponse:
        logger.warning("handoff_validation_error path=%s detail=%s", request.url.path, exc)
        return json_error(
            code="handoff_validation_error",
            message=str(exc),
            status_code=400,
        )

    @app.exception_handler(EnvironmentError)
    async def environment_error_handler(request: Request, exc: EnvironmentError) -> JSONResponse:
        return json_error(
            code="configuration_error",
            message=str(exc),
            status_code=503,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        code = "http_error"
        if exc.status_code == 401:
            code = "authentication_error"
        elif exc.status_code == 429:
            code = "rate_limit_exceeded"
        elif exc.status_code == 404:
            code = "not_found"
        return json_error(
            code=code,
            message=detail,
            status_code=exc.status_code,
        )

    app.add_exception_handler(Exception, unhandled_exception_handler)


def _sandbox_handoff_hints(summary: dict[str, Any]) -> list[str]:
    """Contextual next-action hints after a sandbox handoff."""
    hints: list[str] = []
    if summary.get("emergency_stop"):
        hints.append(
            "EMERGENCY_STOP triggered — review task_context for injection patterns; "
            "this blocks graduation (no_emergency_stops milestone)."
        )
        return hints
    if not summary.get("profile_saved") and not summary.get("onboarding_complete"):
        hints.append(
            "Onboarding incomplete — include a complete product_profile in SSOT metadata "
            "or run POST /onboarding/validate first."
        )
    elif summary.get("profile_saved") and not summary.get("lead_routing_confirmed"):
        hints.append(
            "Profile saved. Next: recruitment handoff (acquisition_stage: prospect), "
            "then warm close (lead_warmth: warm) for lead_routing_confirmed."
        )
    if summary.get("lead_routing_confirmed"):
        hints.append(
            f"Sandbox close achieved — CTA: {summary.get('cta_url') or 'see summary.cta_url'}. "
            "Check GET /partners/me/progress for remaining graduation milestones."
        )
    if not hints:
        hints.append("Check GET /partners/me/progress for milestone status and next step.")
    return hints


def create_app(
    root: Path | None = None,
    xai_client: XAIClient | None = None,
    *,
    api_key: str | None = None,
    rate_limit_per_minute: int | None = None,
    agent_register_rate_limit_per_minute: int | None = None,
    agent_directory_rate_limit_per_minute: int | None = None,
    agent_recommended_rate_limit_per_minute: int | None = None,
    agent_rotate_key_rate_limit_per_minute: int | None = None,
) -> FastAPI:
    root = root or ROOT
    configured_key = api_key if api_key is not None else load_api_key()
    limit = rate_limit_per_minute if rate_limit_per_minute is not None else load_rate_limit_per_minute()
    agent_reg_limit = (
        agent_register_rate_limit_per_minute
        if agent_register_rate_limit_per_minute is not None
        else rate_limit_for_bucket("register")
    )
    agent_dir_limit = (
        agent_directory_rate_limit_per_minute
        if agent_directory_rate_limit_per_minute is not None
        else rate_limit_for_bucket("directory")
    )
    agent_rec_limit = (
        agent_recommended_rate_limit_per_minute
        if agent_recommended_rate_limit_per_minute is not None
        else rate_limit_for_bucket("recommended")
    )
    agent_rotate_limit = (
        agent_rotate_key_rate_limit_per_minute
        if agent_rotate_key_rate_limit_per_minute is not None
        else rate_limit_for_bucket("rotate_key")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        log_event(logger, "server_startup", service="arclya2a", root=str(root))
        record_ops_event(root, "server_startup", category="server", data={"root": str(root)})
        scheduler = BackgroundLearningScheduler(root)
        app.state.learning_scheduler = scheduler
        await scheduler.start()
        yield
        log_event(logger, "server_shutdown", service="arclya2a")
        record_ops_event(root, "server_shutdown", category="server", data={})
        await scheduler.stop()

    app = FastAPI(
        title="Arclya A2A",
        version="0.1.0",
        description="Constitutional agent-to-agent orchestration API",
        lifespan=lifespan,
    )
    app.state.root = root
    app.state.xai_client = xai_client
    app.state.api_key = configured_key
    app.state.rate_limiter = RateLimiter(max_per_minute=limit)
    app.state.sandbox_rate_limiter = RateLimiter(max_per_minute=sandbox_rate_limit())
    app.state.agent_rate_limiters = {
        "register": RateLimiter(max_per_minute=agent_reg_limit),
        "directory": RateLimiter(max_per_minute=agent_dir_limit),
        "recommended": RateLimiter(max_per_minute=agent_rec_limit),
        "rotate_key": RateLimiter(max_per_minute=agent_rotate_limit),
    }
    _register_exception_handlers(app)

    configure_logging()

    def _client_ip(req: Request) -> str:
        return req.client.host if req.client else "anonymous"

    def _rate_limit_exceeded_response(
        *,
        retry_after: int,
        rate_cap: int,
        bucket: str | None = None,
    ) -> JSONResponse:
        details: dict[str, Any] = {
            "retry_after_seconds": retry_after,
            "limit_per_minute": rate_cap,
        }
        if bucket:
            details["bucket"] = bucket
        response = json_error(
            code="rate_limit_exceeded",
            message="Rate limit exceeded. Retry later.",
            details=details,
            status_code=429,
        )
        response.headers["Retry-After"] = str(retry_after)
        response.headers["X-RateLimit-Remaining"] = "0"
        response.headers["X-RateLimit-Limit"] = str(rate_cap)
        return response

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        sandbox_mode = False
        if path_requires_auth(request.url.path):
            caller = verify_api_key(request, app.state.api_key, root=app.state.root)
            if caller is None:
                if is_agent_account_path(request.url.path):
                    auth_err = build_agent_auth_error(
                        request.url.path,
                        provided_key=extract_api_key(request),
                        root=app.state.root,
                    )
                    log_agent_auth_failure(app.state.root, request, auth_err)
                    return json_error(
                        code=auth_err["code"],
                        message=auth_err["message"],
                        details=auth_err.get("details"),
                        status_code=auth_err["status_code"],
                    )
                return json_error(
                    code="authentication_error",
                    message="Invalid or missing API key",
                    status_code=401,
                )
            request.state.caller = caller
            sandbox_mode = caller.get("mode") == "sandbox"

            if sandbox_mode and is_sandbox_path_blocked(request.url.path):
                partner_id = caller.get("partner_id")
                record_sandbox_security_event(
                    app.state.root,
                    "blocked_path",
                    partner_id=partner_id,
                    client_ip=request.client.host if request.client else None,
                    path=request.url.path,
                    details={"method": request.method},
                )
                log_sandbox_audit(
                    app.state.root,
                    action="sandbox_path_denied",
                    reasoning=f"Sandbox key blocked from {request.url.path}",
                    partner_id=partner_id,
                    metadata={"path": request.url.path, "method": request.method},
                )
                return json_error(
                    code="sandbox_forbidden",
                    message="This endpoint is not available in sandbox mode",
                    status_code=403,
                )

            client_id = caller.get("client_id", "anonymous")
            agent_bucket = resolve_agent_rate_limit_bucket(
                request.url.path,
                request.method,
            )
            if agent_bucket:
                rate_limiter = app.state.agent_rate_limiters[agent_bucket]
                rate_cap = rate_limit_for_bucket(agent_bucket)
                rl_client = caller.get("agent_id") or client_id
            else:
                rate_limiter = (
                    app.state.sandbox_rate_limiter if sandbox_mode else app.state.rate_limiter
                )
                rate_cap = sandbox_rate_limit() if sandbox_mode else limit
                rl_client = client_id
            allowed, remaining, retry_after = rate_limiter.check(rl_client)
            if not allowed:
                logger.warning(
                    "rate_limit_exceeded client_id=%s path=%s retry_after=%s",
                    rl_client,
                    request.url.path,
                    retry_after,
                )
                if sandbox_mode and caller.get("partner_id"):
                    record_sandbox_security_event(
                        app.state.root,
                        "rate_limit_exceeded",
                        partner_id=caller.get("partner_id"),
                        client_ip=request.client.host if request.client else None,
                        path=request.url.path,
                    )
                return _rate_limit_exceeded_response(
                    retry_after=retry_after,
                    rate_cap=rate_cap,
                    bucket=agent_bucket,
                )

            set_sandbox_active(sandbox_mode)
            try:
                response = await call_next(request)
            finally:
                set_sandbox_active(False)

            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Limit"] = str(rate_cap)
            if sandbox_mode:
                response.headers["X-Arclya-Mode"] = "sandbox"
            return response

        agent_bucket = resolve_agent_rate_limit_bucket(request.url.path, request.method)
        agent_rate_remaining: int | None = None
        if agent_bucket:
            rate_limiter = app.state.agent_rate_limiters[agent_bucket]
            rate_cap = rate_limit_for_bucket(agent_bucket)
            rl_client = f"ip:{_client_ip(request)}:{agent_bucket}"
            allowed, agent_rate_remaining, retry_after = rate_limiter.check(rl_client)
            if not allowed:
                logger.warning(
                    "agent_rate_limit_exceeded client_id=%s path=%s bucket=%s",
                    rl_client,
                    request.url.path,
                    agent_bucket,
                )
                return _rate_limit_exceeded_response(
                    retry_after=retry_after,
                    rate_cap=rate_cap,
                    bucket=agent_bucket,
                )

        caller = verify_api_key(request, app.state.api_key, root=app.state.root)
        if caller and caller.get("mode") == "sandbox":
            request.state.caller = caller
            set_sandbox_active(True)
            try:
                response = await call_next(request)
            finally:
                set_sandbox_active(False)
            response.headers["X-Arclya-Mode"] = "sandbox"
            return response

        response = await call_next(request)
        if agent_bucket and agent_rate_remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(rate_limit_for_bucket(agent_bucket))
            response.headers["X-RateLimit-Remaining"] = str(max(0, agent_rate_remaining))
        return response

    def _orchestrator() -> Orchestrator:
        client = app.state.xai_client
        if client is None:
            client = XAIClient(app.state.root)
        return Orchestrator(app.state.root, xai_client=client)

    def _caller_context(request: Request) -> dict[str, Any]:
        return getattr(request.state, "caller", {"authenticated": False, "client_id": "anonymous"})

    @app.get("/", response_class=HTMLResponse)
    async def landing_page() -> str:
        """Public landing page for partner agent discovery."""
        return LANDING_HTML

    @app.get("/.well-known/agent-card.json")
    async def agent_card(request: Request) -> JSONResponse:
        card = build_agent_card()
        signed = attach_platform_signature(card, root=request.app.state.root)
        return JSONResponse(content=signed)

    @app.post("/.well-known/agent-card/verify")
    async def verify_platform_agent_card(request: Request) -> dict[str, Any]:
        """Verify HMAC signature on a signed platform Agent Card (A2A v1.0)."""
        try:
            body = await request.json()
        except Exception:
            return {"valid": False, "error": "JSON body required"}
        if not isinstance(body, dict):
            return {"valid": False, "error": "Body must be a JSON object"}
        signature = body.pop("signature", None)
        if not signature:
            return {"valid": False, "error": "signature field required"}
        valid = verify_signature(body, signature, root=request.app.state.root)
        return {"valid": valid, "a2a_protocol_version": "1.0"}

    @app.post("/partners/sandbox/register")
    async def sandbox_register(request: Request) -> dict[str, Any]:
        """Self-service sandbox API key for test partners (no auth required)."""
        client_ip = request.client.host if request.client else None
        try:
            body = await request.json()
        except Exception:
            log_registration_attempt(
                app.state.root,
                agent_name="",
                client_ip=client_ip,
                success=False,
                reason="invalid_json",
            )
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON",
                status_code=422,
            )
        agent_name = (body.get("agent_name") or "").strip()
        agent_card_url = (body.get("agent_card_url") or "").strip() or None

        name_ok, name_err = validate_agent_name(agent_name)
        if not name_ok:
            log_registration_attempt(
                app.state.root,
                agent_name=agent_name,
                client_ip=client_ip,
                success=False,
                reason=name_err,
            )
            return json_error(code="validation_error", message=name_err, status_code=422)

        url_ok, url_err = validate_agent_card_url(agent_card_url)
        if not url_ok:
            log_registration_attempt(
                app.state.root,
                agent_name=agent_name,
                client_ip=client_ip,
                success=False,
                reason=url_err,
            )
            return json_error(code="validation_error", message=url_err, status_code=422)

        allowed, deny_reason = check_registration_allowed(
            app.state.root,
            agent_name=agent_name,
            client_ip=client_ip,
        )
        if not allowed:
            log_registration_attempt(
                app.state.root,
                agent_name=agent_name,
                client_ip=client_ip,
                success=False,
                reason=deny_reason,
            )
            record_sandbox_security_event(
                app.state.root,
                "registration_denied",
                client_ip=client_ip,
                details={"agent_name": agent_name, "reason": deny_reason},
            )
            return json_error(
                code="registration_denied",
                message=deny_reason or "Sandbox registration not allowed",
                status_code=429,
            )

        partner = register_test_partner(
            app.state.root,
            agent_name=agent_name,
            agent_card_url=agent_card_url,
            target_customer=body.get("target_customer"),
            contact=body.get("contact"),
        )
        sandbox_key = register_sandbox_key(
            app.state.root,
            partner_id=partner["partner_id"],
            agent_name=agent_name,
            metadata={
                "agent_card_url": agent_card_url,
                "target_customer": body.get("target_customer"),
            },
        )
        log_registration_attempt(
            app.state.root,
            agent_name=agent_name,
            client_ip=client_ip,
            success=True,
            partner_id=partner["partner_id"],
        )
        log_sandbox_audit(
            app.state.root,
            action="sandbox_registered",
            reasoning=f"Sandbox key issued for {agent_name}",
            partner_id=partner["partner_id"],
            metadata={"client_ip": client_ip, "agent_card_url": agent_card_url},
        )
        record_sandbox_security_event(
            app.state.root,
            "registered",
            partner_id=partner["partner_id"],
            client_ip=client_ip,
            details={"agent_name": agent_name},
        )
        guide = build_onboarding_guide()
        return {
            "partner_id": partner["partner_id"],
            "sandbox_key": sandbox_key,
            "mode": "sandbox",
            "rate_limit_per_minute": sandbox_rate_limit(),
            "tools_mode": "dry_run",
            "billing": "disabled",
            "test_marker": "[SANDBOX — dry-run tools, no production billing]",
            "next_steps": guide["steps"][:3],
            "guide_url": "/partners/onboarding/guide",
            "message": "Use X-Arclya-Key header with sandbox_key on protected endpoints.",
        }

    @app.get("/partners/onboarding/guide")
    async def partners_onboarding_guide() -> dict[str, Any]:
        """Guided JSON flow for test partner onboarding."""
        return build_onboarding_guide()

    @app.get("/partners/test")
    async def partners_test_list() -> dict[str, Any]:
        """List registered test partners (no API keys exposed)."""
        partners = list_test_partners(app.state.root)
        guide = build_onboarding_guide()
        funnel = build_partner_funnel_metrics(app.state.root)
        return {
            "count": len(partners),
            "partners": partners,
            "funnel": {
                "stages": funnel.get("funnel_stages", []),
                "conversion_rates": funnel.get("conversion_rates", {}),
                "active_7d": funnel.get("active_7d", 0),
            },
            "graduation_criteria": guide["graduation_criteria"],
            "security_graduation_criteria": guide["security_graduation_criteria"],
            "success_definition": SUCCESS_DEFINITION,
        }

    @app.get("/partners/me/progress")
    async def partners_me_progress(request: Request) -> dict[str, Any]:
        """Sandbox partner journey progress (requires X-Arclya-Key sandbox key)."""
        caller = _caller_context(request)
        if caller.get("mode") != "sandbox" or not caller.get("partner_id"):
            return json_error(
                code="authentication_error",
                message="Sandbox API key required. Register at POST /partners/sandbox/register.",
                status_code=401,
            )
        progress = build_partner_progress(app.state.root, caller["partner_id"])
        if not progress:
            return json_error(
                code="not_found",
                message="Partner record not found",
                status_code=404,
            )
        return apply_sandbox_markers(progress, sandbox=True)

    @app.post("/partners/graduate")
    async def partners_graduate(request: Request) -> dict[str, Any]:
        """Operator-only: graduate a sandbox partner to production."""
        if not verify_operator_key(request, configured_key=load_operator_key()):
            return json_error(
                code="authentication_error",
                message="Operator key required (X-Arclya-Operator-Key or ARCLYA_OPERATOR_KEY)",
                status_code=401,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        partner_id = resolve_partner_identifier(
            app.state.root,
            partner_id=body.get("partner_id"),
            sandbox_key=body.get("sandbox_key"),
        )
        if not partner_id:
            return json_error(
                code="validation_error",
                message="partner_id or sandbox_key is required",
                status_code=422,
            )
        performed_by = (
            body.get("performed_by")
            or request.headers.get("X-Arclya-Operator-Id", "").strip()
            or "operator_api"
        )
        try:
            result = graduate_partner(
                app.state.root,
                partner_id=partner_id,
                graduated_by=performed_by,
            )
        except GraduationError as exc:
            assessment = assess_graduation_readiness(app.state.root, partner_id)
            return json_error(
                code=exc.code,
                message=str(exc),
                details={
                    "partner_id": partner_id,
                    "blocking_reasons": exc.reasons,
                    "graduation_ready": assessment.get("graduation_ready", False),
                    "milestones": assessment.get("milestones", {}),
                },
                status_code=409,
            )
        return {
            "success": True,
            "partner_id": result["partner_id"],
            "agent_name": result["agent_name"],
            "production_key": result["production_key"],
            "production_key_prefix": result["production_key_prefix"],
            "sandbox_keys_revoked": result["sandbox_keys_revoked"],
            "graduated_at": result["graduated_at"],
            "graduated_by": result["graduated_by"],
            "audit_id": result["audit_id"],
            "notification": result.get("notification"),
            "message": "Sandbox keys revoked. Store the production key securely — it is shown once.",
        }

    @app.post("/onboarding/validate")
    async def onboarding_validate(request: Request) -> dict[str, Any]:
        """Validate a product profile before or during onboarding (no auth required)."""
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON with product_profile object",
                status_code=422,
            )
        profile = body.get("product_profile") or body
        if not isinstance(profile, dict):
            return json_error(
                code="validation_error",
                message="product_profile must be an object",
                status_code=422,
            )
        ok, missing = validate_product_profile(profile)
        errors = format_validation_errors(missing)
        caller = _caller_context(request)
        partner_id = caller.get("partner_id")
        if partner_id:
            if ok:
                record_partner_activity(
                    app.state.root,
                    partner_id,
                    event="profile_validated",
                )
                log_sandbox_audit(
                    app.state.root,
                    action="sandbox_profile_validated",
                    reasoning="Product profile passed validation",
                    partner_id=partner_id,
                )
            elif caller.get("mode") == "sandbox":
                record_sandbox_security_event(
                    app.state.root,
                    "validation_failed",
                    partner_id=partner_id,
                    path="/onboarding/validate",
                    details={"missing_fields": missing},
                )
        result: dict[str, Any] = {
            "valid": ok,
            "onboarding_complete": ok,
            "missing_fields": missing,
            "validation_errors": errors,
            "fields_remaining": len(missing),
            "summary": validation_summary(missing),
            "destination_cta_preview": build_destination_cta(profile) if ok else None,
            "success_definition": SUCCESS_DEFINITION,
        }
        if not ok and errors:
            result["fix_hint"] = (
                f"Fix {len(missing)} field(s) below, then re-submit until valid: true."
            )
            result["required_fields"] = [
                "agent_name", "product_name", "product_description", "target_customer",
                "typical_deal_size", "common_objections (≥3)", "preferred_pricing_model",
                "accepts_crypto", "destination_link",
            ]
        if partner_id:
            progress = build_partner_progress(app.state.root, partner_id)
            if progress:
                result["partner_progress"] = {
                    "partner_id": progress["partner_id"],
                    "milestone_progress": progress["milestone_progress"],
                    "milestones": progress["milestones"],
                    "graduation_ready": progress["graduation_ready"],
                    "next_step": progress["next_step"],
                    "progress_url": progress["progress_url"],
                }
                if ok:
                    result["milestone_achieved"] = "profile_validated"
                    result["next_step"] = recommend_next_step(
                        progress["milestones"],
                        graduation_ready=progress["graduation_ready"],
                    )
        return apply_sandbox_markers(result, sandbox=caller.get("mode") == "sandbox")

    @app.get("/health")
    async def health(detailed: bool = False) -> dict[str, Any]:
        from arclya2a.agents.platform_status import build_agent_platform_summary

        ops = build_ops_status(app.state.root)
        components = ops.get("component_health", {})
        email_h = components.get("email") or {}
        base = {
            "status": ops.get("status", "healthy"),
            "service": "arclya2a",
            "auth_enabled": bool(app.state.api_key),
            "rate_limit_per_minute": limit,
            "checked_at": ops.get("checked_at"),
            "learning_last_run": ops.get("learning", {}).get("last_run_at"),
            "tool_failure_rate": ops.get("tools", {}).get("failure_rate"),
            "pending_high_risk_patches": ops.get("pending_high_risk_count", 0),
            "launch_ready": ops.get("launch_readiness", {}).get("ready", False),
            "components": {
                "email": email_h.get("status"),
                "crypto": (components.get("crypto") or {}).get("status"),
            },
            "email_delivery": {
                "mode": email_h.get("delivery_mode_effective"),
                "provider": email_h.get("smtp_provider"),
                "launch_ready": email_h.get("launch_ready", False),
                "public_url": email_h.get("public_url"),
                "public_url_source": email_h.get("public_url_source"),
            },
            "launch_blockers": (ops.get("launch_readiness") or {}).get("blocking_issues", []),
            "launch_next_steps": components.get("next_steps", []),
            "external_agents": build_agent_platform_summary(app.state.root),
        }
        if detailed:
            base["operations"] = ops
        return base

    @app.get("/status")
    async def status() -> dict[str, Any]:
        """Full operational status: learning, tools, handoffs, pending patches."""
        from arclya2a.agents.platform_status import build_public_platform_summary

        ops = build_ops_status(app.state.root)
        return {
            "service": "arclya2a",
            "auth_enabled": bool(app.state.api_key),
            "platform_summary": build_public_platform_summary(
                app.state.root,
                ops_status=ops.get("status", "healthy"),
                auth_enabled=bool(app.state.api_key),
                checked_at=ops.get("checked_at"),
                payments=ops.get("payments"),
                component_health=ops.get("component_health"),
            ),
            "status_page": "/platform/status",
            **ops,
        }

    @app.get("/platform/status", response_class=HTMLResponse)
    async def platform_status_page() -> str:
        """Visitor-friendly HTML status page before agent sign-up."""
        from arclya2a.agents.platform_status import build_public_platform_summary
        from arclya2a.server.status_page import build_status_page_html

        ops = build_ops_status(app.state.root)
        snapshot = {
            "service": "arclya2a",
            "status": ops.get("status", "healthy"),
            "checked_at": ops.get("checked_at"),
            "auth_enabled": bool(app.state.api_key),
            "platform_summary": build_public_platform_summary(
                app.state.root,
                ops_status=ops.get("status", "healthy"),
                auth_enabled=bool(app.state.api_key),
                checked_at=ops.get("checked_at"),
                payments=ops.get("payments"),
                component_health=ops.get("component_health"),
            ),
            "external_agents": ops.get("external_agents", {}),
            "component_health": ops.get("component_health", {}),
            "payments": ops.get("payments", {}),
            "launch_readiness": ops.get("launch_readiness", {}),
            "status": ops.get("status", "healthy"),
            "checked_at": ops.get("checked_at"),
        }
        return build_status_page_html(snapshot=snapshot)

    @app.get("/ops/dashboard")
    async def ops_dashboard() -> dict[str, Any]:
        """Operational dashboard: learning activity, tool health, pending patches."""
        return build_ops_dashboard(app.state.root)

    @app.get("/security/events")
    async def security_events(
        event_type: str | None = None,
        partner_id: str | None = None,
        severity: str | None = None,
        hours: float | None = 24,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query recent security events with optional filters."""
        from arclya2a.observability.security_events import build_security_metrics, list_security_events

        safe_limit = max(1, min(limit, 200))
        return {
            "events": list_security_events(
                app.state.root,
                event_type=event_type,
                partner_id=partner_id,
                severity=severity,
                hours=hours,
                limit=safe_limit,
            ),
            "metrics": build_security_metrics(app.state.root),
            "filters": {
                "event_type": event_type,
                "partner_id": partner_id,
                "severity": severity,
                "hours": hours,
                "limit": safe_limit,
            },
        }

    @app.post("/orchestrate/handoff-chain", response_model=HandoffChainResponse)
    async def handoff_chain(req: HandoffChainRequest, request: Request) -> dict[str, Any]:
        caller = _caller_context(request)
        client_ip = request.client.host if request.client else None
        snapshot = _request_snapshot(req)

        log_handoff_request_received(
            app.state.root,
            caller=caller,
            request_snapshot=snapshot,
            client_ip=client_ip,
        )

        orchestrator = _orchestrator()
        initial_ssot = build_initial_ssot(req)

        log_handoff_chain_start(
            deal_id=req.deal_id,
            auto_route=req.auto_route,
            entry_agent=req.entry_agent,
            task_context=req.task_context,
            caller=caller,
        )

        try:
            result = await asyncio.to_thread(
                orchestrator.run_chain,
                initial_ssot=initial_ssot,
                task_context=req.task_context,
                revenue_usd=req.revenue_usd,
                estimated_cost_usd=req.estimated_cost_usd,
                auto_route=req.auto_route,
                entry_agent=req.entry_agent,
                partner_id=caller.get("partner_id"),
                sandbox_mode=caller.get("mode") == "sandbox",
            )
        except HandoffValidationError:
            raise
        except EnvironmentError:
            raise
        except Exception as exc:
            log_handoff_chain_failed(
                app.state.root,
                deal_id=req.deal_id,
                client_id=caller.get("client_id"),
                error=str(exc),
            )
            return json_error(
                code="orchestration_failed",
                message="Orchestration failed",
                details=str(exc),
                status_code=500,
            )

        summary_data = build_handoff_summary(result.handoff_chain, result.final_ssot)
        summary = HandoffChainSummary(
            entry_agent=result.entry_agent,
            emergency_stop=result.emergency_stop,
            **summary_data,
        )

        log_handoff_chain_complete(
            app.state.root,
            deal_id=req.deal_id,
            entry_agent=result.entry_agent,
            agents_executed=summary.agents_executed,
            emergency_stop=result.emergency_stop,
            audit_ids=result.audit_ids,
            caller=caller,
            outcome_summary=summary.model_dump(),
        )

        meta = result.final_ssot.get("metadata", {})
        if meta.get("product_profile_complete") and meta.get("product_profile"):
            profile = meta["product_profile"]
            log_profile_saved(
                app.state.root,
                deal_id=req.deal_id,
                agent_name=profile.get("agent_name", "unknown"),
                destination_cta=meta.get("destination_cta"),
            )

        if summary.deal_closed and summary.lead_routing_confirmed:
            log_deal_close(
                app.state.root,
                deal_id=req.deal_id,
                close_type=summary.close_type,
                cta_url=summary.cta_url,
            )

        response = HandoffChainResponse(
            entry_agent=result.entry_agent,
            summary=summary,
            handoff_chain=result.handoff_chain,
            final_ssot=result.final_ssot,
            audit_ids=result.audit_ids,
            cost_records=result.cost_records,
            emergency_stop=result.emergency_stop,
            uses_xai_inference=result.uses_xai_inference,
        )
        response_dict = response.model_dump()

        if caller.get("mode") == "sandbox":
            response_dict = apply_sandbox_markers(response_dict, sandbox=True)
            partner_id = caller.get("partner_id")
            if partner_id:
                record_partner_activity(
                    app.state.root,
                    partner_id,
                    event="handoff_complete",
                    details={"summary": summary.model_dump(), "deal_id": req.deal_id},
                )
                if summary.entry_agent == "recruiter" and not summary.emergency_stop:
                    record_partner_activity(
                        app.state.root,
                        partner_id,
                        event="recruitment_ready",
                    )
                progress = build_partner_progress(app.state.root, partner_id)
                if progress:
                    response_dict["partner_progress"] = {
                        "partner_id": progress["partner_id"],
                        "milestone_progress": progress["milestone_progress"],
                        "milestones": progress["milestones"],
                        "graduation_ready": progress["graduation_ready"],
                        "next_step": progress["next_step"],
                        "progress_url": progress["progress_url"],
                    }
                    response_dict["journey_hints"] = _sandbox_handoff_hints(summary.model_dump())
                log_sandbox_audit(
                    app.state.root,
                    action="sandbox_handoff_complete",
                    reasoning=f"Sandbox handoff for deal {req.deal_id}",
                    partner_id=partner_id,
                    metadata={
                        "deal_id": req.deal_id,
                        "emergency_stop": summary.emergency_stop,
                        "agents_executed": summary.agents_executed,
                    },
                )
                if summary.emergency_stop:
                    record_sandbox_security_event(
                        app.state.root,
                        "emergency_stop",
                        partner_id=partner_id,
                        path="/orchestrate/handoff-chain",
                        details={"deal_id": req.deal_id},
                    )
                for hop in result.handoff_chain:
                    payload = hop.get("payload") or {}
                    recruitment_draft = payload.get("recruitment_draft") or {}
                    if payload.get("ready_to_send") or recruitment_draft.get("ready_to_send"):
                        record_partner_activity(
                            app.state.root,
                            partner_id,
                            event="recruitment_ready",
                        )
                        break

        return response_dict

    @app.get("/orchestrate/route")
    async def route_preview(
        request: Request,
        onboarding_complete: bool = False,
        lead_warmth: str = "cold",
        acquisition_stage: str | None = None,
    ) -> dict[str, str]:
        if lead_warmth not in ("cold", "warm"):
            raise HTTPException(status_code=422, detail="lead_warmth must be 'cold' or 'warm'")
        orchestrator = _orchestrator()
        metadata: dict[str, Any] = {
            "onboarding_complete": onboarding_complete,
            "product_profile_complete": onboarding_complete,
            "lead_warmth": lead_warmth,
        }
        if acquisition_stage:
            metadata["acquisition_stage"] = acquisition_stage
        ssot = {
            "deal_id": "preview",
            "summary": "Route preview",
            "stage": "warm_lead" if lead_warmth == "warm" else "new",
            "metadata": metadata,
        }
        entry = orchestrator.route(ssot)
        return {"entry_agent": entry}

    @app.get("/tools")
    async def list_tools(agent_id: str | None = None) -> dict[str, Any]:
        registry = ToolRegistry(app.state.root)
        if agent_id:
            return {
                "agent_id": agent_id,
                "tools": registry.catalog_for_agent(agent_id),
                "available": registry.list_for_agent(agent_id, only_available=True),
            }
        return registry.summary()

    @app.get("/tools/executions")
    async def tool_executions(
        request: Request,
        limit: int = 50,
        agent_id: str | None = None,
        tool_id: str | None = None,
    ) -> dict[str, Any]:
        """Recent tool execution log for debugging and production trust."""
        rows = list_tool_executions(
            app.state.root,
            limit=min(limit, 200),
            agent_id=agent_id,
            tool_id=tool_id,
        )
        return {
            "executions": rows,
            "summary": execution_summary(app.state.root, limit=limit),
            "filters": {"agent_id": agent_id, "tool_id": tool_id, "limit": limit},
        }

    @app.get("/billing/deals")
    async def billing_deals(request: Request, limit: int = 50) -> dict[str, Any]:
        deals = list_closed_deals(app.state.root, limit=limit)
        return {"deals": deals, "summary": billing_summary(app.state.root)}

    @app.get("/learning/patches")
    async def learning_patches(
        request: Request,
        status: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """List versioned prompt patches for review."""
        patches = list_patches(app.state.root, status=status, agent_id=agent_id)
        return {
            "patches": patches,
            "count": len(patches),
            "filters": {"status": status, "agent_id": agent_id},
        }

    @app.get("/learning/patches/dashboard")
    async def learning_patches_dashboard(request: Request) -> dict[str, Any]:
        """Dashboard: pending patches, recent applied, learning runs, issue summary."""
        return build_dashboard(app.state.root)

    @app.get("/learning/runs")
    async def learning_runs(request: Request, limit: int = 20) -> dict[str, Any]:
        """List recent background and manual learning cycles."""
        runs = list_learning_runs(app.state.root, limit=limit)
        return {"runs": runs, "count": len(runs), "scheduler_enabled": scheduler_enabled()}

    @app.post("/learning/run")
    async def learning_run_now(request: Request) -> dict[str, Any]:
        """Trigger execution analysis, patch generation, and safe low-risk auto-apply."""
        body: dict[str, Any] = {}
        try:
            raw = await request.body()
            if raw:
                body = json.loads(raw)
        except Exception:
            body = {}
        result = run_learning_cycle(
            app.state.root,
            trigger="manual",
            demo_report=body.get("demo_report"),
            auto_apply_low_risk=body.get("auto_apply_low_risk", True),
        )
        return result

    @app.get("/learning/scheduler/status")
    async def learning_scheduler_status(request: Request) -> dict[str, Any]:
        """Scheduler configuration and whether a run is due."""
        should, reason = should_run_learning(app.state.root)
        dashboard = build_dashboard(app.state.root)
        return {
            "enabled": scheduler_enabled(),
            "should_run": should,
            "reason": reason,
            "scheduler": dashboard.get("scheduler", {}),
        }

    @app.post("/learning/patches/{patch_id}/apply")
    async def apply_learning_patch(patch_id: str, request: Request) -> dict[str, Any]:
        """Apply an approved pending patch to the live prompt."""
        try:
            result = apply_patch_by_id(app.state.root, patch_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

    @app.post("/learning/execution-analyze")
    async def execution_analyze(request: Request) -> dict[str, Any]:
        """Analyze tool/billing/demo execution data and emit learning signal."""
        body = {}
        try:
            raw = await request.body()
            if raw:
                body = json.loads(raw)
        except Exception:
            body = {}
        signal = emit_execution_learning_signal(app.state.root, body.get("demo_report"))
        return {"improvement_signal": signal}

    @app.post("/learning/campaign")
    async def campaign_learning(request: Request) -> dict[str, Any]:
        orchestrator = _orchestrator()
        emit_execution_learning_signal(app.state.root)
        demo_signal = load_latest_demo_signal(app.state.root)
        try:
            result = orchestrator.run_chain(
                chain=["meta_optimizer"],
                initial_ssot={"deal_id": "learning", "summary": "Campaign learning", "stage": "closed", "metadata": {}},
                task_context="Analyze latest campaign results and demo outcomes",
            )
        except Exception as exc:
            logger.exception("campaign_learning_failed")
            return json_error(
                code="learning_failed",
                message="Campaign learning failed",
                details=str(exc),
                status_code=500,
            )
        handoff = result.handoff_chain[0] if result.handoff_chain else {}
        return {
            "handoff": handoff,
            "prompt_patch": handoff.get("payload", {}).get("prompt_patch"),
            "improvement_signal": handoff.get("payload", {}).get("improvement_signal"),
            "demo_signal_used": demo_signal is not None,
            "uses_xai_inference": result.uses_xai_inference,
            "cost_records": result.cost_records,
        }

    @app.post("/learning/demo-outcomes")
    async def demo_outcomes_learning(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return json_error(
                code="validation_error",
                message="Request body must be valid JSON demo report",
                status_code=422,
            )
        signal = emit_demo_learning_signal(app.state.root, body)
        emit_execution_learning_signal(app.state.root, body)
        orchestrator = _orchestrator()
        try:
            result = orchestrator.run_chain(
                chain=["meta_optimizer"],
                initial_ssot={"deal_id": "demo_learning", "summary": "Demo outcome learning", "stage": "closed", "metadata": {}},
                task_context="Analyze demo outcomes and suggest prompt improvements",
            )
        except Exception as exc:
            logger.exception("demo_learning_failed")
            return json_error(
                code="learning_failed",
                message="Demo outcome learning failed",
                details=str(exc),
                status_code=500,
            )
        handoff = result.handoff_chain[0] if result.handoff_chain else {}
        return {
            "demo_signal": signal,
            "improvement_signal": handoff.get("payload", {}).get("improvement_signal"),
            "prompt_patch": handoff.get("payload", {}).get("prompt_patch"),
            "uses_xai_inference": result.uses_xai_inference,
        }

    checkout_router = APIRouter(tags=["payments"])
    register_crypto_checkout_routes(checkout_router)
    register_crypto_test_payer_routes(checkout_router)
    app.include_router(checkout_router)

    agent_router = APIRouter(tags=["agents"])
    register_hangout_routes(agent_router)
    register_referral_routes(agent_router)
    register_agent_account_routes(agent_router)
    app.include_router(agent_router)

    @app.get("/prompt/assembly/{agent_id}")
    async def prompt_assembly(agent_id: str, request: Request) -> dict[str, Any]:
        prompt_path = root / "prompts" / f"{agent_id}.md"
        alt_path = root / "prompts" / f"{agent_id}_prompt.md"
        if not prompt_path.exists() and not alt_path.exists():
            raise HTTPException(status_code=404, detail=f"No prompt for agent_id={agent_id}")
        try:
            assembly = assemble_agent_prompt(root, agent_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return assembly_to_response(assembly)

    return app


def main() -> None:
    import uvicorn

    host, port = resolve_server_bind()
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()