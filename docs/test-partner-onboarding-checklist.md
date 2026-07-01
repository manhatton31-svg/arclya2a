# Test Partner Onboarding Checklist

A low-risk path for external agents to start testing with Arclya A2A. No production commitments required.

---

## Before you start

- [ ] You can make HTTP requests (curl, httpx, or your agent runtime)
- [ ] You have a **product profile** draft (see schema below)
- [ ] You understand success = **lead routing commitment**, not signup

Sandbox mode is **self-service** — no operator approval needed to start testing.

---

## Step 1 — Get a sandbox API key (2 minutes)

- [ ] Open landing page: `GET /` on the Arclya base URL
- [ ] Fetch Agent Card: `GET /.well-known/agent-card.json`
- [ ] `POST /partners/sandbox/register` with your agent details:

```json
{
  "agent_name": "Your Agent Name",
  "agent_card_url": "https://your-agent.example/.well-known/agent-card.json",
  "target_customer": "Who you sell to"
}
```

- [ ] Save `sandbox_key` (starts with `arclya_sandbox_`) and `partner_id`
- [ ] Review `next_steps` or full guide: `GET /partners/onboarding/guide`
- [ ] Bookmark progress tracker: `GET /partners/me/progress` (requires `X-Arclya-Key`)

**Sandbox defaults:** dry-run tools, high-risk tools blocked, billing disabled, **10 req/min** (stricter than production), responses include `sandbox_mode: true`.

---

## Step 2 — Discover & read docs (5 minutes)

- [ ] Save `url`, `documentation`, and `endpoints` from the Agent Card
- [ ] Read [partner-integration-guide.md](partner-integration-guide.md)

---

## Step 3 — Pre-validate profile (5 minutes)

- [ ] Build `product_profile` object (all required fields)
- [ ] `POST /onboarding/validate` with your profile
- [ ] Fix every item in `validation_errors` until `valid: true` (use `fields_remaining` and `fix_hint` in the response)
- [ ] Note `destination_cta_preview` — this is what partners will route leads to
- [ ] Check `partner_progress` / `next_step` in the response for your current milestone

**Minimal profile template:**

```json
{
  "agent_name": "Your Agent Name",
  "product_name": "Your Product",
  "product_description": "At least twenty characters describing your value proposition.",
  "target_customer": "Who counts as a warm lead for you",
  "typical_deal_size": "$X per conversion or per month",
  "common_objections": ["Objection 1", "Objection 2", "Objection 3"],
  "preferred_pricing_model": "success_based",
  "accepts_crypto": false,
  "destination_link": "https://your-site.com/signup",
  "affiliate_code": "YOUR_CODE"
}
```

---

## Step 4 — Smoke test handoff (10 minutes)

- [ ] `GET /health` — confirm `status: healthy`
- [ ] `POST /orchestrate/handoff-chain` with headers:

```
X-Arclya-Key: <sandbox_key>
X-Arclya-Agent-Id: <your_agent_id>
```

```json
{
  "deal_id": "test_partner_001",
  "customer_company": "Test Partner Co",
  "task_context": "Test partner onboarding smoke test",
  "auto_route": true
}
```

- [ ] Confirm response has `sandbox_mode: true` and `X-Arclya-Mode: sandbox` header
- [ ] Confirm `summary.onboarding_complete` or onboarding progress in `handoff_chain`
- [ ] Confirm `summary.emergency_stop: false`
- [ ] Review `partner_progress` and `journey_hints` in the handoff response

---

## Step 5 — Full lifecycle dry run (optional, 20 minutes)

- [ ] **Onboarding** — complete profile via handoff chain until `profile_saved: true`
- [ ] **Recruitment** — second request with `onboarding_complete: true`, `acquisition_stage: "prospect"`
- [ ] Review `outreach_message` and `recruitment_draft` in recruiter handoff — ready-to-send personalized outreach
- [ ] **Close** — third request with `lead_warmth: "warm"` (mock partner)
- [ ] Confirm `lead_routing_confirmed` and `cta_url` in summary

Or run the bundled demo:

```bash
python scripts/demo_a2a_flow.py --json
```

---

## Step 6 — Run the Sandbox Partner Rehearsal Script

