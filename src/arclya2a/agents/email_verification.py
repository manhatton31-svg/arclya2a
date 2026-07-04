"""Token-based email verification for external agent accounts."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from arclya2a.agents.accounts import get_agent_account, normalize_email
from arclya2a.agents.email_delivery import (
    classify_smtp_error,
    deliver_plaintext_email,
    detect_smtp_provider,
    effective_email_delivery_mode,
    email_delivery_launch_blockers,
    normalize_delivery_mode,
)
from arclya2a.settings import get_settings

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "ev_"
OUTBOX_FILENAME = "verification_outbox.jsonl"
TOKENS_FILENAME = "email_verification_tokens.jsonl"


def _tokens_path(root: Path) -> Path:
    return root / "data" / "agent_accounts" / TOKENS_FILENAME


def _outbox_path(root: Path) -> Path:
    return root / "data" / "agent_accounts" / OUTBOX_FILENAME


def _ensure_dir(root: Path) -> None:
    (root / "data" / "agent_accounts").mkdir(parents=True, exist_ok=True)


def directory_requires_email_verification() -> bool:
    return get_settings().agent_require_email_verification_for_directory


def verification_token_hours() -> int:
    return get_settings().agent_email_verification_token_hours


VERIFICATION_ERROR_HINTS: dict[str, dict[str, str]] = {
    "invalid_token": {
        "message": "This verification link is not valid.",
        "next_step": "Request a new email via POST /agents/me/resend-verification",
    },
    "token_not_found": {
        "message": "This verification link was not recognized.",
        "next_step": "Request a new email via POST /agents/me/resend-verification",
    },
    "token_already_used": {
        "message": "This verification link was already used.",
        "next_step": "If you are not verified yet, POST /agents/me/resend-verification",
    },
    "token_revoked": {
        "message": "This verification link has been replaced by a newer email.",
        "next_step": "Use the latest email or POST /agents/me/resend-verification",
    },
    "token_expired": {
        "message": "This verification link has expired.",
        "next_step": "POST /agents/me/resend-verification to receive a fresh link",
    },
    "email_mismatch": {
        "message": "Your account email changed since this link was issued.",
        "next_step": "POST /agents/me/resend-verification after confirming PATCH /agents/me email",
    },
    "agent_not_found": {
        "message": "The agent account for this link could not be found.",
        "next_step": "Register again or contact the platform operator",
    },
}


def classify_verification_error(message: str | None) -> str:
    """Map verification error text to a stable error code."""
    text = (message or "").lower()
    if "invalid verification token" in text:
        return "invalid_token"
    if "not found" in text and "token" in text:
        return "token_not_found"
    if "already used" in text:
        return "token_already_used"
    if "replaced" in text or "revoked" in text:
        return "token_revoked"
    if "expired" in text:
        return "token_expired"
    if "no longer matches" in text:
        return "email_mismatch"
    if "agent account not found" in text:
        return "agent_not_found"
    return "verification_failed"


def build_email_verification_status(account: dict[str, Any]) -> dict[str, Any]:
    """Structured email verification state for API responses and onboarding."""
    has_email = bool(normalize_email(account.get("email")))
    verified = bool(account.get("email_verified"))
    required = directory_requires_email_verification()
    directory_ready = (not required) or (has_email and verified)
    delivery_mode = effective_email_delivery_mode()
    configured_mode = normalize_delivery_mode(get_settings().agent_email_delivery)

    if verified:
        verification_state = "verified"
        pending_reason = None
        next_step = "Email verified — you may opt in to the Agent Directory"
    elif not has_email:
        verification_state = "no_email"
        pending_reason = "no_email_on_account"
        next_step = "Add an email via PATCH /agents/me to receive a verification link"
    else:
        verification_state = "pending"
        pending_reason = "awaiting_verification"
        if delivery_mode == "smtp":
            next_step = (
                "Check your inbox (and spam folder) for the verification link. "
                "Resend via POST /agents/me/resend-verification if needed."
            )
        elif configured_mode == "outbox":
            next_step = (
                "Verification is pending. On this deployment emails are logged to the operator "
                "outbox — contact the platform operator or use POST /agents/me/resend-verification."
            )
        else:
            next_step = (
                "Email delivery is not configured on this host yet. "
                "Ask the operator to set ARCLYA_AGENT_EMAIL_SMTP_URL and ARCLYA_AGENT_EMAIL_FROM, "
                "or retry after configuration."
            )

    return {
        "has_email": has_email,
        "email_verified": verified,
        "verification_state": verification_state,
        "pending_reason": pending_reason,
        "required_for_directory": required,
        "directory_ready": directory_ready,
        "delivery_mode": delivery_mode,
        "delivery_mode_configured": configured_mode,
        "verify_endpoint": "POST /agents/verify-email",
        "verify_link_endpoint": "GET /agents/verify-email?token=ev_<token>",
        "resend_endpoint": "POST /agents/me/resend-verification",
        "next_step": next_step,
        "troubleshooting": {
            "resend": "POST /agents/me/resend-verification",
            "link_expiry_hours": verification_token_hours(),
            "smtp_required_for_live_delivery": configured_mode != "outbox",
            "check_spam_folder": delivery_mode == "smtp",
            "smtp_provider": detect_smtp_provider(get_settings().agent_email_smtp_url),
        },
    }


def _load_tokens(root: Path) -> list[dict[str, Any]]:
    path = _tokens_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_tokens(root: Path, rows: list[dict[str, Any]]) -> None:
    _ensure_dir(root)
    with open(_tokens_path(root), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _append_outbox(root: Path, entry: dict[str, Any]) -> None:
    _ensure_dir(root)
    with open(_outbox_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def build_verification_link(base_url: str, token: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/agents/verify-email?token={quote(token)}"


def build_verification_email_content(
    *,
    agent_name: str,
    verify_link: str,
    token: str,
    base_url: str,
    hours: int,
) -> tuple[str, str]:
    """Plain-text and HTML bodies for verification email."""
    base = base_url.rstrip("/")
    plain = (
        f"Hello {agent_name},\n\n"
        f"Verify your email to join the Arclya Agent Directory.\n\n"
        f"Verification link (valid {hours} hours):\n"
        f"{verify_link}\n\n"
        f"Or POST the token to {base}/agents/verify-email\n"
        f'{{"token": "{token}"}}\n\n'
        f"If you did not register, ignore this message."
    )
    html = (
        f"<p>Hello {agent_name},</p>"
        f"<p>Verify your email to join the <strong>Arclya Agent Directory</strong>.</p>"
        f'<p><a href="{verify_link}">Verify email</a> '
        f"(link valid {hours} hours)</p>"
        f'<p style="word-break:break-all;font-family:monospace;font-size:14px;">'
        f"{verify_link}</p>"
        f"<p>Or POST the token to <code>{base}/agents/verify-email</code></p>"
        f"<p>If you did not register, ignore this message.</p>"
    )
    return plain, html


def issue_verification_token(
    root: Path,
    *,
    agent_id: str,
    email: str,
) -> dict[str, Any]:
    """Create a new verification token for an agent email (invalidates prior unused tokens)."""
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("email is required to issue a verification token")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=verification_token_hours())
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    rows = _load_tokens(root)

    for row in rows:
        if row.get("agent_id") == agent_id and not row.get("used_at"):
            row["revoked_at"] = now.isoformat()

    record = {
        "token": token,
        "agent_id": agent_id,
        "email": normalized,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "used_at": None,
        "revoked_at": None,
    }
    rows.append(record)
    _write_tokens(root, rows)
    return record


def send_verification_email(
    root: Path,
    *,
    account: dict[str, Any],
    token: str,
    base_url: str,
) -> dict[str, Any]:
    """Deliver verification email via SMTP in production; outbox fallback for dev/CI."""
    email = normalize_email(account.get("email"))
    if not email:
        return {"sent": False, "reason": "no_email_on_account"}

    agent_name = account.get("agent_name", "your agent")
    verify_link = build_verification_link(base_url, token)
    hours = verification_token_hours()
    subject = "Verify your Arclya agent email"
    plain_body, html_body = build_verification_email_content(
        agent_name=agent_name,
        verify_link=verify_link,
        token=token,
        base_url=base_url,
        hours=hours,
    )
    body: str | tuple[str, str] = (plain_body, html_body)

    delivery_mode = effective_email_delivery_mode()
    outbox_entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": account.get("agent_id"),
        "email": email,
        "subject": subject,
        "body": plain_body,
        "token": token,
        "verify_link": verify_link,
        "delivery": "outbox",
        "delivery_mode": delivery_mode,
        "public_base_url": base_url.rstrip("/"),
    }

    if delivery_mode == "smtp":
        smtp_result = deliver_plaintext_email(to=email, subject=subject, body=body)
    else:
        smtp_result = {
            "delivery": "outbox",
            "sent": delivery_mode == "outbox",
            "smtp_attempted": False,
            "production_delivery": False,
        }

    outbox_entry["delivery"] = smtp_result.get("delivery", "outbox")
    outbox_entry["smtp_attempted"] = smtp_result.get("smtp_attempted", False)
    if smtp_result.get("error"):
        outbox_entry["smtp_error"] = smtp_result["error"]
    if smtp_result.get("from"):
        outbox_entry["smtp_from"] = smtp_result["from"]

    _append_outbox(root, outbox_entry)

    configured_mode = normalize_delivery_mode(get_settings().agent_email_delivery)
    sent = bool(smtp_result.get("sent"))
    production_delivery = bool(smtp_result.get("production_delivery"))
    result: dict[str, Any] = {
        "sent": sent,
        "email": email,
        "expires_in_hours": verification_token_hours(),
        "delivery": outbox_entry["delivery"],
        "delivery_mode": delivery_mode,
        "delivery_mode_configured": configured_mode,
        "production_delivery": production_delivery,
    }
    if configured_mode == "outbox":
        result["message"] = (
            "Verification logged to outbox (dev/CI). In production on Render, set "
            "ARCLYA_AGENT_EMAIL_DELIVERY=auto with ARCLYA_AGENT_EMAIL_SMTP_URL and "
            "ARCLYA_AGENT_EMAIL_FROM."
        )
        result["verify_link"] = verify_link
    elif sent and production_delivery:
        result["message"] = (
            f"Verification email sent to {email} — check your inbox and spam folder"
        )
    elif delivery_mode != "smtp":
        blockers = email_delivery_launch_blockers()
        result["message"] = (
            "Verification email was not sent — SMTP is not configured on this host. "
            + (blockers[0] if blockers else "Set email environment variables on Render.")
        )
        result["delivery_blockers"] = blockers
        result["operator_hint"] = "GET /agents/operator/verification-outbox (operator key)"
    else:
        classified = classify_smtp_error(smtp_result.get("error"))
        result["message"] = classified["message"]
        result["error_code"] = smtp_result.get("error_code") or classified["error_code"]
        result["next_step"] = smtp_result.get("next_step") or classified["next_step"]
        result["smtp_error"] = smtp_result.get("error")
        result["operator_hint"] = "GET /agents/operator/verification-outbox (operator key)"

    if configured_mode == "outbox" or (not sent and not production_delivery):
        result["verify_link"] = verify_link

    return result


def verification_email_uses_background_delivery() -> bool:
    """SMTP sends are queued after the HTTP response to avoid gateway timeouts."""
    return effective_email_delivery_mode() == "smtp"


def queue_agent_email_verification(
    root: Path,
    *,
    account: dict[str, Any],
    base_url: str,
    deliver_smtp_in_background: bool = False,
) -> dict[str, Any] | None:
    """Issue token and send (or queue) verification email for an unverified address."""
    email = normalize_email(account.get("email"))
    if not email or account.get("email_verified"):
        return None
    token_record = issue_verification_token(root, agent_id=account["agent_id"], email=email)
    configured_mode = normalize_delivery_mode(get_settings().agent_email_delivery)
    delivery_mode = effective_email_delivery_mode()

    if deliver_smtp_in_background and delivery_mode == "smtp":
        return {
            "token_id": token_record["token"][:12] + "…",
            "expires_at": token_record["expires_at"],
            "_token": token_record["token"],
            "email": email,
            "sent": False,
            "queued": True,
            "delivery": "smtp",
            "delivery_mode": delivery_mode,
            "delivery_mode_configured": configured_mode,
            "production_delivery": None,
            "message": (
                f"Verification email is being sent to {email} — check your inbox and spam folder shortly"
            ),
            "resend_endpoint": "POST /agents/me/resend-verification",
            "operator_hint": "GET /agents/operator/verification-outbox (operator key)",
            "status": build_email_verification_status(account),
        }

    delivery = send_verification_email(
        root,
        account=account,
        token=token_record["token"],
        base_url=base_url,
    )
    return {
        "token_id": token_record["token"][:12] + "…",
        "expires_at": token_record["expires_at"],
        **delivery,
        "status": build_email_verification_status(account),
    }


def run_background_verification_email(
    root: Path,
    *,
    account: dict[str, Any],
    token: str,
    base_url: str,
) -> None:
    """Background task: deliver verification email without blocking the HTTP response."""
    try:
        send_verification_email(root, account=account, token=token, base_url=base_url)
    except Exception:
        logger.exception(
            "Background verification email failed for agent %s",
            account.get("agent_id"),
        )


def verify_email_token(
    root: Path,
    token: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Mark agent email verified when token is valid."""
    from arclya2a.agents.accounts import _load_all, _write_all

    raw = (token or "").strip()
    if not raw.startswith(TOKEN_PREFIX):
        return None, "Invalid verification token"

    now = datetime.now(timezone.utc)
    rows = _load_tokens(root)
    match: dict[str, Any] | None = None
    for row in rows:
        if row.get("token") == raw:
            match = row
            break

    if not match:
        return None, "Verification token not found"
    if match.get("used_at"):
        return None, "Verification token already used"
    if match.get("revoked_at"):
        return None, "Verification token has been replaced — request a new one"
    expires = datetime.fromisoformat(str(match["expires_at"]).replace("Z", "+00:00"))
    if now > expires:
        return None, "Verification token expired — request a new verification email"

    agent_id = match.get("agent_id")
    expected_email = normalize_email(match.get("email"))
    accounts = _load_all(root)
    updated: dict[str, Any] | None = None
    for account in accounts:
        if account.get("agent_id") != agent_id:
            continue
        current_email = normalize_email(account.get("email"))
        if current_email != expected_email:
            return None, "Email on account no longer matches this verification token"
        account["email_verified"] = True
        account["email_verified_at"] = now.isoformat()
        account["updated_at"] = now.isoformat()
        updated = account
        break

    if not updated:
        return None, "Agent account not found"

    match["used_at"] = now.isoformat()
    _write_tokens(root, rows)
    _write_all(root, accounts)
    return updated, None


