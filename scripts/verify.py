"""Run verification plan and capture SCRATCH evidence."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("SCRATCH", r"C:\Users\cwhat\AppData\Local\Temp\grok-goal-78bc0efe05fb\implementer"))
SCRATCH.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "src"))

from arclya2a.learning.campaign_loop import run_campaign_learning_loop
from arclya2a.orchestrator.engine import Orchestrator
from arclya2a.server.app import create_app
from arclya2a.xai.client import XAIClient, assemble_prompt, select_model
from fastapi.testclient import TestClient


def capture_agent_cards():
    client = TestClient(create_app(ROOT))
    for i in (1, 2):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        (SCRATCH / f"agent-card-run{i}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def capture_handoff_chain():
    orchestrator = Orchestrator(ROOT)
    result = orchestrator.run_chain(
        chain=["outreach_worker", "profit_guardrail", "final_arbiter"],
        initial_ssot={
            "deal_id": "deal_verify_001",
            "summary": "Verification deal",
            "customer": {"company": "VerifyCo"},
            "deal_value_usd": 49.0,
            "stage": "new",
            "metadata": {},
        },
        task_context="Verification handoff chain",
        revenue_usd=49.0,
        estimated_cost_usd=5.0,
    )
    payload = {
        "handoff_chain": result.handoff_chain,
        "final_ssot": result.final_ssot,
        "audit_ids": result.audit_ids,
        "cost_records": result.cost_records,
    }
    (SCRATCH / "handoff-chain.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def capture_prompt_assembly():
    with open(ROOT / "config" / "core.json", encoding="utf-8") as f:
        core = json.load(f)
    model = select_model("economy", core)
    assembly = assemble_prompt(
        ROOT / "prompts" / "outreach_worker.md",
        agent_id="outreach_worker",
        model=model,
        variables={"ssot_snapshot": "{}", "memory_summary": "m", "task_context": "verify"},
    )
    out = {
        "cacheable_instructions": assembly.cacheable_instructions,
        "dynamic_context": assembly.dynamic_context,
        "model": assembly.model,
        "separated": bool(assembly.cacheable_instructions) and bool(assembly.dynamic_context),
    }
    (SCRATCH / "prompt-assembly.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


def capture_xai_call():
    client = XAIClient(ROOT)
    log_path = SCRATCH / "xai-call.log"
    env_missing_path = SCRATCH / "xai-env-missing.log"
    if not client.api_key:
        env_missing_path.write_text("XAI_API_KEY is not set\n", encoding="utf-8")
        return
    try:
        data = client.chat_completion(
            messages=[{"role": "user", "content": "Reply with OK"}],
            model="grok-3-mini",
            agent_id="verify",
        )
        log_path.write_text(json.dumps({"host": client.XAI_HOST, "model": "grok-3-mini", "response": data}, indent=2), encoding="utf-8")
    except Exception as e:
        log_path.write_text(f"xAI call failed: {e}\n", encoding="utf-8")


def capture_learning_loop():
    with open(ROOT / "data" / "campaign_results" / "fixtures.json", encoding="utf-8") as f:
        row = json.load(f)[0]
    signal = run_campaign_learning_loop(ROOT, row)
    (SCRATCH / "learning-loop.json").write_text(json.dumps(signal.to_dict(), indent=2), encoding="utf-8")


def capture_structure():
    lines = []
    for name in sorted(ROOT.iterdir()):
        if name.is_dir() and not name.name.startswith("."):
            lines.append(f"[DIR] {name.name}/")
    for rel in ["agents/registry.json", "pricing/pricing_menu.json", "config/core.json"]:
        p = ROOT / rel
        lines.append(f"[FILE] {rel} ({p.stat().st_size} bytes)")
    (SCRATCH / "structure.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    capture_agent_cards()
    capture_handoff_chain()
    capture_prompt_assembly()
    capture_xai_call()
    capture_learning_loop()
    capture_structure()
    print(f"Verification artifacts written to {SCRATCH}")


if __name__ == "__main__":
    main()