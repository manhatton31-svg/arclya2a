"""Sandbox / test mode for external test partners with security controls."""

from __future__ import annotations

import json
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from arclya2a.audit.logger import append_audit_record
from arclya2a.settings import get_settings

SANDBOX_KEY_PREFIX = "arclya_sandbox_"
TEST_MARKER = "[SANDBOX — dry-run tools, no production billing]"

_sandbox_active: ContextVar[bool] = ContextVar("arclya_sandbox_active", default=False)

# High-risk tools blocked in sandbox even during dry-run (external comms / data writes).
SANDBOX_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "gmail.send_followup_email",
    "calendar.create_event",
    "calendar.create_scheduling_link",
    "notion.create_deal_page",
})

# High-risk API paths sandbox keys cannot access.
SANDBOX_BLOCKED_PATH_PREFIXES: tuple[str, ...] = (
    "/learning/patches/",
    "/learning/run",
    "/billing/",
)

# Registration abuse limits (override via env).
def max_keys_per_agent_name() -> int:
    return get_settings().sandbox_max_keys_per_agent


def max_registrations_per_ip_per_day() -> int:
    return get_settings().sandbox_max_register_per_ip_day


AGENT_NAME_MIN_LEN = 2
AGENT_NAME_MAX_LEN = 128
AGENT_CARD_URL_MAX_LEN = 2048

SUSPICIOUS_FAILED_VALIDATION_THRESHOLD = 5
SUSPICIOUS_RATE_LIMIT_THRESHOLD = 3
MIN_GRADUATION_BEHAVIOR_SCORE = 70

_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
_AGENT_NAME_RE = re.compile(r"^[\w\s\-.']+$", re.UNICODE)


def set_sandbox_active(active: bool) -> None:
    _sandbox_active.set(active)


def is_sandbox_active() -> bool:
    return _sandbox_active.get()


def sandbox_rate_limit() -> int:
    """Stricter default than production (10/min vs 60/min)."""
    return get_settings().sandbox_rate_limit_per_minute


def sandbox_tools_dry_run_default() -> bool:
    return get_settings().sandbox_force_dry_run


def is_sandbox_tool_blocked(tool_id: str) -> bool:
    return tool_id.strip() in SANDBOX_BLOCKED_TOOLS


def is_sandbox_path_blocked(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SANDBOX_BLOCKED_PATH_PREFIXES)


def normalize_agent_name(agent_name: str) -> str:
    return " ".join(agent_name.strip().lower().split())


def validate_agent_name(agent_name: str) -> tuple[bool, str | None]:
    name = (agent_name or "").strip()
    if len(name) < AGENT_NAME_MIN_LEN:
        return False, f"agent_name must be at least {AGENT_NAME_MIN_LEN} characters"
    if len(name) > AGENT_NAME_MAX_LEN:
        return False, f"agent_name must be at most {AGENT_NAME_MAX_LEN} characters"
    if not _AGENT_NAME_RE.match(name):
        return False, "agent_name contains invalid characters"
    return True, None


def validate_agent_card_url(url: str | None) -> tuple[bool, str | None]:
    if not url or not str(url).strip():
        return True, None
    raw = str(url).strip()
    if len(raw) > AGENT_CARD_URL_MAX_LEN:
        return False, "agent_card_url exceeds maximum length"
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        return False, "agent_card_url must use HTTPS"
    if not parsed.netloc:
        return False, "agent_card_url must be a valid URL"
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.startswith("127."):
        return False, "agent_card_url must not point to localhost or private hosts"
    if parsed.username or parsed.password:
        return False, "agent_card_url must not include credentials"
    return True, None


def _keys_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "sandbox_keys.json"


def _security_events_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "security_events.jsonl"


def _registration_log_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "registration_log.jsonl"


def generate_sandbox_key() -> str:
    return f"{SANDBOX_KEY_PREFIX}{secrets.token_urlsafe(24)}"


def load_sandbox_keys(root: Path) -> dict[str, dict[str, Any]]:
    path = _keys_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_sandbox_keys(root: Path, keys: dict[str, dict[str, Any]]) -> None:
    path = _keys_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def count_active_keys_for_agent(root: Path, agent_name: str) -> int:
    normalized = normalize_agent_name(agent_name)
    count = 0
    for entry in load_sandbox_keys(root).values():
        if not entry.get("active", True):
            continue
        if normalize_agent_name(entry.get("agent_name", "")) == normalized:
            count += 1
    return count


def _count_registrations_today(root: Path, client_ip: str) -> int:
    path = _registration_log_path(root)
    if not path.exists():
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("client_ip") == client_ip and row.get("date") == today:
            count += 1
    return count


def check_registration_allowed(
    root: Path,
    *,
    agent_name: str,
    client_ip: str | None,
) -> tuple[bool, str | None]:
    """Abuse protection for POST /partners/sandbox/register."""
    ok, err = validate_agent_name(agent_name)
    if not ok:
        return False, err

    if count_active_keys_for_agent(root, agent_name) >= max_keys_per_agent_name():
        return False, (
            f"Maximum sandbox keys ({max_keys_per_agent_name()}) already issued for this agent_name"
        )

    if client_ip:
        if _count_registrations_today(root, client_ip) >= max_registrations_per_ip_per_day():
            return False, "Daily sandbox registration limit exceeded for this IP"

    return True, None


