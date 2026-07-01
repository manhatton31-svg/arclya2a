"""Registry-driven xAI agent execution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from arclya2a.config.product_profile import (
    build_destination_cta,
    load_profile_snapshot,
    save_agent_profile,
    validate_product_profile,
)
from arclya2a.learning.campaign_loop import compute_deltas, build_recommendations
from arclya2a.learning.prompt_updater import apply_learning_signal, load_learned_context, resolve_prompt_path
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

    profile_snapshot = load_profile_snapshot(root, ssot)

    return {
        "ssot_snapshot": json.dumps(ssot, indent=2),
        "product_profile_snapshot": json.dumps(profile_snapshot, indent=2),
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
        "learned_context": load_learned_context(root, agent["id"]),
        "role_card": agent.get("role_card", ""),
        "good_enough": agent.get("good_enough", ""),
    }


def _infer_xai(
    agent: dict[str, Any],
    root: Path,
    context: dict[str, Any],
    xai_client: XAIClient | None,
    inference_meta: dict[str, Any],
) -> dict[str, Any]:
    """Call xAI with assembled prompt; returns (parsed handoff, inference metadata)."""
    client = xai_client or XAIClient(root, api_key=context.get("xai_api_key"))
    with open(root / "config" / "core.json", encoding="utf-8") as f:
        core = json.load(f)
    model = select_model(agent.get("model_tier", "economy"), core)
    prompt_file = agent.get("prompt_file", f"{agent['id']}.md")
    prompt_path = root / "prompts" / prompt_file
    if not prompt_path.exists():
        prompt_path = resolve_prompt_path(root, agent["id"])
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

    variables = _build_prompt_variables(agent, context["ssot"], context, root)
    assembly = assemble_prompt(
        prompt_path,
        agent_id=agent["id"],
        model=model,
        variables=variables,
    )

    inference_meta.update({
        "model": model,
        "prompt_assembled": True,
        "cost_record": None,
        "inference_failed": False,
    })

    def _call() -> dict[str, Any]:
        data = client.chat_completion(
            messages=[
                {"role": "system", "content": assembly.cacheable_instructions},
                {"role": "user", "content": assembly.dynamic_context},
            ],
            model=model,
            agent_id=agent["id"],
        )
        inference_meta["cost_record"] = data.get("cost_record")
        content = data["choices"][0]["message"]["content"]
        parsed = parse_agent_json_response(content)
        return parsed

    try:
        return execute_with_error_policy(
            agent.get("error_policy", "retry_once_then_escalate"),
            _call,
            root=root,
            agent_id=agent["id"],
        )
    except AgentExecutionError:
        inference_meta["inference_failed"] = True
        inference_meta["cost_record"] = client.record_cost(
            agent_id=agent["id"],
            model=model,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
        )
        raise


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

    if agent["id"] == "outreach_worker" and conf < 70:
        validation["check"] = f"Below good_enough threshold (70): {validation.get('check', '')}"

    if agent["id"] == "profit_guardrail" and conf < 85 and handoff.get("status") == "COMPLETE":
        validation["check"] = f"Margin check confidence {conf} < 85"

    if agent["id"] == "final_arbiter":
        qc = handoff.get("payload", {}).get("qc_result", {})
        if qc.get("passed") and conf < 90:
            validation["check"] = f"QC pass confidence {conf} < 90"

    if agent["id"] == "meta_optimizer":
        min_quality = metrics.get("improvement_signal_quality", 80)
        signal = handoff.get("payload", {}).get("improvement_signal", {})
        if signal and conf < min_quality:
            validation["check"] = f"Signal quality confidence {conf} < {min_quality}"

    return handoff


def _validate_profit_guardrail(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Attach margin math; veto only when margin fails (constitutional veto_power)."""
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
            "confidence": max(handoff.get("validation", {}).get("confidence", 0), 95),
            "check": result.veto_reason or "Vetoed by profit guardrail",
        }
    elif handoff.get("status") == "COMPLETE":
        updates = handoff.get("ssot_updates") or {}
        updates.setdefault("metadata", {})["margin_percent"] = result.margin_percent
        handoff["ssot_updates"] = updates

    return handoff


