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


def effective_email_delivery_mode() -> str:
    """
    Resolve how verification emails are delivered.

    - outbox: always log to outbox (dev, CI, tests)
    - smtp: require SMTP URL + from address; fall back to outbox on misconfiguration
    - auto: use smtp when SMTP URL and from address are configured, else outbox
    """
    settings = get_settings()
    mode = normalize_delivery_mode(settings.agent_email_delivery)
    if mode == "outbox":
        return "outbox"
    if settings.agent_email_smtp_url and settings.agent_email_from:
        return "smtp"
    return "outbox"


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
        with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=30) as smtp:
            if config["username"]:
                smtp.login(config["username"], config["password"] or "")
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
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
    if mode != "smtp":
        return {"delivery": "outbox", "sent": False, "smtp_attempted": False}

    from_addr = settings.agent_email_from
    smtp_url = settings.agent_email_smtp_url
    if not from_addr or not smtp_url:
        return {
            "delivery": "outbox",
            "sent": False,
            "smtp_attempted": False,
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
        return {
            "delivery": "outbox",
            "sent": False,
            "smtp_attempted": True,
            "error": str(exc),
        }

    return {"delivery": "smtp", "sent": True, "smtp_attempted": True, "from": from_addr}