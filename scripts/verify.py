"""Run verification plan and capture SCRATCH evidence via shipped HTTP endpoints."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("SCRATCH", r"C:\Users\cwhat\AppData\Local\Temp\grok-goal-78bc0efe05fb\implementer"))
SCRATCH.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "src"))

from arclya2a.learning.campaign_loop import run_campaign_learning_loop
from arclya2a.server.app import create_app
from arclya2a.xai.client import XAIClient
from fastapi.testclient import TestClient

_AGENT_CONTEXT = {"id": "outreach_worker"}


def _mock_responses(agent_id: str) -> dict:
    mapping = {
        "outreach_worker": {
            "status": "COMPLETE",
            "next_action": "handoff_to_profit_guardrail",
            "draft": {"subject": "Verify subject", "body": "Verify body"},
            "validation": {"confidence": 78, "check": "ok"},
            "preference_handshake": {"format": "json", "accepted": True},
        },
        "profit_guardrail": {
            "status": "COMPLETE",
            "next_action": "handoff_to_final_arbiter",
            "validation": {"confidence": 92, "check": "margin ok"},
        },
        "final_arbiter": {
            "status": "COMPLETE",
            "next_action": "deliver_to_customer",
            "qc_result": {"passed": True, "issues": []},
            "validation": {"confidence": 94, "check": "qc ok"},
        },
        "meta_optimizer": {
            "status": "COMPLETE",
            "next_action": "update_learning_store",
            "improvement_signal": {"recommendations": ["test rec"], "priority": "high"},
            "validation": {"confidence": 85, "check": "signal ok"},
        },
    }
    return mapping.get(agent_id, {"status": "COMPLETE", "validation": {"confidence": 70, "check": "ok"}})


def _build_mock_client() -> XAIClient:
    import json as json_module

    original = XAIClient.chat_completion

    def wrapped(self, *, messages, model, agent_id):
        _AGENT_CONTEXT["id"] = agent_id
        return original(self, messages=messages, model=model, agent_id=agent_id)

    def fake_post(self, url, json=None, headers=None, **kwargs):
        body = _mock_responses(_AGENT_CONTEXT["id"])
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": json_module.dumps(body)}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "cached_tokens": 400},
        }
        return response

    httpx.Client.post = fake_post
    XAIClient.chat_completion = wrapped
    return XAIClient(ROOT, api_key="verify-mock-key")


def _test_client() -> TestClient:
    return TestClient(create_app(ROOT, xai_client=_build_mock_client()))


def capture_agent_cards():
    client = _test_client()
    for i in (1, 2):
        resp = client.get("/.well-known/agent-card.json")
        data = resp.json()
        (SCRATCH / f"agent-card-run{i}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def capture_handoff_chain():
    client = _test_client()
    resp = client.post(
        "/orchestrate/handoff-chain",
        json={
            "deal_id": "deal_verify_001",
            "customer_company": "VerifyCo",
            "task_context": "Verification handoff chain",
            "revenue_usd": 49.0,
            "estimated_cost_usd": 5.0,
        },
    )
    (SCRATCH / "handoff-chain.json").write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")


def capture_prompt_assembly():
    client = _test_client()
    resp = client.get("/prompt/assembly/outreach_worker")
    (SCRATCH / "prompt-assembly.json").write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")


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
        log_path.write_text(
            json.dumps({"host": client.XAI_HOST, "model": "grok-3-mini", "cost_record": data.get("cost_record")}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log_path.write_text(f"xAI call failed: {e}\n", encoding="utf-8")


def capture_learning_loop():
    with open(ROOT / "data" / "campaign_results" / "fixtures.json", encoding="utf-8") as f:
        row = json.load(f)[0]
    signal = run_campaign_learning_loop(ROOT, row)
    client = _test_client()
    resp = client.post("/learning/campaign")
    out = {"signal": signal.to_dict(), "http_response": resp.json()}
    (SCRATCH / "learning-loop.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


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