def _validate_final_arbiter(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Supplement LLM QC with SSOT draft; only fail if structural fields missing."""
    ssot = context["ssot"]
    draft = ssot.get("metadata", {}).get("draft", {})
    payload = handoff.setdefault("payload", {})
    qc = payload.get("qc_result", {})
    issues = list(qc.get("issues", []))

    if not draft.get("subject"):
        issues.append("Missing subject line")
    if not draft.get("body"):
        issues.append("Missing body")

    passed = qc.get("passed", False) and len(issues) == 0
    payload["qc_result"] = {"passed": passed, "issues": issues}
    payload["draft"] = draft

    if passed and handoff.get("status") == "COMPLETE":
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "qc_passed"}
    elif not passed and handoff.get("status") == "COMPLETE":
        policy = agent.get("error_policy", "reject_and_return")
        if policy == "reject_and_return":
            handoff["next_action"] = "return_to_outreach_worker"
            handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": "qc_failed"}

    return handoff


def _validate_meta_optimizer(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Merge LLM improvement_signal with computed campaign deltas; apply patches."""
    fixtures_path = root / "data" / "campaign_results" / "fixtures.json"
    rows = json.loads(fixtures_path.read_text(encoding="utf-8"))
    row = rows[0]
    deltas = compute_deltas(row["predicted"], row["actual"])
    computed_recs = build_recommendations(deltas)

    llm_signal = handoff.get("payload", {}).get("improvement_signal", {})
    merged_recs = list(dict.fromkeys(
        (llm_signal.get("recommendations") or []) + computed_recs
    ))

    merged_signal = {
        "campaign_id": row["campaign_id"],
        "deltas": deltas,
        "recommendations": merged_recs,
        "priority": llm_signal.get("priority", "high" if any(d < -0.05 for d in deltas.values()) else "medium"),
        "meta_optimizer_target": "prompts/outreach_worker.md",
    }
    handoff.setdefault("payload", {})["improvement_signal"] = merged_signal

    if handoff.get("status") == "COMPLETE":
        applied = apply_learning_signal(root, {
            "campaign_id": row["campaign_id"],
            "improvement_signal": merged_signal,
        })
        handoff["payload"]["prompt_patch"] = applied

    return handoff


def _validate_onboarding_specialist(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Validate and persist product profile before marking onboarding complete."""
    payload = handoff.setdefault("payload", {})
    profile = payload.get("product_profile", {})
    complete_flag = payload.get("onboarding_complete", False)
    is_complete, missing = validate_product_profile(profile)

    if complete_flag and not is_complete:
        payload["onboarding_complete"] = False
        payload["missing_fields"] = missing
        payload["validation_errors"] = missing
        handoff["status"] = "COMPLETE"
        handoff["next_action"] = "continue_onboarding"
        handoff["validation"] = {
            "confidence": 50,
            "check": f"Profile incomplete; fix: {', '.join(missing)}",
        }
        handoff["ssot_updates"] = {
            "stage": "onboarding_in_progress",
            "metadata": {
                "product_profile": profile,
                "product_profile_complete": False,
                "onboarding_complete": False,
                "missing_fields": missing,
            },
        }
        return handoff

    if is_complete:
        agent_id = profile.get("agent_name", "unknown_agent").lower().replace(" ", "_")
        save_agent_profile(root, agent_id, profile)
        payload["onboarding_complete"] = True
        payload["missing_fields"] = []
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {
            "stage": "onboarded",
            "summary": f"Onboarded: {profile.get('product_name', 'product')}",
            "metadata": {
                "product_profile": profile,
                "product_profile_complete": True,
                "onboarding_complete": True,
                "onboarding_status": "complete",
                "destination_cta": build_destination_cta(profile),
            },
        }
        targets = agent.get("handoff_targets", [])
        if handoff.get("status") == "COMPLETE" and targets:
            handoff["next_action"] = f"handoff_to_{targets[0]}"
    else:
        payload["onboarding_complete"] = False
        payload["missing_fields"] = missing
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {
            "stage": "onboarding_in_progress",
            "metadata": {
                "product_profile": profile,
                "product_profile_complete": False,
                "onboarding_complete": False,
                "missing_fields": missing,
            },
        }

    return handoff


def _validate_closer(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Ensure closer only runs for onboarded agents with product profile."""
    ssot = context["ssot"]
    meta = ssot.get("metadata", {})
    if not meta.get("product_profile_complete") and not meta.get("onboarding_complete"):
        handoff["status"] = "EMERGENCY_STOP"
        handoff["next_action"] = "route_to_onboarding"
        handoff["validation"] = {
            "confidence": 95,
            "check": "Closer blocked: agent not onboarded",
        }
        return handoff

    close_pkg = handoff.get("payload", {}).get("close_package", {})
    profile = meta.get("product_profile", {})
    if close_pkg and profile:
        cta = build_destination_cta(profile)
        close_pkg["cta_url"] = cta
        close_pkg["destination_link"] = profile.get("destination_link", "")
        close_pkg["affiliate_code"] = profile.get("affiliate_code", "")
        close_pkg.setdefault("pricing_model", "success_based_pay_on_close")

    if handoff.get("status") == "COMPLETE":
        stage = "lead_routing_committed" if close_pkg.get("lead_routing_confirmed") else "close_package_ready"
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {"stage": stage}

    return handoff


def _validate_recruiter(
    agent: dict[str, Any], handoff: dict[str, Any], root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    """Tag acquisition stage; route to guardrail chain (not onboarding)."""
    if handoff.get("status") == "COMPLETE":
        stage = handoff.get("payload", {}).get("acquisition_stage", "invited")
        handoff["ssot_updates"] = handoff.get("ssot_updates") or {
            "stage": "recruiting",
            "metadata": {"acquisition_stage": stage},
        }
        ssot = context["ssot"]
        meta = ssot.get("metadata", {})
        onboarded = meta.get("onboarding_complete") or meta.get("product_profile_complete")
        if not onboarded:
            handoff["next_action"] = "handoff_to_onboarding_specialist"
        else:
            targets = agent.get("handoff_targets", [])
            if targets:
                handoff["next_action"] = f"handoff_to_{targets[0]}"
    return handoff


VALIDATORS = {
    "onboarding_specialist": _validate_onboarding_specialist,
    "recruiter": _validate_recruiter,
    "closer": _validate_closer,
    "profit_guardrail": _validate_profit_guardrail,
    "final_arbiter": _validate_final_arbiter,
    "meta_optimizer": _validate_meta_optimizer,
}


def run_registry_agent(
    agent: dict[str, Any],
    ssot: dict[str, Any],
    root: Path,
    context: dict[str, Any],
    xai_client: XAIClient | None = None,
) -> dict[str, Any]:
    """Execute one agent turn via xAI; validators supplement LLM decisions."""
    context = {**context, "ssot": ssot}
    inference_meta: dict[str, Any] = {
        "model": None,
        "prompt_assembled": False,
        "cost_record": None,
        "inference_failed": True,
    }

    try:
        llm_handoff = _infer_xai(agent, root, context, xai_client, inference_meta)
    except AgentExecutionError as exc:
        err_handoff = handoff_from_policy_error(agent, exc)
        err_handoff["inference"] = inference_meta
        return err_handoff

    handoff = {
        "status": llm_handoff.get("status", "COMPLETE"),
        "next_action": llm_handoff.get("next_action", ""),
        "payload": {
            k: v
            for k, v in llm_handoff.items()
            if k
            not in (
                "status",
                "next_action",
                "ssot_updates",
                "validation",
                "preference_handshake",
            )
        },
        "ssot_updates": llm_handoff.get("ssot_updates"),
        "validation": llm_handoff.get("validation", {"confidence": 70, "check": "xAI turn"}),
        "preference_handshake": llm_handoff.get("preference_handshake"),
        "inference": inference_meta,
    }

    validator = VALIDATORS.get(agent["id"])
    if validator:
        handoff = validator(agent, handoff, root, context)

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
        if handoff.get("status") == "COMPLETE" and targets and not handoff.get("next_action"):
            handoff["next_action"] = f"handoff_to_{targets[0]}"

    return handoff