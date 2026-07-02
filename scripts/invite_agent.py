#!/usr/bin/env python3
"""Generate Arclya agent invitation text for the referral program."""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an agent invitation for the referral program")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ARCLYA_PUBLIC_URL") or os.environ.get("ARCLYA_BASE_URL") or "https://arclya2a.onrender.com",
    )
    parser.add_argument("--api-key", default=os.environ.get("ARCLYA_AGENT_API_KEY") or os.environ.get("X_ARCLYA_KEY"))
    parser.add_argument("--referral-code", help="Public invite without auth when code is known")
    parser.add_argument("--invitee-name", default="there")
    parser.add_argument("--json", action="store_true", help="Print full JSON invitation")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    if args.api_key:
        resp = httpx.post(
            f"{base}/agents/referrals/invite",
            headers={"X-Arclya-Key": args.api_key},
            json={"invitee_name": args.invitee_name},
            timeout=30.0,
        )
        if resp.status_code != 200:
            print(f"Error {resp.status_code}: {resp.text}", file=sys.stderr)
            return 1
        data = resp.json()["invitation"]
    elif args.referral_code:
        resp = httpx.get(f"{base}/agents/referrals/invite", params={"code": args.referral_code}, timeout=30.0)
        if resp.status_code != 200:
            print(f"Error {resp.status_code}: {resp.text}", file=sys.stderr)
            return 1
        data = resp.json()
        if not data.get("valid", True) and data.get("message"):
            print(data["message"], file=sys.stderr)
            return 1
    else:
        print("Provide --api-key or --referral-code", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(data.get("message", json.dumps(data, indent=2)))
        print()
        print(f"Register: {data.get('register_url')}")
        print(f"Referral code: {data.get('referral_code')}")
        print(f"Reward: ${data.get('reward_usd')} {data.get('reward_currency', 'USDC')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())