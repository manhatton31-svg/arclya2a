"""Shared helpers for external agent tests."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from arclya2a.agents.email_verification import latest_outbox_token


def unique_agent_name(prefix: str = "Agent") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def registration_payload(**fields) -> dict:
    """Build a valid POST /agents/register body with required terms acceptance."""
    payload = {"terms_accepted": True}
    payload.update(fields)
    return payload


def verify_agent_from_outbox(
    client: TestClient,
    root: Path,
    *,
    agent_id: str | None = None,
) -> None:
    token = latest_outbox_token(root, agent_id=agent_id)
    assert token, "expected verification token in outbox"
    resp = client.post("/agents/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text


def register_verify_and_list(
    client: TestClient,
    root: Path,
    *,
    name: str | None = None,
    email: str | None = None,
    description: str = "",
    capabilities: list[str] | None = None,
) -> tuple[str, str]:
    """Register with email, verify it, and opt into the directory."""
    agent_name = name or unique_agent_name()
    addr = email or f"agent_{uuid.uuid4().hex[:10]}@example.com"
    payload = registration_payload(agent_name=agent_name, email=addr)
    if description:
        payload["description"] = description
    if capabilities:
        payload["capabilities"] = capabilities

    reg = client.post("/agents/register", json=payload)
    assert reg.status_code == 200, reg.text
    data = reg.json()
    api_key = data["api_key"]
    agent_id = data["agent_id"]

    verify_agent_from_outbox(client, root, agent_id=agent_id)

    listed = client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    assert listed.status_code == 200, listed.text
    return agent_id, api_key