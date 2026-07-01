"""Google Calendar connector — events and scheduling links."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from arclya2a.connectors.base import BaseConnector, ConnectorResult, dry_run_enabled, env_any
from arclya2a.connectors.http_helpers import classify_http_error
from arclya2a.tools.errors import INVALID_PARAMETERS


def _parse_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _default_end(start: datetime, minutes: int = 30) -> datetime:
    return start + timedelta(minutes=minutes)


class GoogleCalendarConnector(BaseConnector):
    name = "google_calendar"

    def execute(
        self,
        *,
        tool_id: str,
        action: str,
        params: dict[str, Any],
        tool_def: dict[str, Any],
    ) -> ConnectorResult:
        if action not in ("create_event", "create_scheduling_link"):
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error=f"Unknown action: {action}",
            )

        title = params.get("title", "").strip()
        start_raw = params.get("start_time", "").strip()
        if not title or not start_raw:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                error="Missing required parameters: title, start_time",
                error_code=INVALID_PARAMETERS,
            )

        calendar_id = env_any("GOOGLE_CALENDAR_ID") or "primary"
        start_dt = _parse_iso(start_raw)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        duration = int(params.get("duration_minutes", 30))
        end_raw = params.get("end_time", "").strip()
        end_dt = _parse_iso(end_raw) if end_raw else _default_end(start_dt, duration)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        attendees = params.get("attendees") or []
        event_body: dict[str, Any] = {
            "summary": title,
            "description": params.get("description", ""),
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        if attendees:
            event_body["attendees"] = [{"email": a} for a in attendees]
        if action == "create_scheduling_link":
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": f"arclya-{int(start_dt.timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        if dry_run_enabled():
            return ConnectorResult(
                success=True,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                dry_run=True,
                data={
                    "event_id": "dry_run_event",
                    "html_link": "https://calendar.google.com/calendar/event?eid=dry_run",
                    "meet_link": "https://meet.google.com/dry-run",
                },
            )

        token = env_any("GOOGLE_CALENDAR_ACCESS_TOKEN")
        if not token:
            return ConnectorResult(
                success=False,
                tool_id=tool_id,
                connector=self.name,
                action=action,
                skipped=True,
                error="GOOGLE_CALENDAR_ACCESS_TOKEN not configured",
            )

        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        query = {"conferenceDataVersion": "1"} if action == "create_scheduling_link" else {}

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=query,
                    json=event_body,
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

        meet_link = ""
        conf = payload.get("conferenceData", {})
        for entry in conf.get("entryPoints", []):
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "")
                break

        return ConnectorResult(
            success=True,
            tool_id=tool_id,
            connector=self.name,
            action=action,
            data={
                "event_id": payload.get("id"),
                "html_link": payload.get("htmlLink"),
                "meet_link": meet_link,
            },
        )