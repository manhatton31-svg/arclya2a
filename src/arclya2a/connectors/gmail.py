"""Gmail connector — send follow-up emails via Gmail API."""

from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from typing import Any

import httpx

from arclya2a.connectors.base import BaseConnector, ConnectorResult, dry_run_enabled, env_any
from arclya2a.connectors.http_helpers import classify_http_error
from arclya2a.tools.errors import INVALID_PARAMETERS


class GmailConnector(BaseConnector):
    name = "gmail"

    def execute(
        self,
        *,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        tool_def: dict[str, Any],
    ) -> ConnectorResult:
        if action != "send_email":
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error=f"Unknown action: {action}",
            )

        to_addr = params.get("to", "").strip()
        subject = params.get("subject", "").strip()
        body = params.get("body", "").strip()
        if not to_addr or not subject or not body:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error="Missing required parameters: to, subject, body",
                error_code=INVALID_PARAMETERS,
            )

        if dry_run_enabled():
            return ConnectorResult(
                success=True,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                dry_run=True,
                data={"message_id": "dry_run_msg", "to": to_addr, "subject": subject},
            )

        sender = env_any("GMAIL_SENDER") or "me"
        token = env_any("GMAIL_ACCESS_TOKEN")
        if not token:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                skipped=True,
                error="GMAIL_ACCESS_TOKEN not configured",
            )

        message = MIMEText(body)
        message["to"] = to_addr
        message["from"] = sender if sender != "me" else ""
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"raw": raw},
                )
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            code, transient, message = classify_http_error(exc)
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error=message,
                error_code=code,
                transient=transient,
            )

        return ConnectorResult(
            success=True,
            tool_id=tool_id,
            connector=self.name,
            action=action,
            data={"message_id": payload.get("id"), "to": to_addr, "subject": subject},
        )