"""Operator workflow: promote graduation-ready test partners to production."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arclya2a.audit.logger import append_audit_record
from arclya2a.settings import get_settings
from arclya2a.partners.production_keys import issue_production_key
from arclya2a.partners.progress import build_partner_progress, collect_blocking_issues
from arclya2a.partners.sandbox import (
    SANDBOX_KEY_PREFIX,
    load_sandbox_keys,
    lookup_sandbox_key,
    save_sandbox_keys,
)
from arclya2a.partners.test_registry import GRADUATION_CRITERIA, get_test_partner, mark_partner_graduated

logger = logging.getLogger("arclya2a.partners.graduation")


class GraduationError(Exception):
    """Graduation blocked or failed."""

    def __init__(self, message: str, *, reasons: list[str] | None = None, code: str = "graduation_blocked"):
        super().__init__(message)
        self.reasons = reasons or [message]
        self.code = code


def graduation_log_path(root: Path) -> Path:
    return root / "data" / "test_partners" / "graduation_log.jsonl"


def list_graduation_events(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent partner graduations, newest first."""
    path = graduation_log_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    rows.reverse()
    return [
        {
            "partner_id": r.get("partner_id"),
            "agent_name": r.get("agent_name"),
            "graduated_by": r.get("graduated_by"),
            "timestamp": r.get("timestamp") or r.get("graduated_at"),
            "production_key_prefix": r.get("production_key_prefix"),
            "sandbox_keys_revoked": r.get("sandbox_keys_revoked", []),
        }
        for r in rows[:limit]
    ]


def resolve_partner_identifier(
    root: Path,
    *,
    partner_id: str | None = None,
    sandbox_key: str | None = None,
) -> str | None:
    """Resolve partner_id from explicit id or sandbox key."""
    if partner_id:
        return partner_id.strip() or None
    if sandbox_key:
        entry = lookup_sandbox_key(root, sandbox_key.strip())
        if entry:
            return entry.get("partner_id")
        if sandbox_key.strip().startswith(SANDBOX_KEY_PREFIX):
            for key, entry in load_sandbox_keys(root).items():
                if key == sandbox_key.strip():
                    return entry.get("partner_id")
    return None


def assess_graduation_readiness(root: Path, partner_id: str) -> dict[str, Any]:
    """Evaluate whether a partner can be graduated."""
    partner = get_test_partner(root, partner_id)
    if not partner:
        return {
            "partner_id": partner_id,
            "ready": False,
            "reasons": [f"Partner not found: {partner_id}"],
            "graduation_ready": False,
        }

    progress = build_partner_progress(root, partner_id) or {}
    reasons: list[str] = []
    if partner.get("status") == "graduated":
        reasons.append("Partner already graduated to production")
    if not progress.get("graduation_ready"):
        reasons.extend(collect_blocking_issues(progress))

    return {
        "partner_id": partner_id,
        "agent_name": progress.get("agent_name"),
        "status": partner.get("status"),
        "ready": partner.get("status") != "graduated" and progress.get("graduation_ready") is True,
        "graduation_ready": progress.get("graduation_ready", False),
        "milestones": progress.get("milestones", {}),
        "milestone_labels": {c["id"]: c["label"] for c in GRADUATION_CRITERIA},
        "reasons": reasons,
        "progress": progress,
    }


def revoke_sandbox_keys_for_partner(root: Path, partner_id: str) -> list[str]:
    """Deactivate all sandbox keys for a partner. Returns revoked key prefixes."""
    keys = load_sandbox_keys(root)
    revoked: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for key, entry in keys.items():
        if entry.get("partner_id") == partner_id and entry.get("active", True):
            entry["active"] = False
            entry["revoked_at"] = now
            entry["revoked_reason"] = "graduated_to_production"
            revoked.append(key[:20] + "…")
    if revoked:
        save_sandbox_keys(root, keys)
    return revoked


