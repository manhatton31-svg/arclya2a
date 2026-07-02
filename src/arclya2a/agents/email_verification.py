"""Token-based email verification for external agent accounts."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from arclya2a.agents.accounts import get_agent_account, normalize_email
from arclya2a.agents.email_delivery import deliver_plaintext_email, effective_email_delivery_mode
from arclya2a.settings import get_settings

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


def build_email_verification_status(account: dict[str, Any]) -> dict[str, Any]:
    """Structured email verification state for API responses and onboarding."""
    has_email = bool(normalize_email(account.get("email")))
    verified = bool(account.get("email_verified"))
    required = directory_requires_email_verification()
    directory_ready = (not required) or (has_email and verified)

    if not has_email:
        next_step = "Add an email via PATCH /agents/me to receive a verification link"
    elif verified:
        next_step = "Email verified — you may opt in to the Agent Directory"
    else:
        next_step = (
            "Check your inbox for the verification link, or POST /agents/me/resend-verification"
        )

    return {
        "has_email": has_email,
        "email_verified": verified,
        "required_for_directory": required,
        "directory_ready": directory_ready,
        "delivery_mode": effective_email_delivery_mode(),
        "verify_endpoint": "POST /agents/verify-email",
        "verify_link_endpoint": "GET /agents/verify-email?token=ev_<token>",
        "resend_endpoint": "POST /agents/me/resend-verification",
        "next_step": next_step,
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
    subject = "Verify your Arclya agent email"
    body = (
        f"Hello {agent_name},\n\n"
        f"Verify your email to join the Arclya Agent Directory.\n\n"
        f"Verification link (valid {verification_token_hours()} hours):\n"
        f"{verify_link}\n\n"
        f"Or POST the token to {base_url.rstrip('/')}/agents/verify-email\n"
        f'Body: {{"token": "{token}"}}\n\n'
        f"If you did not register, ignore this message."
    )

    delivery_mode = effective_email_delivery_mode()
    outbox_entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": account.get("agent_id"),
        "email": email,
        "subject": subject,
        "body": body,
        "token": token,
        "verify_link": verify_link,
        "delivery": "outbox",
        "delivery_mode": delivery_mode,
        "public_base_url": base_url.rstrip("/"),
    }

    smtp_result: dict[str, Any] = {"delivery": "outbox", "sent": False, "smtp_attempted": False}
    if delivery_mode == "smtp":
        smtp_result = deliver_plaintext_email(to=email, subject=subject, body=body)
        outbox_entry["delivery"] = smtp_result.get("delivery", "outbox")
        outbox_entry["smtp_attempted"] = smtp_result.get("smtp_attempted", False)
        if smtp_result.get("error"):
            outbox_entry["smtp_error"] = smtp_result["error"]
        if smtp_result.get("from"):
            outbox_entry["smtp_from"] = smtp_result["from"]

    _append_outbox(root, outbox_entry)

    sent = bool(smtp_result.get("sent")) if delivery_mode == "smtp" else True
    result: dict[str, Any] = {
        "sent": sent,
        "email": email,
        "expires_in_hours": verification_token_hours(),
        "delivery": outbox_entry["delivery"],
        "delivery_mode": delivery_mode,
    }
    if delivery_mode == "outbox":
        result["message"] = (
            "Verification logged to outbox (dev/CI). In production, configure "
            "ARCLYA_AGENT_EMAIL_SMTP_URL and ARCLYA_AGENT_EMAIL_FROM."
        )
    elif sent:
        result["message"] = "Verification email sent — check your inbox"
    else:
        result["message"] = (
            "Verification email could not be sent via SMTP. "
            "Check server logs and retry POST /agents/me/resend-verification."
        )
        result["smtp_error"] = smtp_result.get("error")

    if delivery_mode == "outbox":
        result["verify_link"] = verify_link

    return result


def queue_agent_email_verification(
    root: Path,
    *,
    account: dict[str, Any],
    base_url: str,
) -> dict[str, Any] | None:
    """Issue token and send verification email when account has an unverified email."""
    email = normalize_email(account.get("email"))
    if not email or account.get("email_verified"):
        return None
    token_record = issue_verification_token(root, agent_id=account["agent_id"], email=email)
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
    for entry in reversed(read_outbox_entries(root, limit=200)):
        if agent_id and entry.get("agent_id") != agent_id:
            continue
        token = entry.get("token")
        if token:
            return str(token)
    return None