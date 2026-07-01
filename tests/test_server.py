from fastapi.testclient import TestClient

from arclya2a.server.app import create_app


def _handoff_payload(**overrides):
    base = {
        "deal_id": "deal_test_001",
        "customer_company": "TestCo",
        "task_context": "HTTP test",
        "auto_route": False,
        "onboarding_complete": True,
    }
    base.update(overrides)
    return base


def test_handoff_chain_returns_summary(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.post("/orchestrate/handoff-chain", json=_handoff_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert data["summary"]["agents_executed"]
    assert "profit_guardrail" in data["summary"]["agents_executed"]
    assert "final_arbiter" in data["summary"]["agents_executed"]


def test_handoff_chain_validation_error_invalid_entry_agent(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.post(
        "/orchestrate/handoff-chain",
        json=_handoff_payload(entry_agent="not_a_real_agent"),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


def test_handoff_chain_validation_error_negative_revenue(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.post(
        "/orchestrate/handoff-chain",
        json=_handoff_payload(revenue_usd=-1),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_handoff_chain_requires_api_key_when_configured(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="secret-test-key"))
    resp = client.post("/orchestrate/handoff-chain", json=_handoff_payload())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "authentication_error"

    resp = client.post(
        "/orchestrate/handoff-chain",
        json=_handoff_payload(),
        headers={"X-Arclya-Key": "secret-test-key"},
    )
    assert resp.status_code == 200


def test_handoff_chain_accepts_bearer_token(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, api_key="bearer-key-123"))
    resp = client.post(
        "/orchestrate/handoff-chain",
        json=_handoff_payload(),
        headers={"Authorization": "Bearer bearer-key-123"},
    )
    assert resp.status_code == 200


def test_agent_card_and_health_are_public(root):
    client = TestClient(create_app(root, api_key="secret-key"))
    assert client.get("/health").status_code == 200
    assert client.get("/.well-known/agent-card.json").status_code == 200
    card = client.get("/.well-known/agent-card.json").json()
    assert card.get("authentication", {}).get("name") == "X-Arclya-Key"


def test_rate_limit_enforced(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai, rate_limit_per_minute=2))
    for _ in range(2):
        assert client.post("/orchestrate/handoff-chain", json=_handoff_payload()).status_code == 200
    resp = client.post("/orchestrate/handoff-chain", json=_handoff_payload())
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in resp.headers


def test_route_preview_recruiter_path(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get(
        "/orchestrate/route",
        params={"onboarding_complete": "true", "acquisition_stage": "prospect"},
    )
    assert resp.status_code == 200
    assert resp.json()["entry_agent"] == "recruiter"


def test_health_includes_service(root):
    client = TestClient(create_app(root))
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "arclya2a"
    assert "auth_enabled" in data
    assert "rate_limit_per_minute" in data