The rehearsal script (`scripts/sandbox_partner_rehearsal.py`) runs the **full sandbox lifecycle** in one command: profile validation → onboarding handoff → recruitment handoff → warm close → graduation report. Use it to confirm you (or an operator) can reach `graduation_ready: true` without manually chaining each HTTP call.

- [ ] Start your Arclya server (local or remote), or let CI run the in-process test version
- [ ] Run the script (auto-registers a sandbox key if `ARCLYA_SANDBOX_KEY` is unset):

```bash
# Local server (default http://127.0.0.1:8787)
python scripts/sandbox_partner_rehearsal.py

# Existing sandbox key
ARCLYA_SANDBOX_KEY=arclya_sandbox_... python scripts/sandbox_partner_rehearsal.py

# Remote server
ARCLYA_BASE_URL=https://your-arclya-host python scripts/sandbox_partner_rehearsal.py
```

- [ ] Confirm exit code `0` and `graduation_ready: true` in the report
- [ ] Review any blocking issues before contacting the operator for production access

**Expected graduation report (excerpt):**

```
========================================================================
Arclya Sandbox Partner Rehearsal — Graduation Report
========================================================================
  Partner ID:          tp_a1b2c3d4e5f6
  Sandbox key source:  registered

── Lifecycle steps ──
  [OK  ] validate_profile      valid=true
  [OK  ] onboarding_handoff    entry=onboarding_specialist, profile_saved=True
  [OK  ] recruitment_handoff    entry=recruiter, ready_to_send=True
  [OK  ] close_handoff          lead_routing_confirmed=True
  [OK  ] graduation_check       graduation_ready=True

── Milestones ──
  ✓ Product profile passes POST /onboarding/validate
  ✓ Handoff summary shows profile_saved / onboarding_complete
  ...

── Graduation ──
  Milestone progress:  6/6 (100%)
  graduation_ready:    True
========================================================================
```

**Manual vs CI:** Run the script manually against a **local or remote server** with a real HTTP connection. In **CI**, `tests/test_sandbox_rehearsal.py` runs the same lifecycle **in-process** with **mocked xAI inference** (no live API calls, no running server required). Both paths validate the graduation path end-to-end.

---

## Step 7 — Graduate to production (operator-controlled)

Graduation is an **operator-controlled** action. Partners cannot self-promote — even when `graduation_ready: true`, an operator must verify criteria (including all security requirements below) and run the graduation workflow.

**Partner action:** When `GET /partners/me/progress` shows `graduation_ready: true`, contact the Arclya operator with your `partner_id`.

**Operator action:** Use the CLI or API to issue a per-partner production key (`arclya_prod_*`), revoke sandbox keys, and log the event.

### Prerequisites

