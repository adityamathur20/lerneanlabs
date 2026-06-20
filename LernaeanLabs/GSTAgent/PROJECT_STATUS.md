# GSTAgent — Project Status & Comprehensive Summary

**Last updated:** April 25, 2026  
**Author:** Aditya Mathur (akshuadi96@gmail.com / aditya.mathur96@outlook.com)  
**Purpose:** Complete reference for any Claude session picking up this project

---

## 1. What This Product Is

GSTAgent is a B2B SaaS product for Indian Chartered Accountants (CAs). It automates the monthly GST compliance workflow for small business clients:

1. Fetches the government's GSTR-2B filing from the GST Portal (via a GSP API)
2. Parses the client's Tally ERP accounting export (XML)
3. Runs deterministic reconciliation in Python — finds mismatches, cancelled GSTINs, at-risk ITC
4. Calls Claude (Haiku + Sonnet) to produce a WhatsApp message for the business owner and a technical CA report
5. Delivers the outputs via n8n automation

**Business model:** ₹2,499/month per client, ~88% margin at 50 clients. Solo-operated. CAs are the customer; business owners are end recipients.

---

## 2. Architecture

```
Tally XML exports  →  TallyParser        → [SalesVoucher, PurchaseVoucher]
GSTR-2B JSON       →  GSTR2BReader       → O(1) indexed by (supplier_gstin, invoice_number)
                                ↓
                          Reconciler      (pure Python, deterministic, no AI)
                                ↓ ReconciliationResult
                          PromptBuilder   (converts result → system+user prompt strings)
                                ↓
                          GSTClaudeAgent  → ClaudeAPIClient (urllib, no SDK)
                          ├── Haiku       → WhatsApp message (≤250 words, plain language)
                          ├── Sonnet      → CA technical report
                          └── Haiku       → issues_structured JSON
                                ↓
                    pipeline_server.py    (Flask HTTP wrapper)
                                ↓
                          n8n workflow    → WhatsApp + CA email delivery
                                ↓
                          Supabase        → filing_runs, reconciliation_results, alerts_sent
```

**Core design principle:** Claude never does arithmetic. All numbers are computed in `reconciler.py` and injected as literals into prompts. Claude only handles language and phrasing.

---

## 3. Codebase Map

```
GSTAgent/
├── scripts/
│   ├── tally_parser.py       # Parses Tally ERP XML (lxml). Produces SalesVoucher, PurchaseVoucher dataclasses.
│   ├── gstr2b_reader.py      # Loads GSTR-2B JSON, builds O(1) index. Supports file + API response.
│   ├── reconciler.py         # 4-step reconciliation: GSTIN validation → ITC matching → HSN check → tax calc
│   ├── prompt_builder.py     # Pure data transformation: ReconciliationResult → (system_prompt, user_prompt)
│   ├── claude_agent.py       # Anthropic API client (urllib). Haiku for WhatsApp, Sonnet for CA report.
│   ├── cost_logger.py        # Tracks per-call tokens + USD/INR cost, writes to cost_log.jsonl
│   ├── gsp_client.py         # WhiteBooks GSP API wrapper + Supabase REST client
│   ├── mock_tally.py         # Per-client mock Tally + GSTR-2B data for dry_run (10 industry clients)
│   ├── run_pipeline.py       # Main CLI entry point — runs full pipeline, outputs single JSON line
│   ├── pipeline_server.py    # Flask HTTP server wrapping run_pipeline.py (called by n8n Cloud)
│   ├── run_agent_test.py     # Manual test: runs full pipeline on Mehta Textile testcase
│   └── run_testcase.py       # Reconciliation-only test (no Claude)
├── tests/
│   ├── test_reconciler.py
│   ├── test_tally_parser.py
│   ├── test_gstr2b_reader.py
│   ├── test_claude_agent.py  # dry_run mode — no API key needed
│   ├── test_gsp_client.py    # dry_run by default; --live for sandbox
│   └── run_tests.py
├── supabase/
│   ├── schema.sql            # Full DB schema — idempotent, safe to re-run
│   ├── seed_test_clients.sql # 11 test clients + GSP sessions
│   └── secrets_setup.sql     # Vault key setup for GSP token encryption
├── n8n/
│   ├── workflow.json         # n8n Cloud workflow (import via n8n UI)
│   └── workflow_local.json   # Local n8n variant
├── testcases/
│   └── mehta_textile_oct2024/  # Full testcase: Tally XML + GSTR-2B JSON + config
└── README.md                 # Setup + demo guide
```

---

## 4. Phase Status

### Phase 1 — COMPLETE (as of ~April 17, 2026)

