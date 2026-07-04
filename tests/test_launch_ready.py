"""Tests for scripts/launch_ready.py helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from scripts.launch_ready import (
    build_registration_body,
    extract_token_from_link,
    is_local_base_url,
    parse_api_error,
    registration_failure_hints,
)


def test_build_registration_body_includes_terms():
    body = build_registration_body(
        agent_name="Launch_abc",
        email="launch_abc@example.com",
        suffix="abc",
    )
    assert body["terms_accepted"] is True
    assert body["accept_terms"] is True
    assert body["agent_name"] == "Launch_abc"


def test_extract_token_from_link():
    link = "https://arclya.example/agents/verify-email?token=ev_testtoken123"
    assert extract_token_from_link(link) == "ev_testtoken123"


def test_parse_api_error_structured():
    response = MagicMock()
    response.status_code = 422
    response.text = json.dumps(
        {
            "error": {
                "code": "validation_error",
                "message": "Registration validation failed",
                "details": {
                    "fields": [{"field": "terms_accepted", "message": "terms acceptance required"}],
                },
            }
        }
    )
    response.json.return_value = json.loads(response.text)
    err = parse_api_error(response)
    assert err["status_code"] == 422
    assert err["code"] == "validation_error"
    assert err["details"]["fields"][0]["field"] == "terms_accepted"


def test_registration_failure_hints_for_terms():
    err = {
        "code": "validation_error",
        "details": {
            "fields": [{"field": "terms_accepted", "message": "You must accept the Terms of Service"}],
        },
    }
    hints = registration_failure_hints(err)
    assert any("terms_accepted" in h or "accept_terms" in h for h in hints)


def test_is_local_base_url():
    assert is_local_base_url("http://127.0.0.1:8787") is True
    assert is_local_base_url("http://localhost:8787") is True
    assert is_local_base_url("https://arclya2a.onrender.com") is False