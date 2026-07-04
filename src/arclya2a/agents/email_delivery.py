"""Production email delivery for agent verification (SMTP + outbox fallback)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlparse

from arclya2a.settings import get_settings

logger = logging.getLogger(__name__)

VALID_DELIVERY_MODES = frozenset({"auto", "outbox", "smtp"})

# Documented SMTP URL patterns for common providers (credentials are secrets).
SMTP_PROVIDER_EXAMPLES: dict[str, dict[str, str]] = {
    "sendgrid": {
        "host": "smtp.sendgrid.net",
        "port": "587",
        "url": "smtp://apikey:YOUR_SENDGRID_API_KEY@smtp.sendgrid.net:587",
        "username": "apikey",
    },
    "resend": {
        "host": "smtp.resend.com",
        "port": "587",
        "url": "smtp://resend:YOUR_RESEND_API_KEY@smtp.resend.com:587",
        "username": "resend",
    },
    "mailgun": {
        "host": "smtp.mailgun.org",
        "port": "587",
        "url": "smtp://postmaster@YOUR_DOMAIN.mailgun.org:YOUR_MAILGUN_PASSWORD@smtp.mailgun.org:587",
        "username": "postmaster@YOUR_DOMAIN.mailgun.org",
    },
    "standard_smtp": {
        "host": "mail.yourdomain.com",
        "port": "587",
        "url": "smtp://user:password@mail.yourdomain.com:587",
        "username": "user",
    },
}


def normalize_delivery_mode(raw: str | None) -> str:
    mode = (raw or "auto").strip().lower()
    return mode if mode in VALID_DELIVERY_MODES else "auto"


def smtp_credentials_configured() -> bool:
    settings = get_settings()
    return bool(settings.agent_email_smtp_url and settings.agent_email_from)


def effective_email_delivery_mode() -> str:
    """
    Resolve how verification emails are delivered.

    - outbox: always log to outbox (dev, CI, tests)
    - smtp: require SMTP URL + from address
    - auto: use smtp when SMTP URL and from address are configured, else outbox
    """
    settings = get_settings()
    mode = normalize_delivery_mode(settings.agent_email_delivery)
    if mode == "outbox":
        return "outbox"
    if smtp_credentials_configured():
        return "smtp"
    return "outbox"


def detect_smtp_provider(smtp_url: str | None) -> str | None:
    """Identify common SMTP provider from URL host (no credentials exposed)."""
    if not smtp_url:
        return None
    try:
        host = (parse_smtp_url(smtp_url).get("host") or "").lower()
    except ValueError:
        return None
    if "resend.com" in host:
        return "resend"
    if "sendgrid.net" in host:
        return "sendgrid"
    if "mailgun.org" in host:
        return "mailgun"
    return "custom"


SMTP_ERROR_HINTS: dict[str, dict[str, str]] = {
    "auth_failed": {
        "message": "SMTP authentication failed — check your API key or username/password.",
        "next_step": (
            "Verify ARCLYA_AGENT_EMAIL_SMTP_URL credentials on Render. "
            "For Resend use smtp://resend:re_<key>@smtp.resend.com:587"
        ),
    },
    "connection_failed": {
        "message": "Could not connect to the SMTP server.",
        "next_step": (
            "Confirm the SMTP host and port in ARCLYA_AGENT_EMAIL_SMTP_URL. "
            "Retry POST /agents/me/resend-verification after the host redeploys."
        ),
    },
    "sender_rejected": {
        "message": "The SMTP server rejected the sender address.",
        "next_step": (
            "Verify ARCLYA_AGENT_EMAIL_FROM matches a domain verified in your email provider "
            "(Resend: add and verify your sending domain before launch)."
        ),
    },
    "recipient_rejected": {
        "message": "The SMTP server rejected the recipient address.",
        "next_step": "Confirm the email on your profile is valid, then resend verification.",
    },
    "tls_failed": {
        "message": "SMTP TLS handshake failed.",
        "next_step": (
            "Try port 587 with smtp:// or port 465 with smtps:// in ARCLYA_AGENT_EMAIL_SMTP_URL."
        ),
    },
    "timeout": {
        "message": "SMTP connection timed out.",
        "next_step": "Check network egress from your host and retry resend-verification.",
    },
    "unknown": {
        "message": "Verification email could not be delivered via SMTP.",
        "next_step": (
            "Retry POST /agents/me/resend-verification. "
            "Operators: GET /agents/operator/verification-outbox"
        ),
    },
}


def classify_smtp_error(error: str | None) -> dict[str, str]:
    """Map raw SMTP exception text to a stable error_code and user-facing hints."""
    text = (error or "").lower()
    if any(k in text for k in ("auth", "535", "534", "credentials", "login", "password")):
        code = "auth_failed"
    elif any(k in text for k in ("sender", "from address", "mail from", "550 5.7")):
        code = "sender_rejected"
    elif any(k in text for k in ("recipient", "rcpt to", "550 5.1")):
        code = "recipient_rejected"
    elif any(k in text for k in ("timed out", "timeout")):
        code = "timeout"
    elif any(k in text for k in ("tls", "ssl", "certificate", "starttls")):
        code = "tls_failed"
    elif any(k in text for k in ("connection refused", "connection reset", "errno", "network", "unreachable")):
        code = "connection_failed"
    else:
        code = "unknown"
    hint = SMTP_ERROR_HINTS.get(code, SMTP_ERROR_HINTS["unknown"])
    return {
        "error_code": code,
        "message": hint["message"],
        "next_step": hint["next_step"],
        "raw_error": error or "",
    }


def email_delivery_launch_blockers() -> list[str]:
    """Human-readable issues preventing production SMTP delivery."""
    settings = get_settings()
    mode = normalize_delivery_mode(settings.agent_email_delivery)
    issues: list[str] = []
    if mode == "outbox":
        issues.append("ARCLYA_AGENT_EMAIL_DELIVERY=outbox — emails are logged locally only")
    elif mode == "smtp" and not smtp_credentials_configured():
        issues.append(
            "ARCLYA_AGENT_EMAIL_DELIVERY=smtp but ARCLYA_AGENT_EMAIL_SMTP_URL or "
            "ARCLYA_AGENT_EMAIL_FROM is missing"
        )
    elif mode == "auto" and not smtp_credentials_configured():
        issues.append(
            "Set ARCLYA_AGENT_EMAIL_SMTP_URL and ARCLYA_AGENT_EMAIL_FROM on Render for live delivery"
        )
    if not settings.agent_email_from:
        issues.append("ARCLYA_AGENT_EMAIL_FROM is not set — use a verified sender (e.g. onboarding@yourdomain.com)")
    if not settings.agent_email_smtp_url:
        issues.append(
            "ARCLYA_AGENT_EMAIL_SMTP_URL is not set — "
            "Resend: smtp://resend:re_<key>@smtp.resend.com:587"
        )
    return issues


def parse_smtp_url(smtp_url: str) -> dict[str, Any]:
    """Parse smtp://user:pass@host:port or smtps://user:pass@host:port."""
    parsed = urlparse(smtp_url.strip())
    scheme = (parsed.scheme or "smtp").lower()
    if scheme not in {"smtp", "smtps"}:
        raise ValueError(f"Unsupported SMTP scheme '{scheme}' — use smtp:// or smtps://")

    host = parsed.hostname
    if not host:
        raise ValueError("SMTP URL must include a host")

    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    default_port = 465 if scheme == "smtps" else 587
    port = parsed.port or default_port

    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_ssl": scheme == "smtps",
    }