def read_outbox_entries(root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    path = _outbox_path(root)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:]


def latest_outbox_token(root: Path, *, agent_id: str | None = None) -> str | None:
    """Test helper: latest verification token from outbox."""
    entry = latest_outbox_entry(root, agent_id=agent_id)
    if not entry:
        return None
    token = entry.get("token")
    return str(token) if token else None


def latest_outbox_entry(root: Path, *, agent_id: str | None = None) -> dict[str, Any] | None:
    """Latest verification outbox entry, optionally filtered by agent_id."""
    for entry in reversed(read_outbox_entries(root, limit=200)):
        if agent_id and entry.get("agent_id") != agent_id:
            continue
        return entry
    return None


def list_pending_email_verifications(root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Agents with email on file that are not yet verified."""
    from arclya2a.agents.accounts import _load_all

    pending: list[dict[str, Any]] = []
    for account in _load_all(root):
        email = normalize_email(account.get("email"))
        if not email or account.get("email_verified"):
            continue
        latest = latest_outbox_entry(root, agent_id=account.get("agent_id"))
        pending.append(
            {
                "agent_id": account.get("agent_id"),
                "agent_name": account.get("agent_name"),
                "email": email,
                "status": account.get("status"),
                "created_at": account.get("created_at"),
                "updated_at": account.get("updated_at"),
                "latest_delivery": latest.get("delivery") if latest else None,
                "latest_smtp_error": latest.get("smtp_error") if latest else None,
                "latest_sent_at": latest.get("timestamp") if latest else None,
            }
        )
    pending.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
    return pending[:limit]


def operator_verification_outbox_summary(
    root: Path,
    *,
    agent_id: str | None = None,
    limit: int = 5,
    include_pending: bool = True,
) -> dict[str, Any]:
    """Operator view of recent verification deliveries (launch testing / support)."""
    entries = read_outbox_entries(root, limit=max(limit, 50))
    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]
    display_entries = entries[-limit:] if entries else []
    latest = entries[-1] if entries else None

    delivery_stats = {"smtp": 0, "outbox": 0, "smtp_failed": 0}
    for entry in entries:
        delivery = entry.get("delivery")
        if delivery == "smtp":
            delivery_stats["smtp"] += 1
        else:
            delivery_stats["outbox"] += 1
        if entry.get("smtp_error"):
            delivery_stats["smtp_failed"] += 1

    pending = list_pending_email_verifications(root, limit=25) if include_pending else []
    if agent_id:
        pending = [row for row in pending if row.get("agent_id") == agent_id]

    return {
        "count": len(display_entries),
        "latest": latest,
        "entries": display_entries,
        "delivery_stats": delivery_stats,
        "pending_verifications": pending,
        "pending_count": len(pending),
        "delivery_mode_effective": effective_email_delivery_mode(),
        "delivery_blockers": email_delivery_launch_blockers(),
    }