def _append_graduation_log(root: Path, event: dict[str, Any]) -> None:
    path = graduation_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def _send_graduation_notification(event: dict[str, Any]) -> dict[str, Any]:
    """Optional webhook notification; always logs locally."""
    webhook = get_settings().graduation_webhook_url or ""
    payload = {
        "event": "partner_graduated",
        "partner_id": event.get("partner_id"),
        "agent_name": event.get("agent_name"),
        "graduated_by": event.get("graduated_by"),
        "graduated_at": event.get("graduated_at"),
        "production_key_prefix": event.get("production_key_prefix"),
        "sandbox_keys_revoked": event.get("sandbox_keys_revoked", []),
    }
    logger.info(
        "partner_graduated partner_id=%s agent=%s by=%s",
        event.get("partner_id"),
        event.get("agent_name"),
        event.get("graduated_by"),
    )
    result: dict[str, Any] = {"logged": True, "webhook_sent": False}
    if not webhook:
        return result

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result["webhook_sent"] = 200 <= resp.status < 300
            result["webhook_status"] = resp.status
    except (urllib.error.URLError, TimeoutError) as exc:
        result["webhook_sent"] = False
        result["webhook_error"] = str(exc)
        logger.warning("graduation_webhook_failed partner_id=%s error=%s", event.get("partner_id"), exc)
    return result


def graduate_partner(
    root: Path,
    *,
    partner_id: str,
    graduated_by: str,
    notify: bool = True,
) -> dict[str, Any]:
    """
    Graduate a sandbox partner to production when graduation_ready is true.

    Issues a per-partner production key, revokes sandbox keys, and logs the event.
    """
    assessment = assess_graduation_readiness(root, partner_id)
    if not assessment["ready"]:
        raise GraduationError(
            f"Partner {partner_id} is not eligible for graduation",
            reasons=assessment["reasons"],
        )

    partner = get_test_partner(root, partner_id)
    if not partner:
        raise GraduationError(f"Partner not found: {partner_id}", code="not_found")

    production_key = issue_production_key(
        root,
        partner_id=partner_id,
        agent_name=partner.get("agent_name", "unknown"),
        graduated_by=graduated_by,
        metadata={"agent_card_url": partner.get("agent_card_url")},
    )
    revoked = revoke_sandbox_keys_for_partner(root, partner_id)
    key_prefix = production_key[:20] + "…"
    mark_partner_graduated(
        root,
        partner_id,
        graduated_by=graduated_by,
        production_key_prefix=key_prefix,
    )

    now = datetime.now(timezone.utc).isoformat()
    graduation_event = {
        "timestamp": now,
        "partner_id": partner_id,
        "agent_name": partner.get("agent_name"),
        "graduated_by": graduated_by,
        "graduated_at": now,
        "production_key_prefix": key_prefix,
        "sandbox_keys_revoked": revoked,
    }
    _append_graduation_log(root, graduation_event)

    audit = append_audit_record(
        root,
        agent_id="operator",
        action="partner_graduated",
        reasoning=f"Graduated {partner_id} to production",
        metadata={
            "category": "partner_graduation",
            "partner_id": partner_id,
            "agent_name": partner.get("agent_name"),
            "graduated_by": graduated_by,
            "production_key_prefix": key_prefix,
            "sandbox_keys_revoked_count": len(revoked),
            "sandbox_keys_revoked": revoked,
        },
    )

    notification = _send_graduation_notification(graduation_event) if notify else {"logged": False}

    from arclya2a.partners.sandbox import log_sandbox_audit, record_sandbox_security_event

    record_sandbox_security_event(
        root,
        "graduated",
        partner_id=partner_id,
        details={"graduated_by": graduated_by, "production_key_prefix": key_prefix},
    )
    log_sandbox_audit(
        root,
        action="sandbox_graduated",
        reasoning=f"Partner graduated to production by {graduated_by}",
        partner_id=partner_id,
        metadata=graduation_event,
    )

    return {
        "success": True,
        "partner_id": partner_id,
        "agent_name": partner.get("agent_name"),
        "production_key": production_key,
        "production_key_prefix": key_prefix,
        "sandbox_keys_revoked": revoked,
        "graduated_at": now,
        "graduated_by": graduated_by,
        "audit_id": audit["id"],
        "notification": notification,
    }