def send_smtp_message(
    *,
    to: str,
    subject: str,
    body: str,
    from_addr: str,
    smtp_url: str,
) -> None:
    """Send a plain-text email via SMTP. Raises on failure."""
    config = parse_smtp_url(smtp_url)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = to
    if isinstance(body, tuple):
        plain, html = body
        message.set_content(plain)
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(body)

    if config["use_ssl"]:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=12) as smtp:
            if config["username"]:
                smtp.login(config["username"], config["password"] or "")
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=12) as smtp:
        smtp.ehlo()
        try:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        except smtplib.SMTPException:
            logger.debug("SMTP STARTTLS unavailable for %s:%s", config["host"], config["port"])
        if config["username"]:
            smtp.login(config["username"], config["password"] or "")
        smtp.send_message(message)


def deliver_plaintext_email(
    *,
    to: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """
    Deliver email using configured mode.

    Returns {delivery, sent, error?}. SMTP mode raises no exception to caller —
    failures are returned in the result dict so registration can continue with outbox audit.
    """
    settings = get_settings()
    mode = effective_email_delivery_mode()
    configured = normalize_delivery_mode(get_settings().agent_email_delivery)
    if mode != "smtp":
        return {
            "delivery": "outbox",
            "sent": False,
            "smtp_attempted": False,
            "production_delivery": False,
        }

    from_addr = settings.agent_email_from
    smtp_url = settings.agent_email_smtp_url
    if not from_addr or not smtp_url:
        return {
            "delivery": "outbox",
            "sent": False,
            "smtp_attempted": False,
            "production_delivery": False,
            "error": "SMTP delivery selected but ARCLYA_AGENT_EMAIL_FROM or ARCLYA_AGENT_EMAIL_SMTP_URL is missing",
        }

    try:
        send_smtp_message(
            to=to,
            subject=subject,
            body=body,
            from_addr=from_addr,
            smtp_url=smtp_url,
        )
    except Exception as exc:
        logger.warning("SMTP verification email failed for %s: %s", to, exc)
        classified = classify_smtp_error(str(exc))
        return {
            "delivery": "outbox",
            "sent": False,
            "smtp_attempted": True,
            "production_delivery": False,
            "error": str(exc),
            "error_code": classified["error_code"],
            "error_message": classified["message"],
            "next_step": classified["next_step"],
        }

    return {
        "delivery": "smtp",
        "sent": True,
        "smtp_attempted": True,
        "production_delivery": True,
        "from": from_addr,
    }