"""HTTP entry: A2A agent-card discovery + orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.xai.client import XAIClient
from arclya2a.xai.prompt_helpers import assemble_agent_prompt, assembly_to_response

ROOT = Path(__file__).resolve().parents[3]


def load_core_config() -> dict[str, Any]:
    with open(ROOT / "config" / "core.json", encoding="utf-8") as f:
        return json.load(f)


def build_agent_card() -> dict[str, Any]:
    """Build A2A-compliant AgentCard."""
    core = load_core_config()
    base_url = core["server"]["base_url"]
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
    }


class HandoffChainRequest(BaseModel):
    deal_id: str = "deal_demo_001"
    customer_company: str = "Acme Corp"
    task_context: str = "Draft initial outreach for enterprise SaaS prospect"
    revenue_usd: float = 49.0
    estimated_cost_usd: float = 5.0
    auto_route: bool = True
    entry_agent: str | None = None
    onboarding_complete: bool = False
    lead_warmth: str = "cold"


def create_app(root: Path | None = None, xai_client: XAIClient | None = None) -> FastAPI:
    root = root or ROOT
    app = FastAPI(title="Arclya A2A", version="0.1.0")
    app.state.root = root
    app.state.xai_client = xai_client

    def _orchestrator() -> Orchestrator:
        client = app.state.xai_client
        if client is None:
            client = XAIClient(app.state.root)
        return Orchestrator(app.state.root, xai_client=client)

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> JSONResponse:
        card = build_agent_card()
        return JSONResponse(content=card)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.post("/orchestrate/handoff-chain")
    async def handoff_chain(req: HandoffChainRequest) -> dict[str, Any]:
        orchestrator = _orchestrator()
        metadata: dict[str, Any] = {}
        if req.onboarding_complete:
            metadata["onboarding_complete"] = True
            metadata["product_profile_complete"] = True
        if req.lead_warmth:
            metadata["lead_warmth"] = req.lead_warmth

        initial_ssot = {
            "deal_id": req.deal_id,
            "summary": f"New deal with {req.customer_company}",
            "customer": {"company": req.customer_company},
            "deal_value_usd": req.revenue_usd,
            "stage": "warm_lead" if req.lead_warmth == "warm" else "new",
            "metadata": metadata,
        }
        result = orchestrator.run_chain(
            initial_ssot=initial_ssot,
            task_context=req.task_context,
            revenue_usd=req.revenue_usd,
            estimated_cost_usd=req.estimated_cost_usd,
            auto_route=req.auto_route,
            entry_agent=req.entry_agent,
        )
        return {
            "entry_agent": result.entry_agent,
            "handoff_chain": result.handoff_chain,
            "final_ssot": result.final_ssot,
            "audit_ids": result.audit_ids,
            "cost_records": result.cost_records,
            "emergency_stop": result.emergency_stop,
            "uses_xai_inference": result.uses_xai_inference,
        }

    @app.get("/orchestrate/route")
    async def route_preview(
        onboarding_complete: bool = False,
        lead_warmth: str = "cold",
    ) -> dict[str, str]:
        orchestrator = _orchestrator()
        ssot = {
            "deal_id": "preview",
            "summary": "Route preview",
            "stage": "warm_lead" if lead_warmth == "warm" else "new",
            "metadata": {
                "onboarding_complete": onboarding_complete,
                "product_profile_complete": onboarding_complete,
                "lead_warmth": lead_warmth,
            },
        }
        entry = orchestrator.route(ssot)
        return {"entry_agent": entry}

    @app.post("/learning/campaign")
    async def campaign_learning() -> dict[str, Any]:
        orchestrator = _orchestrator()
        result = orchestrator.run_chain(
            chain=["meta_optimizer"],
            initial_ssot={"deal_id": "learning", "summary": "Campaign learning", "stage": "closed", "metadata": {}},
            task_context="Analyze latest campaign results",
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
    async def prompt_assembly(agent_id: str) -> dict[str, Any]:
        if not (root / "prompts" / f"{agent_id}.md").exists():
            raise HTTPException(status_code=404, detail=f"No prompt for {agent_id}")
        assembly = assemble_agent_prompt(root, agent_id)
        return assembly_to_response(assembly)

    return app


def main() -> None:
    import uvicorn

    core = load_core_config()
    host = core["server"]["host"]
    port = core["server"]["port"]
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()