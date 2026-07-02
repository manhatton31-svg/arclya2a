"""Tests for post-registration welcome flow and onboarding guide."""
from __future__ import annotations
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from arclya2a.agents.onboarding_guide import GUIDE_VERSION, build_agent_onboarding_guide, build_registration_welcome
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload

def _unique_name() -> str:
    return f'PostReg_{uuid.uuid4().hex[:8]}'

@pytest.fixture
def isolated_accounts_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'pricing').mkdir()
    (tmp_path / 'config' / 'core.json').write_text(json.dumps({'platform_name': 'Arclya A2A', 'version': '0.1.0', 'server': {'host': '127.0.0.1', 'port': 8787, 'base_url': 'http://127.0.0.1:8787'}}), encoding='utf-8')
    (tmp_path / 'agents' / 'registry.json').write_text(json.dumps({'version': '1.0.0', 'agents': []}), encoding='utf-8')
    return tmp_path

def test_registration_includes_welcome_and_resources(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    name = _unique_name()
    resp = client.post('/agents/register', json=registration_payload(agent_name=name, description='A recruiting agent'))
    assert resp.status_code == 200
    data = resp.json()
    assert data['registered'] is True
    assert name in data['welcome_message']
    assert 'Welcome to Arclya' in data['welcome_message']
    assert data['api_key_reminder']['shown_once'] is True
    assert data['api_key_reminder']['importance'] == 'critical'
    assert 'cannot be retrieved' in data['api_key_reminder']['message']
    assert len(data['next_steps']) == 8
    assert data['next_steps'][0]['id'] == 'accept_terms'
    assert data['next_steps'][0]['priority'] == 'critical'
    assert data['next_steps'][1]['id'] == 'store_api_key'
    assert data['next_steps'][2]['method'] == 'GET'
    assert data['next_steps'][2]['url'].endswith('/agents/me')
    resources = data['resources']
    assert resources['onboarding_guide'].endswith('/agents/onboarding/guide')
    assert resources['agent_directory'].endswith('/agents/directory')
    assert resources['profile'].endswith('/agents/me')
    assert data['agent_id'] in resources['public_profile']
    assert 'documentation' in resources

def test_registration_next_steps_include_directory_opt_in(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    data = client.post('/agents/register', json=registration_payload(agent_name=_unique_name())).json()
    join_step = next((s for s in data['next_steps'] if s['id'] == 'join_directory'))
    assert join_step['body_example']['publicly_listed'] is True
    browse_step = next((s for s in data['next_steps'] if s['id'] == 'browse_directory'))
    assert browse_step['auth_required'] is False

def test_onboarding_guide_includes_post_registration_flow(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    resp = client.get('/agents/onboarding/guide')
    assert resp.status_code == 200
    guide = resp.json()
    assert guide['version'] == GUIDE_VERSION
    assert guide['post_registration'] is not None
    assert guide['post_registration']['title']
    assert len(guide['post_registration']['steps']) == 8
    assert guide['full_flow']['steps']
    assert guide['resources']['onboarding_guide'].endswith('/agents/onboarding/guide')
    assert guide['authentication']['shown_once_at_registration'] is True

def test_onboarding_guide_module_with_base_url():
    guide = build_agent_onboarding_guide(base_url='https://arclya.example')
    assert guide['post_registration']['resources']['agent_directory'] == 'https://arclya.example/agents/directory'
    assert guide['full_flow']['steps'][0]['url'] == 'https://arclya.example/agents/register'

def test_build_registration_welcome_structure():
    account = {'agent_id': 'ag_abc123def456', 'agent_name': 'Demo Agent'}
    welcome = build_registration_welcome(account, base_url='https://arclya.example')
    assert 'Demo Agent' in welcome['welcome_message']
    assert welcome['resources']['public_profile'] == 'https://arclya.example/agents/ag_abc123def456'
    assert welcome['next_steps'][0]['id'] == 'accept_terms'
    assert welcome['next_steps'][1]['id'] == 'store_api_key'

def test_agent_card_highlights_post_registration(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get('/.well-known/agent-card.json').json()
    assert 'agent_post_registration_flow' in card['platform']['features']
    post_reg = card['platform']['external_agents']['post_registration']
    assert post_reg['first_step']
    assert 'welcome_message' in post_reg['summary']
