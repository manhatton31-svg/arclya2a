"""Registry-driven xAI agent execution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from arclya2a.learning.campaign_loop import run_campaign_learning_loop
from arclya2a.learning.prompt_updater import apply_learning_signal, load_learned_context
from arclya2a.orchestrator.error_policy import (
    AgentExecutionError,
    execute_with_error_policy,
    handoff_from_policy_error,
)
from arclya2a.pricing.margin import check_margin_guardrail, load_pricing_menu
from arclya2a.xai.client import XAIClient, assemble_prompt, select_model


def resolve_chain_from_registry(agents: dict[str, Any], start_id: str) -> list[str]:
    """Build chain by following handoff_targets from registry."""
    chain = []
    current = start_id
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        chain.append(current)
        agent = agents.get(current)
        if not agent:
            break
        targets = agent.get("handoff_targets", [])
        current = targets[0] if targets else ""
    return chain


def parse_agent_json_response(content: str) -> dict[str, Any]:
    """Extract JSON object from xAI response content."""
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1)
    else:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start : end + 1]
    return json.loads(content)


def _build_prompt_variables(
    agent: dict[str, Any],
    ssot: dict[str, Any],
    context: dict[str, Any],
    root: Path,
) -> dict[str, str]:
    pricing = load_pricing_menu(root)
    prev = context.get("previous_handoff") or {}
    draft = ssot.get("metadata", {}).get("draft", {})
    campaign_path = root / "data" / "campaign_results" / "fixtures.json"
    campaign_rows = []
    if campaign_path.exists():
        campaign_rows = json.loads(campaign_path.read_text(encoding="utf-8"))

    learned = load_learned_context(root, agent["id"])
    return {
        "ssot_snapshot": json.dumps(ssot, indent=2),
        "memory_summary": context.get("memory_summary", ssot.get("summary", "")),
        "task_context": context.get("task_context", ""),
        "handoff_payload": json.dumps(prev, indent=2),
        "pricing_snapshot": json.dumps(pricing, indent=2),
        "content_payload": json.dumps(draft, indent=2),
        "campaign_results": json.dumps(campaign_rows, indent=2),
        "predictions": json.dumps(
            campaign_rows[0].get("predicted", {}) if campaign_rows else {},
            indent=2,
        ),
        "learned_context": learned,
        "role_card": agent.get("role_card", ""),
        "good_enough": agent.get("good_enough", ""),
    }


def _infer_xai(agent: dict[str, Any], root: Path, context: dict[str, Any], xai_client: XAIClient | None) -> dict[str, Any]:
    """Call xAI with assembled prompt; returns parsed JSON handoff fields."""
    client = xai_client or XAIClient(root, api_key=context.get("xai_api_key"))
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        core = json.load(f)
    model = select_model(agent.get("model_tier", "economy"), core)
    prompt_path = root / "prompts" / f"{agent['id']}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

    variables = _build_prompt_variables(agent, context["ssot"], context, root)
    assembly = assemble_prompt(
        prompt_path,
        agent_id=agent["id"],
        model=model,
        variables=variables,
    )

    dynamic = assembly.dynamic_context
    if variables.get("learned_context"):
        dynamic = f"{dynamic}\n\n{variables['learned_context']}"

    def _call() -> dict[str, Any]:
        data = client.chat_completion(
            messages=[
                {"role": "system", "content": assembly.cacheable_instructions},
                {"role": "user", "content": dynamic},
            ],
            model=model,
            agent_id=agent["id"],
        )
        content = data["choices"][0]["message"]["content"]
        parsed = parse_agent_json_response(content)
        parsed["_xai_model"] = model
        parsed["_prompt_assembled"] = True
        return parsed

    return execute_with_error_policy(
        agent.get("error_policy", "retry_once_then_escalate"),
        _call,
        root=root,
        agent_id=agent["id"],
    )


def _apply_handoff_targets(agent: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    """Set next_action from registry handoff_targets when COMPLETE and missing."""
    if handoff.get("status") == "COMPLETE" and not handoff.get("next_action"):
        targets = agent.get("handoff_targets", [])
        if targets:
            handoff["next_action"] = f"handoff_to_{targets[0]}"
    return handoff


def _check_success_metrics(agent: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    """Validate confidence against registry good_enough / success_metrics."""
    validation = handoff.setdefault("validation", {"confidence": 0, "check": ""})
    conf = validation.get("confidence", 0)
    metrics = agent.get("success_metrics", {})

    if agent["id"] == "outreach_worker":
        min_conf = 70
        if conf < min_conf:
            validation["check"] = f"Below good_enough threshold ({min_conf}): {validation.get('check', '')}"

    if agent["id"] == "profit_guardrail":
        min_conf = 85
        if conf < min_conf and handoff.get("status") == "COMPLETE":
            validation["check"] = f"Margin check confidence {conf} < {min_conf}"

    if agent["id"] == "final_arbiter":
        min_conf = 90
        qc = handoff.get("payload", {}).get("qc_result", {})
        if qc.get("passed") and conf < min_conf:
            validation["check"] = f"QC pass confidence {conf} < {min_conf}"

    if agent["id"] == "meta_optimizer":
        min_quality = metrics.get("improvement_signal_quality", 80)
        signal = handoff.get("payload", {}).get("improvement_signal", {})
        if signal and conf < min_quality:
            validation["check"] = f"Signal quality confidence {conf} < {min_quality}"

    return handoff


def _enrich_profit_guardrail(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic margin veto with veto_power from registry."""
    result = check_margin_guardrail(
        root,
        revenue_usd=context["revenue_usd"],
        cost_usd=context["estimated_cost_usd"],
    )
    handoff.setdefault("payload", {})["margin_check"] = {
        "approved": result.approved,
        "margin_percent": result.margin_percent,
        "veto_reason": result.veto_reason,
        "revenue_usd": result.revenue_usd,
        "cost_usd": result.cost_usd,
    }

    if agent.get("veto_power") and not result.approved:
        handoff["status"] = "EMERGENCY_STOP"
        handoff["next_action"] = "halt_deal_margin_violation"
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "margin_vetoed"}
        handoff["validation"] = {
            "confidence": 95,
            "check": result.veto_reason or "Vetoed by profit guardrail",
        }
    elif handoff.get("status") == "COMPLETE":
        targets = agent.get("handoff_targets", [])
        handoff["next_action"] = f"handoff_to_{targets[0]}" if targets else "continue"
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {
            "stage": "margin_approved",
            "metadata": {"margin_percent": result.margin_percent},
        }
        handoff["validation"] = handoff.get("validation") or {
            "confidence": 92,
            "check": f"Margin {result.margin_percent:.1f}% approved",
        }

    return handoff


