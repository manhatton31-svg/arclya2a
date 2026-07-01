#!/usr/bin/env python3
"""Operator CLI to issue a sandbox API key for a test partner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.partners.onboarding_guide import build_onboarding_guide
from arclya2a.partners.sandbox import register_sandbox_key, sandbox_rate_limit
from arclya2a.partners.test_registry import register_test_partner


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a sandbox API key for a test partner")
    parser.add_argument("agent_name", help="Partner agent display name")
    parser.add_argument("--agent-card-url", default=None, help="Partner Agent Card URL")
    parser.add_argument("--target-customer", default=None, help="Partner target customer")
    parser.add_argument("--contact", default=None, help="Operator contact note")
    args = parser.parse_args()

    partner = register_test_partner(
        ROOT,
        agent_name=args.agent_name,
        agent_card_url=args.agent_card_url,
        target_customer=args.target_customer,
        contact=args.contact,
    )
    key = register_sandbox_key(
        ROOT,
        partner_id=partner["partner_id"],
        agent_name=args.agent_name,
        metadata={
            "agent_card_url": args.agent_card_url,
            "target_customer": args.target_customer,
            "issued_by": "operator_cli",
        },
    )
    out = {
        "partner_id": partner["partner_id"],
        "sandbox_key": key,
        "mode": "sandbox",
        "rate_limit_per_minute": sandbox_rate_limit(),
        "guide": build_onboarding_guide()["steps"][:2],
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()