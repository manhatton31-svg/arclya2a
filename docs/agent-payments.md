# Pay Arclya with USDC — Agent Self-Service Guide

External agents can purchase Arclya services in **USDC** without manual invoicing. The checkout flow is discoverable from the [Agent Card](https://arclya2a.onrender.com/.well-known/agent-card.json), supports **x402 Payment Required** responses, and works end-to-end on production.

## Quick start

1. **Discover** — `GET /.well-known/agent-card.json` (see `payments` block) or open `GET /` in a browser.
2. **List packages** — `GET /payments/crypto/packages`
3. **Checkout** — `POST /payments/crypto/checkout` with a package id
4. **Pay on-chain** — Send exact USDC amount to the wallet in the response (Base recommended)
5. **Submit proof** — `POST /payments/crypto/{payment_id}/submit` with your `tx_hash`
6. **Confirm** — Poll `GET /payments/crypto/{payment_id}` until status is `confirmed` (HTTP 200)
7. **Use service** — Call `POST /orchestrate/handoff-chain` with your sandbox or production key

## Service packages

| Package | ID | Price (USDC) | What you get |
|---------|-----|--------------|--------------|
| **Onboarding Package** | `onboarding_package` | $49 | Product profile validation, handoff-chain onboarding, recruitment kickoff |
| **Closer Access** | `closer_access` | $99 | AI Closer session for lead routing commitment closes |
| **Per Close** | `per_close` | $25 | Per successful lead routing commitment with tracked CTA attribution |

Package definitions live in `pricing/agent_payment_packages.json` and are served at `GET /payments/crypto/packages`.

## Supported networks

Arclya accepts **USDC on Base, Ethereum, Solana, and BSC**. Pay on whichever chain you already hold USDC.

| Network | ID | Token | Notes |
|---------|-----|-------|-------|
| **Base** (recommended) | `base` | USDC | Lowest fees, fastest settlement |
| **Ethereum** | `ethereum` | USDC | ETH mainnet |
| **Solana** | `solana` | USDC | SPL USDC |
| **BSC** | `bnb` | USDC | BNB Smart Chain |

Pass `"network": "base"`, `"ethereum"`, `"solana"`, or `"bnb"` when creating checkout or intent.

List configured networks and receive addresses:

```bash
curl -s https://arclya2a.onrender.com/payments/crypto/networks
```

## Checkout (recommended)

Create a package-based checkout with step-by-step instructions:

```bash
curl -s -X POST https://arclya2a.onrender.com/payments/crypto/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "package": "onboarding_package",
    "network": "base",
    "partner_id": "tp_your_partner_id",
    "deal_id": "deal_001",
    "agent_id": "your-agent-name"
  }'
```

**Aliases:** `service_type` accepts `onboarding`, `closer`, or `per_close` as shortcuts.

**Response (201 or 402 with `X-Arclya-Prefer-402: true`):**

- `package` — Service name, description, amount, includes
- `payment` — `payment_id`, wallet, amount, network, memo, expiry
- `instructions` — Human- and agent-readable steps, explorer URL, submit/status URLs
- `x402` — x402 V2 Payment Required payload for agent runtimes

## Custom amount (advanced)

For non-package amounts, use the intent endpoint:

```bash
curl -s -X POST https://arclya2a.onrender.com/payments/crypto/intent \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 50.0,
    "network": "base",
    "package": "closer_access",
    "partner_id": "tp_your_partner_id",
    "deal_id": "deal_001"
  }'
```

Minimum amount is set by `ARCLYA_CRYPTO_MIN_AMOUNT_USD` on the server (default $10; production may be lower).

## Submit on-chain proof

After your USDC transfer confirms:

```bash
curl -s -X POST https://arclya2a.onrender.com/payments/crypto/cpay_<id>/submit \
  -H "Content-Type: application/json" \
  -d '{"tx_hash": "0x..."}'
```

You may also pass proof via the `X-Payment` or `PAYMENT-SIGNATURE` header (JSON or base64 JSON with `tx_hash`).

## Check payment status

```bash
curl -s https://arclya2a.onrender.com/payments/crypto/cpay_<id>
```

| HTTP | Status | Meaning |
|------|--------|---------|
| 402 | `pending` or `submitted` | Payment still required or awaiting operator confirm |
| 200 | `confirmed` | Payment settled — start your purchased service |

## x402 integration

Arclya checkout endpoints return x402-compatible headers:

- `X-Payment-Required`, `X-Payment-Required-Details`, `PAYMENT-REQUIRED` — when payment is due
- `X-Payment-Id`, `X-Payment-Amount`, `X-Payment-Network`, `X-Payment-Address` — checkout details
- `PAYMENT-RESPONSE` — after proof submission

Request `402 Payment Required` explicitly with header `X-Arclya-Prefer-402: true` on intent or checkout.

## Discovery endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /.well-known/agent-card.json` | Full platform discovery including `payments` block |
| `GET /payments/crypto/packages` | Package catalog |
| `GET /payments/crypto/networks` | USDC networks and wallets |
| `POST /payments/crypto/checkout` | Package checkout with instructions |
| `POST /payments/crypto/intent` | Custom-amount intent |
| `POST /payments/crypto/{id}/submit` | Submit tx_hash proof |
| `GET /payments/crypto/{id}` | Payment status |

## Operator confirmation

After you submit `tx_hash`, an Arclya operator verifies the on-chain transfer and confirms the payment. This is intentional for Phase 1 — agents should poll status until `confirmed`.

Operators use:

```bash
python scripts/confirm_crypto_payment.py --confirm cpay_<id>
```

## After payment

Once `status: confirmed`:

1. Use your **sandbox** or **production** API key (`X-Arclya-Key`)
2. Run the service you purchased, e.g. onboarding via `POST /orchestrate/handoff-chain` with `auto_route: true`
3. Monitor via `GET /partners/me/progress` (sandbox) or `GET /ops/dashboard`

## Related docs

- [Partner Integration Guide](partner-integration-guide.md)
- [Test Partner Checklist](test-partner-onboarding-checklist.md)
- [First Crypto Sale Runbook](first-crypto-sale-runbook.md) (operators)