All core Python pipeline modules written and tested. 234 tests passing.

| Module | Status | Notes |
|--------|--------|-------|
| `tally_parser.py` | Done | Parses sales + purchase daybook XML via lxml |
| `gstr2b_reader.py` | Done | Indexes by (supplier_gstin, invoice_num) |
| `reconciler.py` | Done | 4-step deterministic reconciliation |
| `prompt_builder.py` | Done | Converts result to prompt strings |
| `claude_agent.py` | Done | Haiku + Sonnet, dry_run, graceful fallback |
| `cost_logger.py` | Done | Per-call token tracking, USD + INR cost |
| `mock_tally.py` | Done | 10-client industry-realistic mock data |
| All tests | Done | 234 passing, dry_run throughout |

### Phase 2 — MOSTLY COMPLETE (as of April 25, 2026)

| Component | Status | Notes |
|-----------|--------|-------|
| `gsp_client.py` | Done | Full WhiteBooks sandbox integration (see Section 6) |
| `run_pipeline.py` | Done | CLI entry point, reads Supabase for client config |
| `pipeline_server.py` | Done | Flask wrapper for n8n calls |
| Supabase schema | Done | 5 tables + RLS + 3 RPC functions + Vault encryption |
| Supabase seed data | Done | 11 test clients seeded |
| n8n workflow | Done | Importable JSON, calls pipeline server |
| Demo setup | Ready | dry_run=True, ngrok, Supabase fake clients |

### Phase 3 — NOT STARTED

- CA PDF report generator (currently just plain text)
- Email delivery integration (SendGrid or similar)
- Production-tuned Claude prompts (Hindi/Gujarati language support drafted but not production-tested)

### Phase 4 — NOT STARTED

- MCP server wrapper for on-premise CA firms

### Phase 5 — NOT STARTED

- First paying client: Razorpay billing at ₹2,499/month

---

## 5. Test Scenario — Mehta Textile (Oct 2024)

Located in `testcases/mehta_textile_oct2024/`. Three intentional compliance issues:

1. **Cancelled GSTIN** (`24AAFVT9999Z1Z9`) — sale recorded as B2B but buyer's GSTIN is cancelled; must be reclassified to B2CS
2. **Missing supplier ITC** — `24AABSF3333C1Z4` (Fabric World) didn't file GSTR-1; ₹5,040 ITC at risk, should not be claimed
3. **HSN mismatch** — HSN `5407` (synthetic fabric) recorded in Tally instead of `5208` (cotton weave) for a cotton product

Expected net GST payable: **₹31,440** (conservative, excluding at-risk ITC).

This testcase is the default when `dry_run=True` and `gstin=24AABMT1234C1Z5`.

### Mock data for all other clients (`mock_tally.py`)

10 additional dummy clients across 5 industries with deterministic scenario assignment:
- `GSTIN character sum % 4` → scenario 0 (CRITICAL), 1 (WARNING), 2 (WARNING), 3 (CLEAN)
- Each client gets industry-realistic HSN codes, item names, supplier/buyer GSTINs

---

## 6. GSP API Integration — WhiteBooks

### What a GSP is

In India, businesses access the GST Portal programmatically only via a licensed GSP (GST Suvidha Provider). GSTAgent uses **WhiteBooks** (operated by Tera Software Limited, also branded as MasterGST) as the GSP.

### Credentials obtained

- **Client ID:** `GSTSda10abb9-ce3a-4c80-ad88-f4552aba03c0`
- **Client Secret:** `GSTSa214867f-eef4-4161-af95-6264b6365bad`
- **Email:** `aditya.mathur96@outlook.com`
- **Sandbox URL:** `https://apisandbox.whitebooks.in`

Stored in `gsp_client.py` as environment variables (`GSP_CLIENT_ID`, `GSP_CLIENT_SECRET`, `GSP_EMAIL`).

### Auth flow (two-step)

```
GET /authentication/otprequest?email=...
  Headers: client_id, client_secret, gst_username, state_cd, ip_address
  → resp["header"]["txn"]  (OTP transaction ID)

GET /authentication/authtoken?otp=575757&email=...
  Headers: same + txn (from step 1)
  → resp["header"]["txn"]  (session token — use in all subsequent calls)
```

Sandbox OTP is always `575757`. Sessions are valid for ~6 hours; max 1 concurrent session per account.

