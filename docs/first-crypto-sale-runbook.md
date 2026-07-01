# First Crypto Sale Runbook

Operator guide for onboarding the **first real test partner** and closing the **first USDC crypto sale** on a live Arclya instance (Render or self-hosted).

Use this runbook before inviting external agents. It validates the full path: sandbox → graduation → crypto intent → on-chain payment → operator confirmation → audit verification.

**Optional CLI helper:** `python scripts/run_first_crypto_sale.py` chains health checks and existing operator scripts with progress reporting. The runbook is the source of truth; the script is a convenience wrapper.

**Related docs:**
- [Test Partner Checklist](test-partner-onboarding-checklist.md) — partner-facing sandbox journey
- [Pay with USDC section](test-partner-onboarding-checklist.md#pay-with-usdc--crypto-sales-first-10-sales) — agent crypto checkout steps
- [Configuration](configuration.md) — env vars and wallet setup

---

## Go / No-Go checklist

Before starting, confirm all items:

- [ ] Live instance responds at `GET /health` with `status: healthy`
- [ ] `ARCLYA_API_KEY` set (production auth enabled)
- [ ] `ARCLYA_OPERATOR_KEY` set (min 8 chars; never shared with partners)
- [ ] `ARCLYA_CRYPTO_ENABLED=1`
- [ ] Per-network wallet receive addresses configured (`ARCLYA_CRYPTO_WALLET_BASE`, etc.)
- [ ] `GET /payments/crypto/networks` returns `enabled: true` and expected networks
- [ ] Operator has access to on-chain explorer (BaseScan for Base USDC recommended)
- [ ] Test partner contact ready (or you are running rehearsal as stand-in)

Quick local check:

```bash
python scripts/check_env.py
python scripts/run_first_crypto_sale.py check
```

---

## Phase 0 — Prerequisites (operator)

### Render / production environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `ARCLYA_API_KEY` | Yes | Platform API auth |
| `ARCLYA_OPERATOR_KEY` | Yes | Graduation + crypto confirm |
| `XAI_API_KEY` | Yes (live) | Agent inference |
| `ARCLYA_CRYPTO_ENABLED` | Yes | Enable checkout |
| `ARCLYA_CRYPTO_NETWORKS` | Yes | e.g. `base,ethereum,solana,bnb` |
| `ARCLYA_CRYPTO_WALLET_BASE` | Yes (min) | USDC receive address on Base |
| `ARCLYA_CRYPTO_NETWORK` | Recommended | Default `base` |
| `RENDER_EXTERNAL_URL` | Auto on Render | Public base URL |

Set `ARCLYA_BASE_URL` locally when running CLI tools against production:

```bash
export ARCLYA_BASE_URL=https://your-arclya.onrender.com
export ARCLYA_OPERATOR_KEY=<your-operator-secret>
```

### Verify crypto is live

```bash
curl -s "$ARCLYA_BASE_URL/payments/crypto/networks" | jq .
```

Expected: `"enabled": true`, `"networks"` array with wallet addresses.

### Verify ops visibility

```bash
curl -s "$ARCLYA_BASE_URL/ops/dashboard" | jq '.payments'
```

Expected keys: `by_status`, `pending_review_count`, `confirmed_total_usd`.

---

## Phase 1 — Partner registration and rehearsal

The partner (or you as stand-in) completes the sandbox lifecycle. Goal: `graduation_ready: true`.

### Option A — Partner self-registers

Partner follows [Test Partner Checklist](test-partner-onboarding-checklist.md) Steps 1–6:

1. `POST /partners/sandbox/register` → save `sandbox_key` and `partner_id`
2. `POST /onboarding/validate` → `valid: true`
3. `POST /orchestrate/handoff-chain` smoke tests
4. Full lifecycle dry run (optional)

### Option B — Operator runs rehearsal script

Use when validating the live instance or rehearsing before a real partner:

```bash
ARCLYA_BASE_URL=https://your-arclya.onrender.com \
  python scripts/sandbox_partner_rehearsal.py
```

Or via wrapper:

```bash
ARCLYA_BASE_URL=https://your-arclya.onrender.com \
  python scripts/run_first_crypto_sale.py rehearse
```

**Success criteria:**
- Exit code `0`
- Report shows `graduation_ready: true`
- All lifecycle steps `[OK]`
- Note `partner_id` (e.g. `tp_a1b2c3d4e5f6`) — required for graduation and crypto attribution

```bash
# Verify progress via API
curl -s "$ARCLYA_BASE_URL/partners/test" | jq '.partners[] | select(.partner_id=="tp_...")'
```

---

## Phase 2 — Graduate to production

Graduation is **operator-only**. Issues a per-partner `arclya_prod_*` key and revokes sandbox keys.

### Check readiness (no changes)

```bash
ARCLYA_OPERATOR_KEY=<secret> \
  python scripts/graduate_partner.py tp_<partner_id> --check-only
```

### Graduate

```bash
curl -X POST "$ARCLYA_BASE_URL/partners/graduate" \
  -H "X-Arclya-Operator-Key: <secret>" \
  -H "Content-Type: application/json" \
  -d '{"partner_id": "tp_<partner_id>", "performed_by": "<your_name>"}'
```

Or via wrapper (uses HTTP API — works against remote Render):

```bash
ARCLYA_BASE_URL=https://your-arclya.onrender.com \
  ARCLYA_OPERATOR_KEY=<secret> \
  python scripts/run_first_crypto_sale.py graduate --partner-id tp_<partner_id>
```

Local-only alternative (requires server data directory on same machine):

```bash
ARCLYA_OPERATOR_KEY=<secret> \
  python scripts/graduate_partner.py tp_<partner_id> --performed-by <your_name>
```

**Success criteria:**
- CLI prints `Partner Graduated to Production`
- `production_key` shown once — store securely and share with partner through a secure channel
- `GET /ops/dashboard` → `partners.recent_graduations` includes this partner

**Deliver to partner:**
- Production key: `X-Arclya-Key: arclya_prod_...`
- Crypto checkout docs: [Pay with USDC](test-partner-onboarding-checklist.md#pay-with-usdc--crypto-sales-first-10-sales)
- Their `partner_id` for payment attribution

---

## Phase 3 — Agent creates crypto payment intent

The **graduated partner agent** (or buying agent) creates a payment intent after close.

```bash
curl -X POST "$ARCLYA_BASE_URL/payments/crypto/intent" \
  -H "Content-Type: application/json" \
  -H "X-Arclya-Key: arclya_prod_<partner_key>" \
  -d '{
    "amount": 49.0,
    "network": "base",
    "partner_id": "tp_<partner_id>",
    "deal_id": "first_crypto_sale_001",
    "memo": "Arclya first_crypto_sale_001"
  }'
```

**Save from response:**
- `payment_id` (e.g. `cpay_...`)
- `wallet_address` / `X-Payment-Address` header
- `amount` / `X-Payment-Amount`
- `memo`

**Operator monitor:** Payment appears in ops dashboard as `pending`:

```bash
python scripts/confirm_crypto_payment.py
# or
python scripts/run_first_crypto_sale.py payments
```

---

## Phase 4 — Agent pays on-chain (USDC)

The agent sends **exact USDC amount** to `wallet_address` on the specified network.

**Operator actions while waiting:**
1. Note `payment_id`, `partner_id`, `deal_id`, amount, network
2. Open explorer (e.g. [BaseScan](https://basescan.org)) for your receive address
3. Do **not** confirm until you see a matching incoming USDC transfer

**Tips for first sale:**
- Use **Base** for lowest fees
- Match amount exactly (49.0 USDC for $49.00 intent)
- Include memo in transfer if wallet supports it

Check payment status:

```bash
curl -s "$ARCLYA_BASE_URL/payments/crypto/cpay_<id>" -w "\nHTTP %{http_code}\n"
```

Returns **402** while unpaid, **200** when confirmed.

---

## Phase 5 — Agent submits tx_hash

After on-chain payment:

```bash
curl -X POST "$ARCLYA_BASE_URL/payments/crypto/cpay_<payment_id>/submit" \
  -H "Content-Type: application/json" \
  -d '{"tx_hash": "0x<verified_transaction_hash>"}'
```

**Success criteria:**
- Response `payment.status` = `submitted`
- Ops dashboard `pending_review_count` increases
- `confirm_crypto_payment.py` lists the payment with tx hash

---

## Phase 6 — Operator confirms payment

**Only confirm after verifying the transaction on-chain** (correct amount, correct token USDC, correct destination).

### List pending

```bash
ARCLYA_OPERATOR_KEY=<secret> \
  python scripts/confirm_crypto_payment.py
```

### Confirm

```bash
ARCLYA_OPERATOR_KEY=<secret> \
  python scripts/confirm_crypto_payment.py \
    --confirm cpay_<payment_id> \
    --tx-hash 0x<verified_transaction_hash> \
    --confirmed-by <your_name>
```

Or via wrapper:

```bash
python scripts/run_first_crypto_sale.py confirm \
  --payment-id cpay_<payment_id> \
  --tx-hash 0x<verified_transaction_hash>
```

**Success criteria:**
- CLI prints `Crypto Payment Confirmed`
- `GET /payments/crypto/cpay_<id>` returns 200, `status: confirmed`
- Ops dashboard: `by_status.confirmed` incremented, `confirmed_total_usd` updated

---

## Phase 7 — Verification (ops, audit, attribution)

Run all verification steps after confirmation.

### 1. Payment record

```bash
curl -s "$ARCLYA_BASE_URL/payments/crypto/cpay_<payment_id>" | jq '.payment | {payment_id, status, partner_id, deal_id, amount, network, tx_hash, confirmed_at}'
```

Confirm:
- `status` = `confirmed`
- `partner_id` matches graduated partner
- `deal_id` matches intent
- `tx_hash` present

### 2. Ops dashboard

```bash
curl -s "$ARCLYA_BASE_URL/ops/dashboard" | jq '{
  payments: .payments.by_status,
  confirmed_usd: .payments.confirmed_total_usd,
  recent_graduation: .partners.recent_graduations[0]
}'
```

Or:

```bash
python scripts/run_first_crypto_sale.py verify --payment-id cpay_<payment_id> --partner-id tp_<id> --deal-id first_crypto_sale_001
```

### 3. Audit trail

On the server filesystem (or via deployed logs):

```bash
# Search audit log for confirmation event
grep crypto_payment_confirmed data/audit/audit.jsonl | tail -1 | jq .
```

Expected audit fields:
- `action`: `crypto_payment_confirmed`
- `metadata.payment_id`, `partner_id`, `deal_id`, `tx_hash`, `confirmed_by`

### 4. Partner attribution

Query payments by partner (local/server data):

```bash
grep '"partner_id": "tp_<id>"' data/payments/crypto_payments.jsonl | tail -1 | jq .
```

---

## Full operator workflow (quick reference)

```bash
# 0. Set target instance
export ARCLYA_BASE_URL=https://your-arclya.onrender.com
export ARCLYA_OPERATOR_KEY=<secret>

# 1. Prerequisites
python scripts/run_first_crypto_sale.py check

# 2. Rehearsal (stand-in partner)
python scripts/run_first_crypto_sale.py rehearse
# → note partner_id from output

# 3. Graduate
python scripts/run_first_crypto_sale.py graduate --partner-id tp_<id>

# 4. (Partner) intent → pay USDC → submit tx_hash
#    See Phase 3–5 above

# 5. Operator confirm
python scripts/run_first_crypto_sale.py payments
python scripts/run_first_crypto_sale.py confirm \
  --payment-id cpay_<id> --tx-hash 0x...

# 6. Verify
python scripts/run_first_crypto_sale.py verify \
  --payment-id cpay_<id> --partner-id tp_<id> --deal-id first_crypto_sale_001
```

**Partial automation:** `python scripts/run_first_crypto_sale.py run --partner-id tp_<id>` executes check → rehearse (if no `--skip-rehearse`) → graduate, then prints agent instructions for Phases 3–5.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `crypto_not_configured` (503) | Missing wallet env vars | Set `ARCLYA_CRYPTO_WALLET_*` on Render, redeploy |
| `crypto_disabled` (503) | `ARCLYA_CRYPTO_ENABLED` not `1` | Enable and redeploy |
| Rehearsal exit 1 | Milestones or security blockers | `GET /partners/me/progress`, fix blockers |
| Graduation blocked | `graduation_ready: false` | Complete sandbox milestones; zero EMERGENCY_STOP |
| Payment not in pending list | Wrong instance or not submitted | Confirm `ARCLYA_BASE_URL`; agent must submit tx_hash |
| Confirm fails 401 | Wrong operator key | Match `ARCLYA_OPERATOR_KEY` on server |
| 402 on GET payment after confirm | Confirm failed or wrong payment_id | Re-run confirm; check audit log |

---

## After first sale

- [ ] Document production key handoff process for partner #2
- [ ] Record sale in operator notes (`payment_id`, `tx_hash`, date)
- [ ] Share feedback with partner (UX, timing, unclear errors)
- [ ] Plan automation (on-chain tx verification) before scaling to 10 sales

**First sale complete when:** payment `confirmed`, audit logged, ops dashboard updated, partner attribution verified.