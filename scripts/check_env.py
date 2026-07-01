#!/usr/bin/env python3
"""Quick .env validation (masks secrets in output)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arclya2a.settings import get_settings, reset_dotenv_state  # noqa: E402


def _mask(value: str | None) -> str:
    if not value:
        return "MISSING"
    if len(value) <= 8:
        return value[:2] + "…" + value[-1:]
    return value[:4] + "…" + value[-4:]


def main() -> int:
    reset_dotenv_state()
    s = get_settings()
    c = s.crypto
    checks = [
        ("XAI_API_KEY", bool(s.xai_api_key)),
        ("ARCLYA_API_KEY", bool(s.arclya_api_key)),
        ("ARCLYA_OPERATOR_KEY", bool(s.arclya_operator_key)),
        ("RENDER_API_KEY", bool(__import__("os").environ.get("RENDER_API_KEY"))),
        ("Crypto enabled", c.enabled),
        ("Crypto wallets", c.configured),
        ("Crypto base", bool(c.wallets.get("base"))),
        ("Crypto ethereum", bool(c.wallets.get("ethereum"))),
        ("Crypto solana", bool(c.wallets.get("solana"))),
        ("Crypto bnb", bool(c.wallets.get("bnb"))),
    ]
    print("Arclya .env check")
    print("-" * 40)
    for name, ok in checks:
        print(f"  {'OK' if ok else 'MISSING':7}  {name}")
    print("-" * 40)
    print(f"  Operator key length: {len(s.arclya_operator_key or '')} (min 8)")
    print(f"  Crypto networks: {', '.join(c.networks) or 'none'}")
    print(f"  Masked XAI:        {_mask(s.xai_api_key)}")
    print(f"  Masked ARCLYA_API: {_mask(s.arclya_api_key)}")
    print(f"  Masked OPERATOR:   {_mask(s.arclya_operator_key)}")
    print(f"  Masked RENDER:     {_mask(__import__('os').environ.get('RENDER_API_KEY'))}")
    missing = [n for n, ok in checks if not ok]
    if missing:
        print(f"\nFix: {', '.join(missing)}")
        return 1
    if len(s.arclya_operator_key or "") < 8:
        print("\nWarning: operator key should be at least 8 characters.")
    print("\nAll required values present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())