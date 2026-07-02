"""Tests for the public Agent Directory (Agent Hangout)."""
from __future__ import annotations
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from arclya2a.agents.accounts import list_directory_agents, register_agent_account, update_agent_profile
from arclya2a.agents.email_verification import issue_verification_token, verify_email_token
from arclya2a.server.app import create_app
from tests.agent_helpers import registration_payload, register_verify_and_list, verify_agent_from_outbox

def _unique_name() -> str:
    return f'DirAgent_{uuid.uuid4().hex[:8]}'

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

def _register_listed_agent(client: TestClient, root, *, name: str, description: str, capabilities: list[str]):
    return register_verify_and_list(client, root, name=name, description=description, capabilities=capabilities)

def test_new_agents_not_listed_by_default(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    client.post('/agents/register', json=registration_payload(agent_name=_unique_name(), description='Hidden agent'))
    resp = client.get('/agents/directory')
    assert resp.status_code == 200
    assert resp.json()['total'] == 0
    assert resp.json()['count'] == 0

def test_opt_in_via_patch_lists_agent(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    name = _unique_name()
    email = f'dir_{uuid.uuid4().hex[:8]}@example.com'
    reg = client.post('/agents/register', json=registration_payload(agent_name=name, email=email, description='Recruiting specialist', capabilities=['recruitment']))
    assert reg.status_code == 200
    data = reg.json()
    api_key = data['api_key']
    agent_id = data['agent_id']
    assert data.get('email_verification', {}).get('sent') is True
    me = client.get('/agents/me', headers={'X-Arclya-Key': api_key}).json()
    assert me['publicly_listed'] is False
    assert me['email_verified'] is False
    blocked = client.patch('/agents/me', headers={'X-Arclya-Key': api_key}, json={'publicly_listed': True})
    assert blocked.status_code == 422
    verify_agent_from_outbox(client, isolated_accounts_root, agent_id=agent_id)
    patch = client.patch('/agents/me', headers={'X-Arclya-Key': api_key}, json={'publicly_listed': True})
    assert patch.status_code == 200
    assert patch.json()['profile']['publicly_listed'] is True
    assert patch.json()['profile']['email_verified'] is True
    directory = client.get('/agents').json()
    assert directory['total'] == 1
    assert directory['count'] == 1
    entry = directory['agents'][0]
    assert entry['agent_name'] == name
    assert entry['description'] == 'Recruiting specialist'
    assert entry['capabilities'] == ['recruitment']
    assert entry['capability_count'] == 1
    assert entry['created_at']
    assert 'email' not in entry

def test_directory_alias_endpoint(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed_agent(client, isolated_accounts_root, name=_unique_name(), description='Listed via alias test', capabilities=['onboarding'])
    root_resp = client.get('/agents')
    alias_resp = client.get('/agents/directory')
    assert root_resp.status_code == 200
    assert alias_resp.status_code == 200
    assert root_resp.json()['total'] == alias_resp.json()['total'] == 1

def test_directory_filters_by_capability(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed_agent(client, isolated_accounts_root, name=_unique_name(), description='Closer agent', capabilities=['closing', 'objection_handling'])
    _register_listed_agent(client, isolated_accounts_root, name=_unique_name(), description='Research agent', capabilities=['lead_research'])
    resp = client.get('/agents/directory', params={'capability': 'closing'})
    data = resp.json()
    assert data['total'] == 1
    assert data['count'] == 1
    assert data['agents'][0]['capabilities'] == ['closing', 'objection_handling']

def test_directory_search_by_text(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _register_listed_agent(client, isolated_accounts_root, name='Alpha Recruiter', description='Finds SaaS partners', capabilities=['recruitment'])
    _register_listed_agent(client, isolated_accounts_root, name='Beta Closer', description='Handles enterprise deals', capabilities=['closing'])
    by_name = client.get('/agents', params={'q': 'alpha'}).json()
    assert by_name['total'] == 1
    assert by_name['agents'][0]['agent_name'] == 'Alpha Recruiter'
    by_desc = client.get('/agents', params={'q': 'enterprise'}).json()
    assert by_desc['total'] == 1
    assert by_desc['agents'][0]['agent_name'] == 'Beta Closer'

def test_opt_out_removes_from_directory(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    _, api_key = _register_listed_agent(client, isolated_accounts_root, name=_unique_name(), description='Temporary listing', capabilities=['outreach'])
    assert client.get('/agents/directory').json()['total'] == 1
    client.patch('/agents/me', headers={'X-Arclya-Key': api_key}, json={'publicly_listed': False})
    assert client.get('/agents/directory').json()['total'] == 0

def test_list_directory_agents_module(isolated_accounts_root):
    a, _, _ = register_agent_account(isolated_accounts_root, agent_name='Listed One', email=f'listed_{uuid.uuid4().hex[:6]}@example.com', description='Visible in hangout', capabilities=['onboarding'], terms_accepted=True)
    b, _, _ = register_agent_account(isolated_accounts_root, agent_name='Hidden Two', description='Not visible', capabilities=['onboarding'], terms_accepted=True)
    token_record = issue_verification_token(isolated_accounts_root, agent_id=a['agent_id'], email=a['email'])
    verified, err = verify_email_token(isolated_accounts_root, token_record['token'])
    assert err is None
    assert verified['email_verified'] is True
    a_updated, err = update_agent_profile(isolated_accounts_root, a['agent_id'], publicly_listed=True)
    assert err is None
    all_listed = list_directory_agents(isolated_accounts_root)
    assert all_listed['total'] == 1
    assert all_listed['agents'][0]['agent_id'] == a['agent_id']
    filtered = list_directory_agents(isolated_accounts_root, capabilities='onboarding')
    assert filtered['total'] == 1
    missing = list_directory_agents(isolated_accounts_root, search='Hidden')
    assert missing['total'] == 0

def test_directory_pagination(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    for idx in range(3):
        _register_listed_agent(client, isolated_accounts_root, name=f'Paged Agent {idx}', description=f'Agent number {idx}', capabilities=['onboarding'])
    page1 = client.get('/agents', params={'limit': 2, 'offset': 0}).json()
    assert page1['total'] == 3
    assert page1['count'] == 2
    assert page1['pagination']['offset'] == 0
    assert page1['pagination']['limit'] == 2
    page2 = client.get('/agents', params={'limit': 2, 'offset': 2}).json()
    assert page2['total'] == 3
    assert page2['count'] == 1
    assert page2['pagination']['offset'] == 2

def test_directory_sorts_by_created_at_desc(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    first_id, _ = _register_listed_agent(client, isolated_accounts_root, name='First Listed', description='Registered first', capabilities=['recruitment'])
    second_id, _ = _register_listed_agent(client, isolated_accounts_root, name='Second Listed', description='Registered second', capabilities=['recruitment'])
    data = client.get('/agents', params={'sort': 'created_at_desc'}).json()
    assert data['pagination']['sort'] == 'created_at_desc'
    assert data['agents'][0]['agent_id'] == second_id
    assert data['agents'][1]['agent_id'] == first_id

def test_agent_card_advertises_directory(isolated_accounts_root):
    client = TestClient(create_app(isolated_accounts_root))
    card = client.get('/.well-known/agent-card.json').json()
    assert 'agent_directory' in card['platform']['features']
    assert 'agent_directory_pagination' in card['platform']['features']
    assert 'agent_public_profiles' in card['platform']['features']
    assert card['endpoints']['agent_directory'].endswith('/agents/directory')
    assert '{agent_id}' in card['endpoints']['agent_public_profile']
    assert card['platform']['agent_directory_capabilities']['pagination'] is True
    assert card['platform']['agent_accounts']['publicly_listed'] == 0
    doc_rels = {d.get('rel') for d in card.get('documentation', [])}
    assert 'agent-directory' in doc_rels
    assert 'agent-public-profile' in doc_rels
    assert 'agent-onboarding' in doc_rels
    assert 'agent-onboarding-guide' in doc_rels
    assert 'external_agents' in card['platform']
    assert 'post_registration' in card['platform']['external_agents']
    assert card['endpoints']['agent_onboarding_guide'].endswith('/agents/onboarding/guide')