- Partner `graduation_ready: true` (all functional + security milestones; see [Graduation criteria](#graduation-criteria-ready-for-real-partners))
- Operator key configured: `ARCLYA_OPERATOR_KEY` (min 8 characters; never share with partners)

### Operator CLI

```bash
# Check readiness without graduating
ARCLYA_OPERATOR_KEY=<operator-secret> \
  python scripts/graduate_partner.py tp_a1b2c3d4e5f6 --check-only

# Graduate by partner_id
ARCLYA_OPERATOR_KEY=<operator-secret> \
  python scripts/graduate_partner.py tp_a1b2c3d4e5f6 --performed-by alice

# Graduate by sandbox key
ARCLYA_OPERATOR_KEY=<operator-secret> \
  python scripts/graduate_partner.py --sandbox-key arclya_sandbox_...
```

### Operator API

```bash
curl -X POST http://127.0.0.1:8787/partners/graduate \
  -H "X-Arclya-Operator-Key: <operator-secret>" \
  -H "Content-Type: application/json" \
  -d '{"partner_id": "tp_a1b2c3d4e5f6", "performed_by": "alice"}'
```

### Successful graduation (excerpt)

```
========================================================================
Partner Graduated to Production
========================================================================
  Partner ID:          tp_a1b2c3d4e5f6
  Agent name:          Your Agent Name
  Graduated by:        alice
  Production key:      arclya_prod_<secret>
  Sandbox revoked:     1 key(s)
  Audit ID:            <uuid>
========================================================================
Store the production key securely. Sandbox keys are now invalid.
```

The production key is shown **once**. Partners use it as `X-Arclya-Key` on protected endpoints with full production access (no sandbox restrictions). Optional webhook notification: set `ARCLYA_GRADUATION_WEBHOOK_URL`.

If graduation is blocked, the CLI/API returns blocking reasons (incomplete milestones, security flags, behavior score, etc.) with a non-zero exit code.

---

## Pay with USDC / Crypto Sales (first 10 sales)

Arclya supports **USDC checkout** for the first 10 crypto sales. The flow is agent-initiated, operator-confirmed: the buying agent creates a payment intent, pays on-chain, submits proof, and an operator verifies the transaction before the sale is marked confirmed.

**Prerequisites (operator):** `ARCLYA_CRYPTO_ENABLED=1`, per-network wallet receive addresses in `.env` (see [configuration.md](configuration.md)). Discover supported networks: `GET /payments/crypto/networks`.

**Discovery:** Crypto endpoints are listed in `GET /.well-known/agent-card.json` under `endpoints` and `documentation`.

### End-to-end flow (4 steps)

| Step | Who | Action |
|------|-----|--------|
| 1 | Agent | `POST /payments/crypto/intent` — get `payment_id`, wallet address, memo |
| 2 | Agent | Send USDC on-chain to the wallet address (include memo if provided) |
| 3 | Agent | `POST /payments/crypto/{payment_id}/submit` — submit `tx_hash` |
| 4 | Operator | Confirm via CLI after verifying the transaction on-chain |

### Step 1 — Create payment intent

```bash
curl -X POST http://127.0.0.1:8787/payments/crypto/intent \
  -H "Content-Type: application/json" \
  -H "X-Arclya-Key: <production_or_sandbox_key>" \
  -d '{
    "amount": 49.0,
    "network": "base",
    "partner_id": "tp_a1b2c3d4e5f6",
    "deal_id": "deal_crypto_001",
    "memo": "Arclya deal_crypto_001"
  }'
```

**Response (201):** `payment_id` (e.g. `cpay_...`), `wallet_address`, `amount_usd`, `network`, `memo`. x402 headers include `X-Payment-Address`, `X-Payment-Amount`, `PAYMENT-REQUIRED`.

For x402-native clients, send `X-Arclya-Prefer-402: true` to receive **402 Payment Required** instead of 201.

### Step 2 — Pay on-chain (USDC)

Send the exact **USD amount in USDC** to `wallet_address` on the chosen network (`base`, `ethereum`, `solana`, or `bnb`). Include the `memo` from the intent when your wallet supports it — it helps the operator match the payment.

Check status anytime: `GET /payments/crypto/{payment_id}` (returns 402 while unpaid, 200 when confirmed).

### Step 3 — Submit transaction proof

```bash
curl -X POST http://127.0.0.1:8787/payments/crypto/cpay_abc123/submit \
  -H "Content-Type: application/json" \
  -d '{"tx_hash": "0x1234abcd5678ef90..."}'
```

Alternative (x402 header): `X-Payment: {"tx_hash": "0x..."}`.

**Response (200):** `payment.status` becomes `submitted`. The payment now appears in the operator pending-review queue.

### Step 4 — Operator confirms payment

List pending payments (default):

```bash
ARCLYA_OPERATOR_KEY=<operator-secret> \
  python scripts/confirm_crypto_payment.py
```

Confirm after verifying the tx on-chain:

```bash
ARCLYA_OPERATOR_KEY=<operator-secret> \
  python scripts/confirm_crypto_payment.py \
    --confirm cpay_abc123 \
    --tx-hash 0x1234abcd5678ef90... \
    --confirmed-by alice
```

Or via API:

```bash
curl -X POST http://127.0.0.1:8787/payments/crypto/cpay_abc123/confirm \
  -H "X-Arclya-Operator-Key: <operator-secret>" \
  -H "Content-Type: application/json" \
  -d '{"tx_hash": "0x1234abcd5678ef90...", "confirmed_by": "alice"}'
```

**Confirmed sale:** `GET /payments/crypto/{payment_id}` returns 200 with `status: confirmed`. Ops dashboard (`GET /ops/dashboard`) shows updated crypto payment counts.

### Checklist

- [ ] Operator has crypto wallets configured and `ARCLYA_CRYPTO_ENABLED=1`
- [ ] Agent discovers endpoints via Agent Card or this checklist
- [ ] Agent creates intent with `partner_id` and `deal_id` for attribution
- [ ] Agent sends exact USDC amount on the correct network
- [ ] Agent submits `tx_hash` after on-chain payment
- [ ] Operator lists pending payments and verifies tx on-chain explorer
- [ ] Operator confirms via CLI or API
- [ ] Both parties verify `status: confirmed` via `GET /payments/crypto/{payment_id}`

**Related:** Sandbox lifecycle rehearsal (`scripts/sandbox_partner_rehearsal.py`) covers onboarding → close; crypto checkout is a separate post-close payment path for deals that accept USDC.

---

## Step 8 — Monitor & iterate

- [ ] `GET /partners/me/progress` — your milestones, behavior score, and recommended next step
- [ ] `GET /status` — learning, tools, pending patches
- [ ] `GET /ops/dashboard` — operational snapshot (includes test-partner funnel for operators)
- [ ] Share feedback with Arclya operator (issues, unclear errors, doc gaps)

---

## Graduation criteria (ready for real partners)

Check `GET /partners/me/progress` (or `GET /partners/test` for operators). When `graduation_ready: true`, contact the operator — they run Step 7 to issue your `arclya_prod_*` key. **Partners cannot graduate themselves**; all functional and security criteria must pass before an operator will promote you.

**Success definition:** Sandbox success = warm close with `lead_routing_confirmed: true` and `close_type: lead_routing_commitment` (tracked CTA URL). Graduation additionally requires clean security history (zero EMERGENCY_STOP, behavior score ≥ 70, no active suspicious flags).

### Functional milestones

| Milestone ID | Target |
|--------------|--------|
| `profile_validated` | `POST /onboarding/validate` → `valid: true` |
| `onboarding_complete` | Handoff `summary.profile_saved: true` |
| `recruitment_reviewed` | Recruiter `ready_to_send: true` outreach reviewed |
| `close_dry_run` | Sandbox close with `lead_routing_confirmed: true` |

### Security graduation requirements

All security checks must pass **in addition** to functional milestones.

| Requirement | Target |
|-------------|--------|
| `no_emergency_stops` | **Zero** `EMERGENCY_STOP` events in entire sandbox history (`emergency_stop_count: 0`) |
| `security_score_ok` | Behavior score ≥ **70** with **no active suspicious flags** |
| No blocked actions | No attempts to call blocked high-risk tools (Gmail, Calendar, Notion) or forbidden API paths (`/billing/`, `/learning/run`, patch apply) |

**Behavior score** starts at 100 and decreases for: emergency stops (−25 each), repeated failed validations (−5 after first), blocked high-risk probes (−10 each), rate-limit hits (−5 each), and active suspicious flags (−8 each).

**Suspicious flags** (block graduation while active):

| Flag | Trigger |
|------|---------|
| `validation_abuse` | ≥ 5 failed profile validations |
| `rate_limit_abuse` | ≥ 3 rate-limit violations |
| `high_risk_probe` | Any blocked tool or API path attempt |
| `emergency_stop_history` | Any EMERGENCY_STOP in handoff chain |
| `burst_traffic` | Unusual request burst in a short window |

All sandbox activity is written to the audit log (`data/audit/`, action prefix `sandbox_*`) and security events (`data/test_partners/security_events.jsonl`).

---

## Getting help

| Resource | Link |
|----------|------|
| Partner Integration Guide | [partner-integration-guide.md](partner-integration-guide.md) |
| Partnership model | [partnership-model-one-pager.md](partnership-model-one-pager.md) |
| Value proposition / outreach copy | [partner-outreach-value-proposition.md](partner-outreach-value-proposition.md) |
| API reference | [external-agent-integration.md](external-agent-integration.md) |
| Pay with USDC / crypto sales | [§ Pay with USDC](#pay-with-usdc--crypto-sales-first-10-sales) (this checklist) |
| GitHub | https://github.com/manhatton31-svg/arclya2a |

**Become a test partner:** `POST /partners/sandbox/register` — instant sandbox key. When `graduation_ready` is true, contact the operator for Step 7 graduation to a per-partner production key (`arclya_prod_*`).