"""Tests for scripts/sandbox_partner_rehearsal.py.

Runs in the default pytest collection (CI-safe: mocked xAI, in-process HTTP).
Selectively: pytest -m rehearsal
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.rehearsal
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sandbox_partner_rehearsal as rehearsal  # noqa: E402
from arclya2a.partners.progress import collect_blocking_issues
from sandbox_partner_rehearsal import (  # noqa: E402
    REHEARSAL_PROFILE,
    format_graduation_report,
    resolve_sandbox_key,
    run_rehearsal,
)
from arclya2a.server.app import create_app


class _TestClientAdapter:
    """Wrap FastAPI TestClient for the rehearsal HttpClient protocol."""

    def __init__(self, client: TestClient):
        self._client = client

    def post(self, url: str, *, json=None, headers=None):
        return self._client.post(url, json=json, headers=headers)

    def get(self, url: str, *, headers=None):
        return self._client.get(url, headers=headers)


def _unique_agent() -> str:
    return f"RehearsalTest_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def relax_sandbox_register_limits(monkeypatch):
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_REGISTER_PER_IP_DAY", "1000")
    monkeypatch.setenv("ARCLYA_SANDBOX_MAX_KEYS_PER_AGENT", "10")


@pytest.fixture
def rehearsal_client(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="prod-secret"))
    yield _TestClientAdapter(client)


def test_rehearsal_profile_is_complete():
    from arclya2a.config.product_profile import validate_product_profile

    ok, missing = validate_product_profile(REHEARSAL_PROFILE)
    assert ok is True
    assert missing == []


def test_resolve_sandbox_key_auto_registers(rehearsal_client):
    key, source, partner_id = resolve_sandbox_key(
        rehearsal_client,
        "http://testserver",
        agent_name=_unique_agent(),
    )
    assert source == "registered"
    assert key.startswith("arclya_sandbox_")
    assert partner_id and partner_id.startswith("tp_")


def test_run_rehearsal_achieves_graduation_ready(rehearsal_client):
    report = run_rehearsal(
        base_url="http://testserver",
        http_client=rehearsal_client,
        agent_id="rehearsal_test_agent",
    )
    assert report["exit_code"] == 0
    assert report["graduation_ready"] is True
    assert all(step["ok"] for step in report["steps"])
    progress = report["progress"]
    assert progress["milestones"]["profile_validated"] is True
    assert progress["milestones"]["close_dry_run"] is True
    assert progress["graduation_ready"] is True
    assert report["blocking_issues"] == []


def test_run_rehearsal_with_existing_sandbox_key(rehearsal_client):
    key, _, _ = resolve_sandbox_key(
        rehearsal_client,
        "http://testserver",
        agent_name=_unique_agent(),
    )
    report = run_rehearsal(
        base_url="http://testserver",
        sandbox_key=key,
        http_client=rehearsal_client,
    )
    assert report["key_source"] == "argument"
    assert report["graduation_ready"] is True


def test_format_graduation_report_includes_milestones(rehearsal_client):
    report = run_rehearsal(
        base_url="http://testserver",
        http_client=rehearsal_client,
    )
    text = format_graduation_report(report)
    assert "Graduation Report" in text
    assert "graduation_ready:" in text
    assert "profile_validated" in text or "Product profile" in text


def test_collect_blocking_issues_when_incomplete():
    progress = {
        "milestones": {"profile_validated": False, "close_dry_run": False},
        "milestone_labels": {
            "profile_validated": "Profile validated",
            "close_dry_run": "Close dry run",
        },
        "security": {"behavior_score": 50, "emergency_stop_count": 1, "suspicious_flags": ["burst_traffic"]},
    }
    issues = collect_blocking_issues(progress)
    assert any("Profile validated" in i for i in issues)
    assert any("Emergency stops" in i for i in issues)
    assert any("burst_traffic" in i for i in issues)
    assert any("Behavior score" in i for i in issues)


def test_rehearsal_fails_on_invalid_profile(rehearsal_client, monkeypatch):
    monkeypatch.setattr(rehearsal, "REHEARSAL_PROFILE", {"agent_name": "X"}, raising=False)
    report = run_rehearsal(
        base_url="http://testserver",
        http_client=rehearsal_client,
    )
    assert report["exit_code"] == 1
    assert report["graduation_ready"] is False
    assert report["steps"][0]["name"] == "validate_profile"
    assert report["steps"][0]["ok"] is False