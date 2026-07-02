"""Tests for Agent Directory discovery, search, and recommendations."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from arclya2a.agents.accounts import (
    compute_capability_match_score,
    compute_search_relevance,
    list_directory_agents,
)
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list, verify_agent_from_outbox


def _unique_name() -> str:
    return f"Disc_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "pricing").mkdir()

    (tmp_path / "config" / "core.json").write_text(
        json.dumps(
            {
                "platform_name": "Arclya A2A",
                "version": "0.1.0",
                "server": {"host": "127.0.0.1", "port": 8787, "base_url": "http://127.0.0.1:8787"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "agents" / "registry.json").write_text(
        json.dumps({"version": "1.0.0", "agents": []}),
        encoding="utf-8",
    )
    return tmp_path


def _register_listed(client: TestClient, root, *, name: str, description: str, capabilities: list[str]):
    return register_verify_and_list(
        client,
        root,
        name=name,
        description=description,
        capabilities=capabilities,
    )


def _register_verify_and_list(
    client: TestClient,
    root,
    *,
    email: str | None = None,
    capabilities: list[str] | None = None,
) -> tuple[str, str]:
    name = _unique_name()
    reg = client.post(
        "/agents/register",
        json=registration_payload(
            agent_name= name,
            email= email or f"disc_{uuid.uuid4().hex[:8]}@example.com",
            capabilities= capabilities or ["onboarding"])
    )
    assert reg.status_code == 200
    data = reg.json()
    api_key = data["api_key"]
    agent_id = data["agent_id"]
    verify_agent_from_outbox(client, root, agent_id=agent_id)
    client.patch(
        "/agents/me",
        headers={"X-Arclya-Key": api_key},
        json={"publicly_listed": True},
    )
    return agent_id, api_key


def test_multi_capability_filter_requires_all(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed(
        client,
        isolated_accounts_root,
        name="Dual Skill",
        description="Research and outreach",
        capabilities=["lead_research", "outreach"],
    )
    _register_listed(
        client,
        isolated_accounts_root,
        name="Research Only",
        description="Just research",
        capabilities=["lead_research"],
    )

    resp = client.get(
        "/agents/directory",
        params=[("capability", "lead_research"), ("capability", "outreach")],
    )
    data = resp.json()
    assert data["total"] == 1
    assert data["agents"][0]["agent_name"] == "Dual Skill"
    assert data["filters"]["capabilities"] == ["lead_research", "outreach"]


def test_search_matches_capabilities_with_relevance(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed(
        client,
        isolated_accounts_root,
        name="Alpha Bot",
        description="General helper",
        capabilities=["recruitment"],
    )
    _register_listed(
        client,
        isolated_accounts_root,
        name="Beta Bot",
        description="Handles closing",
        capabilities=["closing", "objection_handling"],
    )

    resp = client.get("/agents", params={"q": "closing"})
    data = resp.json()
    assert data["total"] == 1
    assert data["mode"] == "search"
    assert data["scoring_active"] is True
    assert data["agents"][0]["agent_name"] == "Beta Bot"
    assert "relevance" in data["agents"][0]
    assert data["agents"][0]["relevance"] > 0
    assert data["pagination"]["sort"] == "relevance"


def test_search_relevance_sorts_best_match_first(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed(
        client,
        isolated_accounts_root,
        name="SaaS Helper",
        description="Misc",
        capabilities=["onboarding"],
    )
    _register_listed(
        client,
        isolated_accounts_root,
        name="SaaS Recruiter",
        description="Recruits SaaS partners",
        capabilities=["recruitment"],
    )

    data = client.get("/agents", params={"q": "saas recruiter", "sort": "relevance"}).json()
    assert data["agents"][0]["agent_name"] == "SaaS Recruiter"
    assert data["agents"][0]["relevance"] >= data["agents"][1]["relevance"]


def test_recommended_endpoint_requires_auth(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get("/agents/recommended")
    assert resp.status_code == 401


def test_recommended_agents_by_capability_overlap(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    viewer_id, viewer_key = _register_verify_and_list(
        client,
        isolated_accounts_root,
        capabilities=["recruitment", "lead_research"],
    )

    high_id, _ = _register_listed(
        client,
        isolated_accounts_root,
        name="High Overlap",
        description="Recruitment specialist",
        capabilities=["recruitment", "lead_research", "outreach"],
    )
    _register_listed(
        client,
        isolated_accounts_root,
        name="Low Overlap",
        description="Only closing",
        capabilities=["closing"],
    )

    resp = client.get("/agents/recommended", headers={"X-Arclya-Key": viewer_key})
    data = resp.json()
    assert data["mode"] == "recommended"
    assert data["scoring_active"] is True
    assert data["total"] >= 1
    assert data["agents"][0]["agent_id"] == high_id
    assert "match_score" in data["agents"][0]
    assert data["agents"][0]["match_score"] > 0
    assert all(a["agent_id"] != viewer_id for a in data["agents"])


def test_directory_recommended_flag(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, key = _register_verify_and_list(client, isolated_accounts_root)
    _register_listed(
        client,
        isolated_accounts_root,
        name="Peer Agent",
        description="Also onboarding",
        capabilities=["onboarding"],
    )

    resp = client.get("/agents/directory", params={"recommended": "true"}, headers={"X-Arclya-Key": key})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "recommended"
    assert resp.json()["filters"]["recommended"] is True


def test_compute_search_relevance_module():
    row = {
        "agent_name": "RecruitBot",
        "description": "Finds partners",
        "capabilities": ["recruitment", "outreach"],
    }
    assert compute_search_relevance(row, "recruitment") > 0
    assert compute_search_relevance(row, "nonexistent") == 0


def test_compute_match_score_module():
    row = {"capabilities": ["recruitment", "outreach", "closing"]}
    score = compute_capability_match_score(row, ["recruitment", "lead_research"])
    assert 0 < score < 1


def test_agent_card_advertises_discovery(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get("/.well-known/agent-card.json").json()
    caps = card["platform"]["agent_directory_capabilities"]
    assert caps["multi_capability_filter"] is True
    assert "relevance" in caps["sort_options"]
    assert caps["recommendations"]["endpoint"] == "GET /agents/recommended"
    assert "agent_directory_discovery" in card["platform"]["features"]
    doc_rels = {d.get("rel") for d in card.get("documentation", [])}
    assert "agent-directory-recommended" in doc_rels