### Key bugs fixed during development

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `Client_id` header capitalization | `urllib` auto-capitalizes headers | Switched all HTTP calls to `http.client.HTTPSConnection` |
| Empty response body | Missing `?email=` query param | Added `urllib.parse.quote(email)` to all URL parameters |
| `status` vs `status_cd` | WhiteBooks uses `status_cd: "1"` for success | Updated all checks from `status` to `status_cd` |
| txn in wrong location | Was reading `resp["txn"]`; actually at `resp["header"]["txn"]` | Fixed extraction path |
| Wrong `state_cd` | Derived from real client GSTIN (e.g. `24` for Gujarat) but sandbox GSTIN is Tamil Nadu (`33`) | When `GSP_SANDBOX_GSTIN` env var set, derive `state_cd` from that, not the real GSTIN |
| AUTH403 session limit | Multiple test runs exhausted session slots | Added `logout()` method; must call at end of every live test |

### Sandbox GSTIN credentials (from WhiteBooks PDF)

`SANDBOX_CREDENTIALS` dict in `gsp_client.py` maps each 2-digit state code to `(sandbox_gstin, gsp_username, gsp_password)`. All 33 states mapped.

Account-specific credentials (override PDF defaults via env vars):
- `GSP_SANDBOX_GSTIN=33AAGCB1286Q2ZA` — Tamil Nadu buyer sandbox account
- `GSP_GST_USERNAME=TN_NT2.152384`
- Password: not stored in code (Bvm@123456 for PDF defaults; account-specific is different)

### GSTR-2B sandbox limitation (important)

The sandbox follows real GST calendar dates:

