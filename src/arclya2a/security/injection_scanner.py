"""Scan external agent content for prompt injection and manipulation."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Built-in patterns: (id, label, regex, severity 0-1, category)
_BUILTIN_PATTERNS: list[tuple[str, str, str, float, str]] = [
    (
        "instruction_override",
        "Direct instruction override",
        r"(?i)\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?|prompts?)\b",
        0.95,
        "direct",
    ),
    (
        "role_hijack",
        "Role / persona hijack",
        r"(?i)\b(you are now|act as|pretend to be|developer mode|jailbreak|dan mode)\b",
        0.9,
        "direct",
    ),
    (
        "fake_system",
        "Embedded fake system message",
        r"(?i)(^|\n)\s*(system|assistant|operator|admin)\s*:\s*",
        0.85,
        "direct",
    ),
    (
        "new_prompt_injection",
        "New prompt injection",
        r"(?i)\b(new prompt|updated instructions?|override guardrails?|bypass\s+(billing|guardrails?|validation))\b",
        0.9,
        "direct",
    ),
    (
        "premature_tool",
        "Premature tool execution demand",
        r"(?i)\b(call tool|execute\s+(gmail|linear|calendar|notion)|send\s+(the\s+)?email\s+now|create\s+(the\s+)?task\s+now)\b",
        0.75,
        "indirect",
    ),
    (
        "false_close_claim",
        "Unverified close / commitment claim",
        r"(?i)\b(deal is closed|commitment confirmed|lead_routing_confirmed\s*:\s*true|mark\s+(it\s+)?closed)\b",
        0.7,
        "indirect",
    ),
    (
        "sandbox_escalation",
        "Sandbox privilege escalation",
        r"(?i)\b(sandbox exempt|production privileges?|graduated partner|real api key|disable sandbox)\b",
        0.8,
        "indirect",
    ),
    (
        "off_platform_routing",
        "Off-platform data exfiltration",
        r"(?i)\b(webhook\s*:|send\s+to\s+https?://(?!seller\.|example\.com)[^\s]+|post\s+results?\s+to)\b",
        0.65,
        "indirect",
    ),
    (
        "urgency_coercion",
        "Urgency coercion without substance",
        r"(?i)\b(must close now|api will expire|immediate action required|time.?sensitive.*close)\b",
        0.55,
        "indirect",
    ),
]

SCAN_EVENTS_PATH = "learning/injection_scan_events.jsonl"
LEARNED_PATTERNS_PATH = "learning/injection_patterns.json"

REJECT_CONFIDENCE = 0.65
DISQUALIFY_CONFIDENCE = 0.85
SCAN_BLOCK_ACTIONS = frozenset({"reject", "disqualify"})


@dataclass
class InjectionScanResult:
    is_suspicious: bool
    confidence: float
    detected_patterns: list[dict[str, Any]] = field(default_factory=list)
    recommended_action: str = "continue"
    content_sources: list[str] = field(default_factory=list)
    scan_id: str = ""
    agent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _patterns_path(root: Path) -> Path:
    return root / LEARNED_PATTERNS_PATH


def _scan_events_path(root: Path) -> Path:
    return root / SCAN_EVENTS_PATH


def load_learned_patterns(root: Path) -> list[tuple[str, str, str, float, str]]:
    """Load operator/learning-tuned patterns from learning/injection_patterns.json."""
    path = _patterns_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[tuple[str, str, str, float, str]] = []
    for entry in data.get("patterns", []):
        pid = entry.get("id", "learned")
        label = entry.get("label", pid)
        regex = entry.get("regex", "")
        severity = float(entry.get("severity", 0.6))
        category = entry.get("category", "learned")
        if regex:
            out.append((pid, label, regex, severity, category))
    return out


def all_patterns(root: Path) -> list[tuple[str, str, str, float, str]]:
    return _BUILTIN_PATTERNS + load_learned_patterns(root)


def _flatten_text(value: Any, *, prefix: str = "") -> list[tuple[str, str]]:
    """Extract scannable string segments from nested structures."""
    segments: list[tuple[str, str]] = []
    if value is None:
        return segments
    if isinstance(value, str):
        text = value.strip()
        if text:
            segments.append((prefix or "text", text))
        return segments
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            segments.extend(_flatten_text(item, prefix=path))
        return segments
    if isinstance(value, list):
        for idx, item in enumerate(value):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            segments.extend(_flatten_text(item, prefix=path))
        return segments
    text = str(value).strip()
    if text:
        segments.append((prefix or "value", text))
    return segments


def collect_external_content(
    agent_id: str,
    ssot: dict[str, Any],
    context: dict[str, Any],
) -> list[tuple[str, str]]:
    """Gather untrusted text sources for an agent turn."""
    segments: list[tuple[str, str]] = []
    meta = ssot.get("metadata") or {}

    task = (context.get("task_context") or "").strip()
    if task:
        segments.append(("task_context", task))

    prev = context.get("previous_handoff") or {}
    if prev:
        segments.extend(_flatten_text(prev, prefix="handoff_payload"))

    profile = meta.get("product_profile") or context.get("product_profile") or {}
    if profile:
        segments.extend(_flatten_text(profile, prefix="product_profile"))

    initial_profile = (ssot.get("metadata") or {}).get("product_profile")
    if initial_profile and initial_profile != profile:
        segments.extend(_flatten_text(initial_profile, prefix="ssot.product_profile"))

    if agent_id == "closer":
        draft = meta.get("draft") or {}
        segments.extend(_flatten_text(draft, prefix="negotiation_draft"))

    return segments


def scan_text(
    text: str,
    *,
    root: Path,
    source: str = "text",
) -> list[dict[str, Any]]:
    """Scan a single text blob; returns list of detected pattern dicts."""
    if not text or not text.strip():
        return []
    detected: list[dict[str, Any]] = []
    for pid, label, pattern, severity, category in all_patterns(root):
        try:
            match = re.search(pattern, text, re.MULTILINE)
        except re.error:
            continue
        if match:
            excerpt = text[max(0, match.start() - 20) : min(len(text), match.end() + 40)]
            detected.append({
                "id": pid,
                "label": label,
                "category": category,
                "severity": severity,
                "source": source,
                "excerpt": excerpt.replace("\n", " ")[:120],
            })
    return detected


def _aggregate_confidence(patterns: list[dict[str, Any]]) -> float:
    if not patterns:
        return 0.0
    # Combine severities: max + fractional boost for additional hits
    severities = sorted((p["severity"] for p in patterns), reverse=True)
    base = severities[0]
    extra = sum(0.05 for _ in severities[1:4])
    return min(1.0, round(base + extra, 3))


def _recommend_action(confidence: float, agent_id: str) -> str:
    if confidence >= DISQUALIFY_CONFIDENCE:
        return "disqualify" if agent_id == "closer" else "reject"
    if confidence >= REJECT_CONFIDENCE:
        return "reject"
    if confidence >= 0.35:
        return "caution"
    return "continue"


def scan_external_content(
    root: Path,
    *,
    agent_id: str,
    ssot: dict[str, Any],
    context: dict[str, Any],
) -> InjectionScanResult:
    """Scan all external content for an agent turn."""
    segments = collect_external_content(agent_id, ssot, context)
    all_detected: list[dict[str, Any]] = []
    sources: list[str] = []

    for source, text in segments:
        hits = scan_text(text, root=root, source=source)
        if hits:
            sources.append(source)
            all_detected.extend(hits)

    # Deduplicate by pattern id keeping highest severity per id
    by_id: dict[str, dict[str, Any]] = {}
    for p in all_detected:
        existing = by_id.get(p["id"])
        if not existing or p["severity"] > existing["severity"]:
            by_id[p["id"]] = p
    patterns = list(by_id.values())
    confidence = _aggregate_confidence(patterns)
    is_suspicious = confidence >= 0.35

    return InjectionScanResult(
        is_suspicious=is_suspicious,
        confidence=confidence,
        detected_patterns=patterns,
        recommended_action=_recommend_action(confidence, agent_id),
        content_sources=sources,
        scan_id=f"scan_{uuid.uuid4().hex[:12]}",
        agent_id=agent_id,
    )


def record_scan_event(
    root: Path,
    result: InjectionScanResult,
    *,
    deal_id: str | None = None,
    partner_id: str | None = None,
) -> dict[str, Any]:
    """Persist scan for learning loop analysis and pattern tuning."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_id": result.scan_id,
        "agent_id": result.agent_id,
        "deal_id": deal_id,
        "partner_id": partner_id,
        **result.to_dict(),
    }
    path = _scan_events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    if result.recommended_action in SCAN_BLOCK_ACTIONS:
        from arclya2a.security.security_analyzer import log_security_incident

        log_security_incident(
            root,
            "injection_scan_block",
            agent_id=result.agent_id,
            partner_id=partner_id,
            deal_id=deal_id,
            details={
                "observability_event_type": "injection_scan_rejection",
                "scan_id": result.scan_id,
                "recommended_action": result.recommended_action,
                "confidence": result.confidence,
                "patterns": [p.get("id") for p in result.detected_patterns[:5]],
                "detected_pattern_count": len(result.detected_patterns),
            },
        )
    return entry


