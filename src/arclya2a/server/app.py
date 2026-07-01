"""HTTP entry: A2A agent-card discovery + orchestration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from arclya2a.handoff.validators import HandoffValidationError
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.server.auth import (
    load_api_key,
    load_rate_limit_per_minute,
    path_requires_auth,
    verify_api_key,
)
from arclya2a.server.errors import json_error, unhandled_exception_handler
from arclya2a.server.events import (
    _request_snapshot,
    build_handoff_summary,
    log_deal_close,
    log_handoff_chain_complete,
    log_handoff_chain_start,
    log_handoff_request_received,
    log_profile_saved,
)
from arclya2a.server.rate_limit import RateLimiter
from arclya2a.server.schemas import HandoffChainRequest, HandoffChainResponse, HandoffChainSummary
from arclya2a.xai.client import XAIClient
from arclya2a.xai.prompt_helpers import assemble_agent_prompt, assembly_to_response

ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger("arclya2a.server")


def load_core_config() -> dict[str, Any]:
    with open(ROOT / "config" / "core.json", encoding="utf-8") as f:
        return json.load(f)


def resolve_public_base_url() -> str:
    """Public URL for Agent Card discovery (Render, explicit override, or local config)."""
    for key in ("ARCLYA_PUBLIC_URL", "RENDER_EXTERNAL_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value.rstrip("/")
    return load_core_config()["server"]["base_url"].rstrip("/")


def resolve_server_bind() -> tuple[str, int]:
    """Host/port for uvicorn; Render sets PORT and requires 0.0.0.0."""
    core = load_core_config()
    port_env = os.environ.get("PORT", "").strip()
    if port_env:
        return "0.0.0.0", int(port_env)
    return core["server"]["host"], int(core["server"]["port"])


def build_agent_card() -> dict[str, Any]:
    """Build A2A-compliant AgentCard."""
    core = load_core_config()
    base_url = resolve_public_base_url()
    with open(ROOT / "agents" / "registry.json", encoding="utf-8") as f:
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

    return {
        "name": core["platform_name"],
        "description": "Constitutional agent-to-agent platform for affordable, customizable outreach and AI closing workflows.",
        "url": base_url,
        "version": core["version"],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
        "authentication": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Arclya-Key",
            "alternate": "Authorization: Bearer <ARCLYA_API_KEY>",
        },
    }


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


def create_app(
    root: Path | None = None,
    xai_client: XAIClient | None = None,
    *,
    api_key: str | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    root = root or ROOT
    configured_key = api_key if api_key is not None else load_api_key()
    limit = rate_limit_per_minute if rate_limit_per_minute is not None else load_rate_limit_per_minute()

    app = FastAPI(
        title="Arclya A2A",
        version="0.1.0",
        description="Constitutional agent-to-agent orchestration API",
    )
    app.state.root = root
    app.state.xai_client = xai_client
    app.state.api_key = configured_key
    app.state.rate_limiter = RateLimiter(max_per_minute=limit)
    _register_exception_handlers(app)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if not path_requires_auth(request.url.path):
            return await call_next(request)

        caller = verify_api_key(request, app.state.api_key)
        if caller is None:
            return json_error(
                code="authentication_error",
                message="Invalid or missing API key",
                status_code=401,
            )
        request.state.caller = caller

        client_id = caller.get("client_id", "anonymous")
        allowed, remaining, retry_after = app.state.rate_limiter.check(client_id)
        if not allowed:
            logger.warning(
                "rate_limit_exceeded client_id=%s path=%s retry_after=%s",
                client_id,
                request.url.path,
                retry_after,
            )
            response = json_error(
                code="rate_limit_exceeded",
                message="Rate limit exceeded. Retry later.",
                details={"retry_after_seconds": retry_after, "limit_per_minute": limit},
                status_code=429,
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(limit)
        return response

    def _orchestrator() -> Orchestrator:
        client = app.state.xai_client
        if client is None:
            client = XAIClient(app.state.root)
        return Orchestrator(app.state.root, xai_client=client)

    def _caller_context(request: Request) -> dict[str, Any]:
        return getattr(request.state, "caller", {"authenticated": False, "client_id": "anonymous"})

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> JSONResponse:
        card = build_agent_card()
        return JSONResponse(content=card)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "arclya2a",
            "auth_enabled": bool(app.state.api_key),
            "rate_limit_per_minute": limit,
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
            result = orchestrator.run_chain(
                initial_ssot=initial_ssot,
                task_context=req.task_context,
                revenue_usd=req.revenue_usd,
                estimated_cost_usd=req.estimated_cost_usd,
                auto_route=req.auto_route,
                entry_agent=req.entry_agent,
            )
        except HandoffValidationError:
            raise
        except EnvironmentError:
            raise
        except Exception as exc:
            logger.exception(
                "handoff_chain_failed deal_id=%s client_id=%s",
                req.deal_id,
                caller.get("client_id"),
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
        return response.model_dump()

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

    @app.post("/learning/campaign")
    async def campaign_learning(request: Request) -> dict[str, Any]:
        orchestrator = _orchestrator()
        try:
            result = orchestrator.run_chain(
                chain=["meta_optimizer"],
                initial_ssot={"deal_id": "learning", "summary": "Campaign learning", "stage": "closed", "metadata": {}},
                task_context="Analyze latest campaign results",
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
            "uses_xai_inference": result.uses_xai_inference,
            "cost_records": result.cost_records,
        }

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