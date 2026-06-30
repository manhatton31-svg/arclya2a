"""HTTP entry: A2A agent-card discovery + orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from arclya2a.learning.campaign_loop import run_campaign_learning_loop
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.xai.client import XAIClient, assemble_prompt, select_model

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


def create_app(root: Path | None = None) -> FastAPI:
    root = root or ROOT
    app = FastAPI(title="Arclya A2A", version="0.1.0")

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> JSONResponse:
        card = build_agent_card()
        return JSONResponse(content=card)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.post("/orchestrate/handoff-chain")
    async def handoff_chain(req: HandoffChainRequest) -> dict[str, Any]:
        orchestrator = Orchestrator(root)
        initial_ssot = {
            "deal_id": req.deal_id,
            "summary": f"New deal with {req.customer_company}",
            "customer": {"company": req.customer_company},
            "deal_value_usd": req.revenue_usd,
            "stage": "new",
            "metadata": {},
        }
        result = orchestrator.run_chain(
            chain=["outreach_worker", "profit_guardrail", "final_arbiter"],
            initial_ssot=initial_ssot,
            task_context=req.task_context,
            revenue_usd=req.revenue_usd,
            estimated_cost_usd=req.estimated_cost_usd,
        )
        return {
            "handoff_chain": result.handoff_chain,
            "final_ssot": result.final_ssot,
            "audit_ids": result.audit_ids,
            "cost_records": result.cost_records,
            "emergency_stop": result.emergency_stop,
        }

    @app.post("/learning/campaign")
    async def campaign_learning() -> dict[str, Any]:
        fixtures_path = root / "data" / "campaign_results" / "fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            raise HTTPException(status_code=404, detail="No campaign fixtures")
        signal = run_campaign_learning_loop(root, rows[0])
        return signal.to_dict()

    @app.get("/prompt/assembly/{agent_id}")
    async def prompt_assembly(agent_id: str) -> dict[str, Any]:
        prompt_path = root / "prompts" / f"{agent_id}.md"
        if not prompt_path.exists():
            raise HTTPException(status_code=404, detail=f"No prompt for {agent_id}")
        core = load_core_config()
        model = select_model("economy", core)
        assembly = assemble_prompt(
            prompt_path,
            agent_id=agent_id,
            model=model,
            variables={
                "ssot_snapshot": '{"deal_id":"demo"}',
                "memory_summary": "stage=new",
                "task_context": "demo task",
                "handoff_payload": "{}",
                "pricing_snapshot": "{}",
                "content_payload": "{}",
                "campaign_results": "[]",
                "predictions": "{}",
            },
        )
        return {
            "agent_id": assembly.agent_id,
            "model": assembly.model,
            "cacheable_instructions": assembly.cacheable_instructions,
            "dynamic_context": assembly.dynamic_context,
            "has_cacheable_section": bool(assembly.cacheable_instructions),
            "has_dynamic_section": bool(assembly.dynamic_context),
        }

    return app


def main() -> None:
    import uvicorn

    core = load_core_config()
    host = core["server"]["host"]
    port = core["server"]["port"]
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()