"""Linear connector — create follow-up issues."""

from __future__ import annotations

from typing import Any

import httpx

from arclya2a.connectors.base import BaseConnector, ConnectorResult, dry_run_enabled, env_any
from arclya2a.connectors.http_helpers import classify_http_error
from arclya2a.tools.errors import CONNECTOR_ERROR, INVALID_PARAMETERS


CREATE_ISSUE_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      identifier
      url
      title
    }
  }
}
"""


class LinearConnector(BaseConnector):
    name = "linear"

    def execute(
        self,
        *,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        tool_def: dict[str, Any],
    ) -> ConnectorResult:
        if action != "create_issue":
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

        if dry_run_enabled():
            return ConnectorResult(
                success=True,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                dry_run=True,
                data={
                    "issue_id": "dry_run_issue",
                    "identifier": "ARC-DRY",
                    "url": "https://linear.app/dry-run/issue/ARC-DRY",
                    "title": title,
                },
            )

        api_key = env_any("LINEAR_API_KEY")
        if not api_key:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                skipped=True,
                error="LINEAR_API_KEY not configured",
            )

        team_id = params.get("team_id") or env_any("LINEAR_TEAM_ID")
        if not team_id:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error="Missing team_id (param or LINEAR_TEAM_ID env)",
            )

        issue_input: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": params.get("description", ""),
        }
        if params.get("priority"):
            issue_input["priority"] = int(params["priority"])

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "https://api.linear.app/graphql",
                    headers={
                        "Authorization": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": CREATE_ISSUE_MUTATION,
                        "variables": {"input": issue_input},
                    },
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

        if payload.get("errors"):
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error=str(payload["errors"]),
                error_code=CONNECTOR_ERROR,
                transient=False,
            )

        issue_data = payload.get("data", {}).get("issueCreate", {})
        issue = issue_data.get("issue") or {}
        return ConnectorResult(
            success=bool(issue_data.get("success")),
            tool_id=tool_id,
            connector=self.name,
            action=action,
            data={
                "issue_id": issue.get("id"),
                "identifier": issue.get("identifier"),
                "url": issue.get("url"),
                "title": issue.get("title"),
            },
        )