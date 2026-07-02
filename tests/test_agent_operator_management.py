"""Tests for operator management and moderation of external agents."""
from __future__ import annotations
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from arclya2a.agents.audit import EVENT_STATUS_CHANGED, read_agent_audit_events
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list
OPERATOR_KEY = 'operator-mgmt-test-key'

def _unique_name() -> str:
    return f'OpMgr_{uuid.uuid4().hex[:8]}'

@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    monkeypatch.setenv('ARCLYA_OPERATOR_KEY', OPERATOR_KEY)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'pricing').mkdir()
    (tmp_path / 'config' / 'core.json').write_text(json.dumps({'platform_name': 'Arclya A2A', 'version': '0.1.0', 'server': {'host': '127.0.0.1', 'port': 8787, 'base_url': 'http://127.0.0.1:8787'}}), encoding='utf-8')
    (tmp_path / 'agents' / 'registry.json').write_text(json.dumps({'version': '1.0.0', 'agents': []}), encoding='utf-8')
    return tmp_path

@pytest.fixture
def operator_headers():
    return {'X-Arclya-Operator-Key': OPERATOR_KEY}

def _register_listed(client: TestClient, root) -> tuple[str, str]:
    return register_verify_and_list(client, root)

def test_manage_requires_operator(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    assert client.get('/agents/manage').status_code == 401

def test_operator_lists_agents_with_filters(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id, _ = _register_listed(client, isolated_accounts_root)
    resp = client.get('/agents/manage', headers=operator_headers, params={'status': 'active', 'publicly_listed': 'true'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] >= 1
    assert any((a['agent_id'] == agent_id for a in data['agents']))

def test_operator_suspend_blocks_api_and_directory(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root, api_key='platform-secret'))
    agent_id, key = _register_listed(client, isolated_accounts_root)
    headers = {'X-Arclya-Key': key}
    suspend = client.patch(f'/agents/{agent_id}/status', headers=operator_headers, json={'status': 'suspended', 'reason': 'policy violation'})
    assert suspend.status_code == 200
    assert suspend.json()['status'] == 'suspended'
    assert suspend.json()['agent']['publicly_listed'] is False
    me = client.get('/agents/me', headers=headers)
    assert me.status_code == 403
    assert me.json()['error']['details']['reason'] == 'account_suspended'
    directory = client.get('/agents')
    listed_ids = {a['agent_id'] for a in directory.json()['agents']}
    assert agent_id not in listed_ids
    public = client.get(f'/agents/{agent_id}')
    assert public.status_code == 404

def test_operator_reactivate_restores_access(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root, api_key='platform-secret'))
    agent_id, key = _register_listed(client, isolated_accounts_root)
    headers = {'X-Arclya-Key': key}
    client.patch(f'/agents/{agent_id}/status', headers=operator_headers, json={'status': 'suspended', 'reason': 'test'})
    reactivate = client.patch(f'/agents/{agent_id}/status', headers=operator_headers, json={'status': 'active', 'reason': 'cleared'})
    assert reactivate.status_code == 200
    assert reactivate.json()['status'] == 'active'
    client.patch('/agents/me', headers=headers, json={'publicly_listed': True})
    assert client.get('/agents/me', headers=headers).status_code == 200

def test_pending_review_blocks_authentication(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root, api_key='platform-secret'))
    agent_id, key = _register_listed(client, isolated_accounts_root)
    client.patch(f'/agents/{agent_id}/status', headers=operator_headers, json={'status': 'pending_review'})
    resp = client.get('/agents/me', headers={'X-Arclya-Key': key})
    assert resp.status_code == 403
    assert resp.json()['error']['details']['reason'] == 'account_pending_review'

def test_status_change_audited(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id, _ = _register_listed(client, isolated_accounts_root)
    client.patch(f'/agents/{agent_id}/status', headers=operator_headers, json={'status': 'suspended', 'reason': 'audit test'})
    events = read_agent_audit_events(isolated_accounts_root, agent_id=agent_id, event_type=EVENT_STATUS_CHANGED)
    assert len(events) >= 1
    assert events[0]['details']['new_status'] == 'suspended'

def test_agent_audit_endpoint(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root))
    agent_id, _ = _register_listed(client, isolated_accounts_root)
    resp = client.get(f'/agents/{agent_id}/audit', headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()['agent_id'] == agent_id
    assert resp.json()['count'] >= 1

def test_ops_dashboard_management_section(isolated_accounts_root, operator_headers):
    client = TestClient(create_app(isolated_accounts_root))
    client.post('/agents/register', json=registration_payload(agent_name=_unique_name()))
    dash = client.get('/ops/dashboard').json()
    mgmt = dash['agents']['management']
    assert mgmt['total_agents'] >= 1
    assert 'active' in mgmt
    assert 'suspended' in mgmt
    assert mgmt['operator_endpoints']['list'] == 'GET /agents/manage'

def test_agent_card_documents_operator_management(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get('/.well-known/agent-card.json').json()
    mgmt = card['platform']['agent_operator_management']
    assert mgmt['endpoints']['list_agents'] == 'GET /agents/manage'
    assert 'agent_operator_moderation' in card['platform']['features']
    assert 'suspended' in mgmt['statuses']