- **GSTR-2B for month M** is only available **after the 14th of month M+1** (after suppliers' GSTR-1 filing deadline)
- Periods where GSTR-3B is already auto-filed are locked (`IMS2B007` error)
- As of April 25, 2026: March 2026 is locked, April 2026 not yet available until May 14

**Conclusion: live GSTR-2B fetching cannot be tested in sandbox before May 14, 2026.** Use `dry_run=True` for all demos and development until then.

### GSTR-1 filing attempt (April 2026)

To generate test GSTR-2B data, a GSTR-1 was filed from Maharashtra sandbox (`27AAGCB1286Q1Z4`) with buyer = Tamil Nadu sandbox (`33AAGCB1286Q2ZA`) for period `042026`:

- Reference ID: `ef7f0b01-efde-4ad5-ab39-6f26c82b8059`
- Status: `status_cd: 1` (accepted)
- Invoice: INV-2026-001, ₹1,20,000 taxable, 12% IGST = ₹14,400, inter-state (pos=33)

The gen2b call for April 2026 returned `IMS2B009` ("generate 2B after 14/05/2026") — this is expected behavior. The GSTR-2B will become fetchable after May 14.

### Other GSPs evaluated

- **IRIS Zircon** (support@irisgst.com) — identified as backup option, 15-day trial available; not pursued yet
- **Sandbox.co.in** — evaluated, does not provide GSTR-2B reconciliation API
- **MasterGST** — same company as WhiteBooks (both Tera Software Limited); no separate application needed

---

## 7. Supabase Schema

### Tables

| Table | Purpose |
|-------|---------|
| `clients` | One row per subscribing business. GSTIN, firm name, owner, CA details, subscription status, Razorpay subscription ID |
| `filing_runs` | One row per monthly run. Status, reconciliation result summary, cost, timestamps |
| `reconciliation_results` | Full pipeline output as JSONB. WhatsApp message, CA report, issues_structured, model names used |
| `alerts_sent` | Delivery audit log for WhatsApp + email alerts |
| `gsp_sessions` | Encrypted GSP session tokens (pgp_sym_encrypt via Supabase Vault) |

### RPC functions (called by `gsp_client.py`)

- `upsert_gsp_session` — write/refresh encrypted GSP token
- `get_gsp_token` — read + decrypt token
- `gsp_session_status` — check if valid session exists (no token returned)

All use `security definer` so the encryption key never leaves Postgres.

### Test clients seeded

11 clients across 5 industries:
- Textiles: Mehta Textile Traders, Patel Cotton Mills, Shah Synthetics
- Pharma: Cadila Pharma Distributors, Apollo Medical Supplies
- Electronics: Bengaluru Electronics Hub, Infotech Components
- Food/FMCG: Delhi Spice Traders, Aggarwal Foods
- Auto Parts: Chennai Auto Ancillaries, Tamil Nadu Tyres & Parts (cancelled)

---

## 8. n8n Workflow

The workflow (`n8n/workflow.json`) automates the monthly run for all clients:

1. **Schedule Trigger** — fires monthly (configurable)
2. **Calculate Period** — derives MMYYYY from current date
3. **Fetch Active Clients** — queries Supabase for `subscription_status in (active, trial)`
4. **Loop over clients** — one pipeline run per client
5. **Check GSP Session** — calls `gsp_session_status` RPC; triggers OTP refresh if expired
6. **Run Pipeline** — `POST {PIPELINE_SERVER_URL}/run` with `gstin`, `period`, `dry_run`
7. **Store Result** — writes to `reconciliation_results` and updates `filing_runs`
8. **Send WhatsApp** — (stub node, not yet wired to live provider)
9. **Send CA Email** — (stub node, not yet wired to SendGrid/SMTP)

The `pipeline_server.py` Flask server is the bridge between n8n and the Python pipeline. n8n Cloud cannot execute Python directly, hence the HTTP wrapper.

---

## 9. Demo Setup (Current State)

The demo uses `dry_run=True` with seeded Supabase clients. No live API calls required.

### Steps to run demo
```bash
# Terminal 1 — pipeline server
cd /path/to/GSTAgent
export PIPELINE_SECRET=demo-secret-123
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/pipeline_server.py

# Terminal 2 — expose to n8n Cloud
ngrok http 5001

# In n8n: set PIPELINE_SERVER_URL to ngrok URL, trigger workflow manually
```

### What the demo produces

For each of the 11 seeded clients, the pipeline returns:
- `reconciliation_status`: CRITICAL / ISSUES_FOUND / CLEAN (deterministic per GSTIN)
- `whatsapp_message`: plain-language alert for business owner
- `ca_report`: structured technical report for CA
- `issues_structured`: JSON array of all issues
- `cost_usd` + `cost_inr`: per-run Claude API cost

---

## 10. Environment Variables Reference

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...

# WhiteBooks GSP (live mode only)
GSP_CLIENT_ID=GSTSda10abb9-ce3a-4c80-ad88-f4552aba03c0
GSP_CLIENT_SECRET=GSTSa214867f-eef4-4161-af95-6264b6365bad
GSP_EMAIL=aditya.mathur96@outlook.com

# Optional: override sandbox GSTIN (Tamil Nadu buyer account)
GSP_SANDBOX_GSTIN=33AAGCB1286Q2ZA
GSP_GST_USERNAME=TN_NT2.152384

# Pipeline server
PIPELINE_SECRET=your-secret-token
```

---

## 11. What's Working Right Now

- Full Python pipeline end-to-end (`dry_run=True`)
- All 234 tests pass
- 11 test clients seeded in Supabase
- Pipeline server starts and handles requests
- n8n workflow JSON is importable and structurally complete
- WhiteBooks authentication confirmed working (`status_cd: 1`)
- GSTR-1 successfully filed from sandbox (`reference_id: ef7f0b01-efde-4ad5-ab39-6f26c82b8059`)

---

## 12. What's Pending / Next Steps

### Immediate (demo readiness)

- [ ] Wire ngrok URL into n8n workflow and do a full end-to-end demo run
- [ ] Verify all 11 clients produce correct output (different scenarios per client)
- [ ] Test `pipeline_server.py` health endpoint and `/run` endpoint manually

### Short term (post-demo)

- [ ] Live GSTR-2B fetch from WhiteBooks — available after May 14, 2026 (April 2026 period)
- [ ] WhatsApp delivery — integrate with WhatsApp Business API (Twilio or Meta direct)
- [ ] CA email delivery — integrate with SendGrid or similar
- [ ] PDF report generation — convert CA report text to PDF

### Medium term

- [ ] First real CA client onboarding — get real Tally XML + GSTIN, run live pipeline
- [ ] Razorpay billing integration at ₹2,499/month
- [ ] Hindi/Gujarati language support (scaffolding exists in `claude_agent.py`)
- [ ] Handle multi-page GSTR-2B (`page` parameter in WhiteBooks API)

### Long term

- [ ] MCP server wrapper for on-premise CA firms (Phase 4)
- [ ] Scale to 50+ clients

---

## 13. Key Decisions & Why

| Decision | Rationale |
|----------|-----------|
| Reconciliation in Python, not Claude | Claude hallucinates numbers; Python is deterministic and auditable |
| Two model tiers (Haiku + Sonnet) | Haiku for short/cheap (WhatsApp, JSON), Sonnet for detailed CA narrative |
| `http.client` not `urllib` for GSP | `urllib` auto-capitalizes header names; WhiteBooks is case-sensitive |
| `dry_run=True` for all tests | Full pipeline testability with zero API cost or credentials |
| Supabase for DB | Generous free tier, built-in RLS, Vault for secret encryption, REST API (no ORM needed) |
| Flask pipeline server | n8n Cloud cannot execute local Python; HTTP bridge is the simplest solution |
| GSP sessions encrypted in Vault | OTP tokens are sensitive; key never leaves Postgres |
| ngrok for demo | Fastest way to expose local server to n8n Cloud without a deployment |
