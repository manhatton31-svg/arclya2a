"""Notion connector — log deals and follow-ups as pages."""

from __future__ import annotations

from typing import Any

import httpx

from arclya2a.connectors.base import BaseConnector, ConnectorResult, dry_run_enabled, env_any
from arclya2a.connectors.http_helpers import classify_http_error
from arclya2a.tools.errors import INVALID_PARAMETERS


class NotionConnector(BaseConnector):
    name = "notion"

    def execute(
        self,
        *,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        tool_def: dict[str, Any],
    ) -> ConnectorResult:
        if action != "create_page":
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error=f"Unknown action: {action}",
            )

        title = params.get("title", "").strip()
        if not title:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error="Missing required parameter: title",
                error_code=INVALID_PARAMETERS,
            )

        content = params.get("content", "")
        if dry_run_enabled():
            return ConnectorResult(
                success=True,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                dry_run=True,
                data={
                    "page_id": "dry_run_page",
                    "url": "https://notion.so/dry-run",
                    "title": title,
                },
            )

        api_key = env_any("NOTION_API_KEY")
        if not api_key:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                skipped=True,
                error="NOTION_API_KEY not configured",
            )

        database_id = params.get("database_id") or env_any("NOTION_DATABASE_ID")
        if not database_id:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error="Missing database_id (param or NOTION_DATABASE_ID env)",
            )

        body: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": {
                "Name": {"title": [{"text": {"content": title}}]},
            },
        }
        if content:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}],
                    },
                }
            ]

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "https://api.notion.com/v1/pages",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Notion-Version": "2022-06-28",
                        "Content-Type": "application/json",
                    },
                    json=body,
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
            data={
                "page_id": payload.get("id"),
                "url": payload.get("url"),
                "title": title,
            },
        )