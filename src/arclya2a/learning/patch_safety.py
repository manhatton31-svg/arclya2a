"""Patch safety classification, validation, and auto-apply rules."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Risk by issue type
ISSUE_RISK: dict[str, str] = {
    "reinforcement": "low_risk",
    "objections_not_documented": "medium_risk",
    "onboarding_incomplete": "medium_risk",
    "recruiter_retriggered_onboarding": "medium_risk",
    "demo_tool_failures": "medium_risk",
    "billing_no_deals": "medium_risk",
    "tool_high_skip_rate": "low_risk",
    # High risk — tool behavior, negotiation flow, pricing/attribution
    "tools_called_before_close": "high_risk",
    "tools_called_too_early": "high_risk",
    "tool_high_failure_rate": "high_risk",
    "demo_no_tools_on_close": "high_risk",
    "closer_no_commitment": "high_risk",
    "negotiation_too_short": "high_risk",
    "billing_missing_attribution": "high_risk",
    "billing_low_margin": "high_risk",
    # Defensive security patches
    "injection_scan_rejection": "medium_risk",
    "injection_scan_disqualify": "medium_risk",
    "repeated_injection_pattern": "low_risk",
    "tool_gate_violation": "high_risk",
    "tool_gate_partner_command": "high_risk",
    "tool_gate_premature": "high_risk",
    "sandbox_suspicious_partner": "medium_risk",
    "sandbox_tool_block": "low_risk",
    "emergency_stop_security": "high_risk",
    "high_risk_partner": "medium_risk",
    "suspicious_partner_trust_block": "medium_risk",
    "sandbox_repeat_offender": "medium_risk",
}

HIGH_RISK_ANCHORS = (
    "tool",
    "Tool",
    "negotiation",
    "Negotiation",
    "CTA",
    "pricing",
    "billing",
    "close",
)


@dataclass
class PatchValidation:
    ok: bool
    errors: list[str]
    warnings: list[str]


def classify_patch_risk(patch: dict[str, Any]) -> str:
    """Classify patch as low_risk, medium_risk, or high_risk."""
    issue = patch.get("issue", "")
    if issue in ISSUE_RISK:
        return ISSUE_RISK[issue]

    changes = patch.get("changes") or []
    if not changes:
        return "medium_risk"

    change = changes[0]
    change_type = change.get("type", "")
    content = (change.get("content") or "").lower()
    anchor = (change.get("anchor") or "").lower()

    if change_type == "append_section" and "reinforcement" in content:
        return "low_risk"

    if any(kw in content or kw in anchor for kw in ("tool", "negotiation", "cta", "pricing", "billing")):
        return "high_risk"

    if change_type == "insert_after" and len(change.get("content", "")) < 120:
        return "medium_risk"

    return "medium_risk"


def compute_patch_confidence(patch: dict[str, Any], *, anchor_found: bool | None = None) -> float:
    """Score 0.0–1.0 confidence that patch is correct and safe to apply."""
    score = 0.45
    issue = patch.get("issue", "")

    if issue and issue != "reinforcement":
        score += 0.15
    evidence = patch.get("evidence") or {}
    if evidence.get("tool_failure_rate") is not None:
        score += 0.08
    if evidence.get("issues_detected"):
        score += 0.07
    if patch.get("priority") == "high":
        score += 0.05

    changes = patch.get("changes") or []
    if changes:
        content_len = len(changes[0].get("content", ""))
        if content_len < 150:
            score += 0.12
        elif content_len > 400:
            score -= 0.1

    if anchor_found is True:
        score += 0.15
    elif anchor_found is False:
        score -= 0.2

    risk = patch.get("risk_class") or classify_patch_risk(patch)
    if risk == "low_risk":
        score += 0.08
    elif risk == "high_risk":
        score -= 0.05

    return round(max(0.0, min(1.0, score)), 2)


def validate_patch(root: Path, patch: dict[str, Any]) -> PatchValidation:
    """Basic validation before applying a patch."""
    errors: list[str] = []
    warnings: list[str] = []

    if patch.get("patch_kind") == "injection_pattern":
        patterns_path = root / "learning" / "injection_patterns.json"
        if not patterns_path.exists():
            patterns_path.parent.mkdir(parents=True, exist_ok=True)
            patterns_path.write_text('{"version": 1, "patterns": []}', encoding="utf-8")
        changes = patch.get("changes") or []
        if not changes:
            errors.append("Patch has no changes")
        for i, change in enumerate(changes):
            if change.get("type") != "append_injection_pattern":
                errors.append(f"Change {i}: expected append_injection_pattern")
            elif not change.get("regex", "").strip():
                errors.append(f"Change {i}: injection pattern missing regex")
        return PatchValidation(ok=len(errors) == 0, errors=errors, warnings=warnings)

    target = patch.get("target_prompt", "")
    prompt_path = root / target
    if not prompt_path.exists():
        errors.append(f"Target prompt missing: {target}")

    changes = patch.get("changes") or []
    if not changes:
        errors.append("Patch has no changes")

    prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    for i, change in enumerate(changes):
        ctype = change.get("type")
        if ctype == "append_injection_pattern":
            if not change.get("regex", "").strip():
                errors.append(f"Change {i}: injection pattern missing regex")
            continue

        if ctype not in ("insert_after", "append_section", "replace_line"):
            errors.append(f"Change {i}: unknown type '{ctype}'")
            continue

        content = change.get("content", "").strip()
        if not content:
            errors.append(f"Change {i}: empty content")
            continue

        if content in prompt_text:
            warnings.append(f"Change {i}: content may already exist in prompt (duplicate)")

        if ctype == "insert_after":
            anchor = change.get("anchor", "")
            if anchor and anchor not in prompt_text:
                warnings.append(f"Change {i}: anchor not found — will append fallback")

        if ctype == "replace_line":
            if change.get("old", "") not in prompt_text:
                errors.append(f"Change {i}: replace_line target not found")

    if patch.get("risk_class") == "high_risk":
        warnings.append("High-risk patch — manual review recommended")

    return PatchValidation(ok=len(errors) == 0, errors=errors, warnings=warnings)


def auto_apply_enabled() -> bool:
    raw = os.environ.get("ARCLYA_AUTO_APPLY_LOW_RISK", "1").strip().lower()
    return raw in ("1", "true", "yes")


def min_confidence_for_auto_apply() -> float:
    raw = os.environ.get("ARCLYA_AUTO_APPLY_MIN_CONFIDENCE", "0.75").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.75


def should_auto_apply(patch: dict[str, Any], validation: PatchValidation) -> bool:
    """Determine if a patch can be auto-applied without manual review."""
    from arclya2a.security.cross_agent_isolation import check_patch_isolation

    if not auto_apply_enabled():
        return False
    if not validation.ok:
        return False
    if patch.get("status") == "isolation_blocked":
        return False
    isolation = check_patch_isolation(patch)
    if not isolation.allowed:
        return False
    if patch.get("risk_class") != "low_risk":
        return False
    confidence = patch.get("confidence", 0)
    if confidence < min_confidence_for_auto_apply():
        return False
    if patch.get("status") == "applied":
        return False
    return True


def build_diff_preview(prompt_text: str, change: dict[str, Any]) -> dict[str, str]:
    """Minimal before/after preview for a single change."""
    ctype = change.get("type", "insert_after")
    content = change.get("content", "")

    if ctype == "insert_after":
        anchor = change.get("anchor", "")
        if anchor and anchor in prompt_text:
            idx = prompt_text.index(anchor)
            before = prompt_text[max(0, idx - 20): idx + len(anchor) + 40]
            after = before + "\n" + content
            return {"before": before.strip(), "after": after.strip(), "type": "insert_after"}

    if ctype == "replace_line":
        old = change.get("old", "")
        new = change.get("new", "")
        return {"before": old, "after": new, "type": "replace_line"}

    return {"before": "(end of file)", "after": content[:200], "type": ctype}


def enrich_patch(root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Add risk_class, confidence, diff_preview, and validation metadata."""
    target = root / patch.get("target_prompt", "")
    prompt_text = target.read_text(encoding="utf-8") if target.exists() else ""

    anchor_found = None
    changes = patch.get("changes") or []
    if changes and changes[0].get("type") == "insert_after":
        anchor = changes[0].get("anchor", "")
        anchor_found = bool(anchor and anchor in prompt_text)

    patch["risk_class"] = classify_patch_risk(patch)
    patch["confidence"] = compute_patch_confidence(patch, anchor_found=anchor_found)

    previews = []
    for change in changes:
        previews.append(build_diff_preview(prompt_text, change))
    patch["diff_preview"] = previews

    validation = validate_patch(root, patch)
    patch["validation"] = {
        "ok": validation.ok,
        "errors": validation.errors,
        "warnings": validation.warnings,
    }
    patch["auto_apply_eligible"] = should_auto_apply(patch, validation)

    return patch