def append_learned_pattern(
    root: Path,
    *,
    pattern_id: str,
    label: str,
    regex: str,
    severity: float = 0.7,
    category: str = "learned",
) -> dict[str, Any]:
    """Add a pattern for the learning system to apply on future scans."""
    path = _patterns_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"version": 1, "patterns": []}
    else:
        data = {"version": 1, "patterns": []}

    entry = {
        "id": pattern_id,
        "label": label,
        "regex": regex,
        "severity": severity,
        "category": category,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    patterns = data.setdefault("patterns", [])
    patterns = [p for p in patterns if p.get("id") != pattern_id]
    patterns.append(entry)
    data["patterns"] = patterns
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return entry


_ONBOARDING_OUTPUT_FIELDS = ("product_profile",)
_CLOSER_OUTPUT_FIELDS = ("partner_agreement_summary",)


def scan_agent_output(
    root: Path,
    *,
    agent_id: str,
    payload: dict[str, Any],
) -> InjectionScanResult:
    """Post-LLM scan of agent-produced text fields (excludes structural booleans)."""
    segments: list[tuple[str, str]] = []
    if agent_id == "onboarding_specialist":
        for key in _ONBOARDING_OUTPUT_FIELDS:
            if key in payload:
                segments.extend(_flatten_text(payload[key], prefix=key))
    elif agent_id == "closer":
        for key in _CLOSER_OUTPUT_FIELDS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                segments.append((key, val))
    else:
        segments.extend(_flatten_text(payload, prefix="payload"))

    all_detected: list[dict[str, Any]] = []
    sources: list[str] = []
    for source, text in segments:
        hits = scan_text(text, root=root, source=source)
        if hits:
            sources.append(source)
            all_detected.extend(hits)

    by_id: dict[str, dict[str, Any]] = {}
    for p in all_detected:
        existing = by_id.get(p["id"])
        if not existing or p["severity"] > existing["severity"]:
            by_id[p["id"]] = p
    patterns = list(by_id.values())
    confidence = _aggregate_confidence(patterns)

    return InjectionScanResult(
        is_suspicious=confidence >= 0.35,
        confidence=confidence,
        detected_patterns=patterns,
        recommended_action=_recommend_action(confidence, agent_id),
        content_sources=sources,
        scan_id=f"scan_{uuid.uuid4().hex[:12]}",
        agent_id=agent_id,
    )


def handoff_for_scan_rejection(
    agent_id: str,
    scan: InjectionScanResult,
) -> dict[str, Any]:
    """Build agent handoff when scan blocks LLM processing."""
    if agent_id == "closer":
        return {
            "status": "COMPLETE",
            "next_action": "halt_recruitment_margin_risk",
            "payload": {
                "deal_closed": False,
                "lead_routing_confirmed": False,
                "close_type": None,
                "partner_trust": "suspicious",
                "disqualification_reason": "prompt_injection",
                "partner_agreement_summary": (
                    f"Blocked: external content failed injection scan "
                    f"({', '.join(p['id'] for p in scan.detected_patterns[:3])})"
                ),
                "tool_reasoning": "Injection scan rejected input; no tools permitted.",
                "tool_requests": [],
                "security_scan": scan.to_dict(),
            },
            "validation": {
                "confidence": 95,
                "check": "Injection scanner blocked untrusted external content before negotiation",
            },
        }

    return {
        "status": "COMPLETE",
        "next_action": "continue_onboarding",
        "payload": {
            "onboarding_complete": False,
            "security_scan": scan.to_dict(),
            "validation_errors": [
                {
                    "field": "_security",
                    "message": "External content contains suspected prompt injection; sanitize and resubmit",
                }
            ],
        },
        "validation": {
            "confidence": 15,
            "check": "Injection scanner blocked untrusted profile content",
        },
    }