def log_registration_attempt(
    root: Path,
    *,
    agent_name: str,
    client_ip: str | None,
    success: bool,
    partner_id: str | None = None,
    reason: str | None = None,
) -> None:
    path = _registration_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "agent_name": agent_name,
        "client_ip": client_ip,
        "success": success,
        "partner_id": partner_id,
        "reason": reason,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def register_sandbox_key(
    root: Path,
    *,
    partner_id: str,
    agent_name: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create and persist a new sandbox API key."""
    key = generate_sandbox_key()
    keys = load_sandbox_keys(root)
    keys[key] = {
        "partner_id": partner_id,
        "agent_name": agent_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
        "metadata": metadata or {},
    }
    save_sandbox_keys(root, keys)
    return key


def lookup_sandbox_key(root: Path, provided_key: str) -> dict[str, Any] | None:
    if not provided_key.startswith(SANDBOX_KEY_PREFIX):
        return None
    entry = load_sandbox_keys(root).get(provided_key)
    if not entry or not entry.get("active", True):
        return None
    return {**entry, "sandbox_key_prefix": provided_key[:20] + "…"}


def apply_sandbox_markers(payload: dict[str, Any], *, sandbox: bool) -> dict[str, Any]:
    """Add visible test markers to API responses."""
    if not sandbox:
        return payload
    marked = dict(payload)
    marked["sandbox_mode"] = True
    marked["test_marker"] = TEST_MARKER
    marked["tools_mode"] = "dry_run" if sandbox_tools_dry_run_default() else "live"
    return marked


def log_sandbox_audit(
    root: Path,
    *,
    action: str,
    reasoning: str,
    partner_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write sandbox activity to the security audit log."""
    meta = dict(metadata or {})
    meta["category"] = "sandbox_security"
    if partner_id:
        meta["partner_id"] = partner_id
    return append_audit_record(
        root,
        agent_id="sandbox_security",
        action=action,
        reasoning=reasoning,
        metadata=meta,
    )


def record_sandbox_security_event(
    root: Path,
    event_type: str,
    *,
    partner_id: str | None = None,
    client_ip: str | None = None,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist security event and evaluate suspicious patterns."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "partner_id": partner_id,
        "client_ip": client_ip,
        "path": path,
        "details": details or {},
    }
    events_path = _security_events_path(root)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    log_sandbox_audit(
        root,
        action=f"sandbox_{event_type}",
        reasoning=f"Sandbox security event: {event_type}",
        partner_id=partner_id,
        metadata=event,
    )

    if partner_id:
        from arclya2a.partners.test_registry import apply_security_event

        apply_security_event(root, partner_id, event_type=event_type, details=details)

    from arclya2a.observability.security_events import EVENT_SANDBOX_SECURITY, record_security_event

    record_security_event(
        root,
        EVENT_SANDBOX_SECURITY,
        reason_code=event_type,
        partner_id=partner_id,
        sandbox_mode=True,
        details={
            "client_ip": client_ip,
            "path": path,
            **(details or {}),
        },
    )

    return event


def evaluate_suspicious_behavior(
    root: Path,
    partner_id: str,
    *,
    security: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return active suspicious flags for a partner."""
    sec = security or {}
    flags: list[str] = list(sec.get("suspicious_flags") or [])

    if int(sec.get("failed_validation_count", 0)) >= SUSPICIOUS_FAILED_VALIDATION_THRESHOLD:
        if "validation_abuse" not in flags:
            flags.append("validation_abuse")

    if int(sec.get("rate_limit_hits", 0)) >= SUSPICIOUS_RATE_LIMIT_THRESHOLD:
        if "rate_limit_abuse" not in flags:
            flags.append("rate_limit_abuse")

    if int(sec.get("blocked_action_count", 0)) >= 1:
        if "high_risk_probe" not in flags:
            flags.append("high_risk_probe")

    if int(sec.get("emergency_stop_count", 0)) >= 1:
        if "emergency_stop_history" not in flags:
            flags.append("emergency_stop_history")

    recent = _recent_partner_events(root, partner_id, limit=30)
    if len(recent) >= 25:
        window_start = recent[0].get("timestamp", "")
        window_end = recent[-1].get("timestamp", "")
        if window_start and window_end and window_start[:16] == window_end[:16]:
            if "burst_traffic" not in flags:
                flags.append("burst_traffic")

    return {"suspicious_flags": flags, "flag_count": len(flags)}


def _recent_partner_events(root: Path, partner_id: str, *, limit: int) -> list[dict[str, Any]]:
    path = _security_events_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("partner_id") == partner_id:
            rows.append(row)
    return rows[-limit:]


def compute_behavior_score(security: dict[str, Any]) -> int:
    """0–100 trust score; higher is better for graduation."""
    score = 100
    score -= int(security.get("emergency_stop_count", 0)) * 25
    failed = int(security.get("failed_validation_count", 0))
    score -= max(0, failed - 1) * 5
    score -= int(security.get("blocked_action_count", 0)) * 10
    score -= int(security.get("rate_limit_hits", 0)) * 5
    flags = security.get("suspicious_flags") or []
    score -= len(flags) * 8
    return max(0, min(100, score))