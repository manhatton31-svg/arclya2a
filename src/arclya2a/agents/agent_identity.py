"""Cryptographic identity and signed Agent Cards (A2A v1.0)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

A2A_PROTOCOL_VERSION = "1.0"
SIGNATURE_ALGORITHM = "HS256"
PLATFORM_KEY_ID = "arclya-platform-v1"


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signing_key(root: Path) -> bytes:
    """Platform signing secret — set ARCLYA_AGENT_CARD_SIGNING_KEY in production."""
    explicit = os.environ.get("ARCLYA_AGENT_CARD_SIGNING_KEY", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    operator = os.environ.get("ARCLYA_OPERATOR_KEY", "").strip()
    if operator:
        return hashlib.sha256(f"arclya-card-sign:{operator}".encode()).digest()
    stable = hashlib.sha256(f"arclya-dev:{root.resolve()}".encode()).hexdigest()
    return f"arclya-dev-signing-{stable}".encode("utf-8")


def agent_did(agent_id: str) -> str:
    return f"did:arclya:{agent_id}"


def agent_public_fingerprint(agent_id: str) -> str:
    return hashlib.sha256(f"arclya-identity:v1:{agent_id}".encode()).hexdigest()


def build_identity_block(agent_id: str) -> dict[str, Any]:
    """Verifiable agent identity for A2A interoperability."""
    fp = agent_public_fingerprint(agent_id)
    return {
        "did": agent_did(agent_id),
        "publicKeyFingerprint": f"sha256:{fp}",
        "verificationMethod": "arclya-hmac-v1",
        "interoperability": {
            "handoff_protocol": "strong_handoff_v1",
            "task_delegation": True,
            "signed_agent_card": True,
        },
    }


def sign_payload(payload: dict[str, Any], *, root: Path) -> dict[str, Any]:
    digest = hmac.new(signing_key(root), _canonical_json(payload), hashlib.sha256).digest()
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "keyId": PLATFORM_KEY_ID,
        "value": base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    }


def verify_signature(
    payload: dict[str, Any],
    signature: dict[str, Any],
    *,
    root: Path,
) -> bool:
    if not signature or signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    expected = sign_payload(payload, root=root)
    return hmac.compare_digest(str(signature.get("value", "")), str(expected.get("value", "")))


def attach_platform_signature(card: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Sign agent card JSON (signature covers all fields except signature itself)."""
    signed = dict(card)
    signable = {k: v for k, v in signed.items() if k != "signature"}
    signed["signature"] = sign_payload(signable, root=root)
    if "a2a" in signed:
        signed["a2a"]["protocol_version"] = A2A_PROTOCOL_VERSION
        signed["a2a"]["signed_agent_card"] = True
    return signed


def build_per_agent_card(
    account: dict[str, Any],
    *,
    base_url: str,
    root: Path,
) -> dict[str, Any]:
    """Minimal signed Agent Card for an external agent identity."""
    agent_id = str(account.get("agent_id", ""))
    card: dict[str, Any] = {
        "name": account.get("agent_name", "Arclya Agent"),
        "description": account.get("description", ""),
        "url": f"{base_url.rstrip('/')}/agents/{agent_id}",
        "version": "1.0.0",
        "a2a": {
            "protocol_version": A2A_PROTOCOL_VERSION,
            "identity": build_identity_block(agent_id),
            "inference": {"provider": "xai", "xai_only": True},
            "constitutional": {
                "margin_guardrail": "profit_guardrail",
                "living_prompts": True,
                "prompt_caching": True,
            },
        },
        "capabilities": {
            "streaming": False,
            "taskDelegation": True,
            "secureHandoff": True,
        },
        "skills": [
            {
                "id": agent_id,
                "name": account.get("agent_name", ""),
                "tags": account.get("capabilities", []),
            }
        ],
        "endpoints": {
            "profile": f"{base_url.rstrip('/')}/agents/{agent_id}",
            "reputation": f"{base_url.rstrip('/')}/agents/{agent_id}/reputation",
            "platform_card": f"{base_url.rstrip('/')}/.well-known/agent-card.json",
        },
    }
    from arclya2a.agents.reputation import public_reputation_summary

    rep = public_reputation_summary(root, agent_id)
    if rep:
        card["reputation"] = rep
    return attach_platform_signature(card, root=root)