def _enrich_final_arbiter(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Merge SSOT draft into QC payload."""
    ssot = context["ssot"]
    draft = ssot.get("metadata", {}).get("draft", {})
    payload = handoff.setdefault("payload", {})
    qc = payload.get("qc_result", {})
    issues = list(qc.get("issues", []))
    if not draft.get("subject"):
        issues.append("Missing subject line")
    if not draft.get("body"):
        issues.append("Missing body")
    passed = qc.get("passed", True) and len(issues) == 0
    payload["qc_result"] = {"passed": passed, "issues": issues}
    payload["draft"] = draft

    if passed:
        handoff["status"] = "COMPLETE"
        handoff["next_action"] = "deliver_to_customer"
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "qc_passed"}
    else:
        policy = agent.get("error_policy", "reject_and_return")
        if policy == "reject_and_return":
            handoff["status"] = "COMPLETE"
            handoff["next_action"] = "return_to_outreach_worker"
            handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "qc_failed"}

    return handoff


def _enrich_meta_optimizer(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Run campaign learning loop and apply prompt patches."""
    fixtures_path = root / "data" / "campaign_results" / "fixtures.json"
    rows = json.loads(fixtures_path.read_text(encoding="utf-8"))
    signal = run_campaign_learning_loop(root, rows[0])

    llm_signal = handoff.get("payload", {}).get("improvement_signal", {})
    merged_signal = {
        "campaign_id": signal.campaign_id,
        "deltas": signal.deltas,
        "recommendations": signal.recommendations or llm_signal.get("recommendations", []),
        "priority": signal.priority,
        "improvement_signal": signal.improvement_signal,
    }
    handoff.setdefault("payload", {})["improvement_signal"] = merged_signal
    applied = apply_learning_signal(root, signal.to_dict())
    handoff["payload"]["prompt_patch"] = applied
    handoff["status"] = "COMPLETE"
    handoff["next_action"] = "update_learning_store"
    handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "learning_applied"}
    handoff["validation"] = handoff.get("validation") or {
        "confidence": 82,
        "check": f"Applied {applied['patches_applied']} prompt patches",
    }
    return handoff


ENRICHERS = {
    "profit_guardrail": _enrich_profit_guardrail,
    "final_arbiter": _enrich_final_arbiter,
    "meta_optimizer": _enrich_meta_optimizer,
}


def run_registry_agent(
    agent: dict[str, Any],
    ssot: dict[str, Any],
    root: Path,
    context: dict[str, Any],
    xai_client: XAIClient | None = None,
) -> dict[str, Any]:
    """Execute one agent turn via xAI + registry enrichers."""
    context = {**context, "ssot": ssot}

    try:
        llm_handoff = _infer_xai(agent, root, context, xai_client)
    except AgentExecutionError as exc:
        return handoff_from_policy_error(agent, exc)

    handoff = {
        "status": llm_handoff.get("status", "COMPLETE"),
        "next_action": llm_handoff.get("next_action", ""),
        "payload": {k: v for k, v in llm_handoff.items() if k not in (
            "status", "next_action", "ssot_updates", "validation",
            "preference_handshake", "_xai_model", "_prompt_assembled",
        )},
        "ssot_updates": llm_handoff.get("ssot_updates"),
        "validation": llm_handoff.get("validation", {"confidence": 70, "check": "xAI turn"}),
        "preference_handshake": llm_handoff.get("preference_handshake"),
        "inference": {
            "model": llm_handoff.get("_xai_model"),
            "prompt_assembled": llm_handoff.get("_prompt_assembled", False),
        },
    }

    enricher = ENRICHERS.get(agent["id"])
    if enricher:
        handoff = enricher(agent, handoff, root, context)

    handoff = _apply_handoff_targets(agent, handoff)
    handoff = _check_success_metrics(agent, handoff)

    if agent["id"] == "outreach_worker":
        draft = handoff.get("payload", {}).get("draft", {})
        if draft:
            handoff["ssot_updates"] = handoff.get("ssot_updates") or {
                "stage": "draft_ready",
                "summary": f"Draft created: {draft.get('subject', 'untitled')}",
                "metadata": {"draft": draft},
            }
        targets = agent.get("handoff_targets", [])
        if handoff.get("status") == "COMPLETE" and targets:
            handoff["next_action"] = f"handoff_to_{targets[0]}"

    return handoff