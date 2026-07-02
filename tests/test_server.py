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


def test_learning_patches_endpoint(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/learning/patches")
    assert resp.status_code == 200
    data = resp.json()
    assert "patches" in data


def test_learning_patches_dashboard_endpoint(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/learning/patches/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending_count" in data
    assert "pending_by_risk" in data
    assert "recent_applied" in data
    assert "outcome_stats" in data
    assert "issue_summary" in data
    assert "recent_learning_runs" in data
    assert "scheduler" in data


def test_learning_run_endpoint(root, mock_xai, monkeypatch):
    monkeypatch.setenv("ARCLYA_AUTO_APPLY_LOW_RISK", "1")
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.post("/learning/run", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "patches_created" in data
    assert "trigger" in data
    assert data["trigger"] == "manual"


def test_learning_runs_endpoint(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/learning/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data
    assert "scheduler_enabled" in data


def test_learning_scheduler_status_endpoint(root, mock_xai):
    client = TestClient(create_app(root, xai_client=mock_xai))
    resp = client.get("/learning/scheduler/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "should_run" in data
    assert "reason" in data


def test_tools_endpoint_public_and_lists_closer_tools(root):
    client = TestClient(create_app(root, api_key="secret-key"))
    resp = client.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tools"] >= 5

    resp = client.get("/tools", params={"agent_id": "closer"})
    assert resp.status_code == 200
    closer = resp.json()
    tool_ids = {t["id"] for t in closer["tools"]}
    assert "linear.create_followup_task" in tool_ids


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
    assert data["status"] in ("healthy", "degraded")
    assert "learning_last_run" in data
    assert "tool_failure_rate" in data
    assert "pending_high_risk_patches" in data
    agents = data["external_agents"]
    assert agents["status"] == "available"
    assert "terms_version" in agents
    assert "accounts_total" in agents
    assert "onboarding_guide_version" in agents


def test_status_endpoint(root):
    client = TestClient(create_app(root))
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "arclya2a"
    assert "learning" in data
    assert "tools" in data
    assert "handoffs" in data
    agents = data["external_agents"]
    assert agents["status"] == "available"
    assert "accounts" in agents
    assert "rate_limits" in agents
    assert "activity_24h" in agents
    assert agents["documentation"]["production_readiness"] == "docs/production-readiness-checklist.md"
    assert "platform_summary" in data
    assert data["status_page"] == "/platform/status"


def test_ops_dashboard_endpoint(root):
    client = TestClient(create_app(root))
    resp = client.get("/ops/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "learning" in data
    assert "patches" in data
    assert "partners" in data
    assert "graduated" in data["partners"]
    assert "recent_graduations" in data["partners"]


def test_health_detailed_mode(root):
    client = TestClient(create_app(root))
    resp = client.get("/health", params={"detailed": True})
    assert resp.status_code == 200
    data = resp.json()
    assert "operations" in data


def test_landing_page(root):
    client = TestClient(create_app(root))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Arclya A2A" in resp.text
    assert "lead routing" in resp.text.lower()
    assert "accept_terms" in resp.text
    assert "production-readiness-checklist" in resp.text
    assert "Pay with USDC" in resp.text
    assert "Solana" in resp.text
    assert "BSC" in resp.text
    assert "payments/crypto/packages" in resp.text
    assert "payments/crypto/checkout" in resp.text


def test_onboarding_validate_endpoint_valid(root):
    client = TestClient(create_app(root))
    profile = {
        "agent_name": "Test Agent",
        "product_name": "Test Product",
        "product_description": "Agent-to-agent lead routing with pay-on-close tracking.",
        "target_customer": "SaaS agents",
        "typical_deal_size": "$49/mo",
        "common_objections": ["Price", "ROI", "Integration"],
        "preferred_pricing_model": "success_based",
        "accepts_crypto": False,
        "destination_link": "https://example.com/signup",
        "affiliate_code": "TEST123",
    }
    resp = client.post("/onboarding/validate", json={"product_profile": profile})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["destination_cta_preview"]


def test_onboarding_validate_endpoint_invalid(root):
    client = TestClient(create_app(root))
    resp = client.post("/onboarding/validate", json={"product_profile": {"agent_name": "X"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["validation_errors"]