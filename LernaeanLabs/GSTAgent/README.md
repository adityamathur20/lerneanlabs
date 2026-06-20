# GSTAgent

Automated GST compliance for Indian SMBs. Reconciles Tally ERP accounting data against government GSTR-2B filings and generates CA reports + WhatsApp alerts via Claude AI.

## How it works

```
Tally XML exports  →  TallyParser
GSTR-2B (API/mock) →  GSTR2BReader
                            ↓
                       Reconciler  (pure Python, deterministic)
                            ↓ ReconciliationResult
                       GSTClaudeAgent
                       ├── Haiku  → WhatsApp message (≤250 words)
                       ├── Sonnet → CA technical report
                       └── Haiku  → issues_structured JSON
                            ↓
                    pipeline_server.py → n8n workflow → WhatsApp + Email
```

---

## Prerequisites

- Python 3.10+
- `lxml` (`pip3 install lxml`)
- `flask` (`pip3 install flask`) — for the pipeline server
- ngrok — to expose local server to n8n Cloud
- Anthropic API key — for live Claude calls (not needed for dry-run)
- Supabase project — for client data

---

## Environment variables

Add these to `~/.zshrc` and run `source ~/.zshrc`:

```bash
# Anthropic (Claude)
export ANTHROPIC_API_KEY="sk-ant-..."

# Supabase
export SUPABASE_URL="https://xxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."

# WhiteBooks GSP API (needed only for live GSTR-2B fetching, not dry-run)
export GSP_CLIENT_ID="GSTSda10abb9-..."
export GSP_CLIENT_SECRET="GSTSa214867f-..."
export GSP_EMAIL="your@email.com"

# Pipeline server auth token (you choose this value)
export PIPELINE_SECRET="your-secret-token"
```

---

## Running locally

### 1. Quick test — reconciliation only (no API key needed)
```bash
cd scripts
python3 run_testcase.py
```

### 2. Full pipeline with Claude (dry-run, no API key needed)
```bash
cd scripts
python3 run_pipeline.py --gstin 24AABMT1234C1Z5 --period 102024 --dry-run
```

### 3. Full pipeline with live Claude calls
```bash
cd scripts
python3 run_pipeline.py --gstin 24AABMT1234C1Z5 --period 102024
```

### 4. Run all tests
```bash
cd tests
python3 run_tests.py
```

---

## Demo setup (n8n Cloud + dry-run)

This is the recommended way to demo the product. Uses mock GSTR-2B data and Supabase client records — no live GSP API calls required.

### Step 1 — Seed Supabase with test clients

Run `supabase/seed_test_clients.sql` in the Supabase Dashboard SQL Editor.

This adds 11 test clients (Mehta Textile + 10 dummy firms across textiles, pharma, electronics, food, auto parts) plus valid GSP sessions for all active/trial clients.

### Step 2 — Start the pipeline server

Open **Terminal 1**:
```bash
cd /path/to/GSTAgent
export PIPELINE_SECRET=demo-secret-123
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/pipeline_server.py
```

The server starts on `http://0.0.0.0:5001`. You should see:
```
GSTAgent Pipeline Server
Listening on http://0.0.0.0:5001
Auth: enabled (X-Pipeline-Secret header)
```

Test it's alive:
```bash
curl http://localhost:5001/health
# → {"status": "ok", "service": "gstagent-pipeline"}
```

### Step 3 — Expose to n8n Cloud via ngrok

Open **Terminal 2**:
```bash
ngrok http 5001
```

Copy the `https://xxxx.ngrok-free.app` URL from the ngrok output.

### Step 4 — Configure n8n

In your n8n workflow, set these variables:
- `PIPELINE_SERVER_URL` → `https://xxxx.ngrok-free.app`
- `PIPELINE_SECRET` → `demo-secret-123`

The workflow's HTTP Request node calls:
```
POST {PIPELINE_SERVER_URL}/run
Header: X-Pipeline-Secret: {PIPELINE_SECRET}
Body:   {"gstin": "...", "period": "102024", "dry_run": true}
```

### Step 5 — Trigger the workflow

Trigger n8n manually for any client GSTIN from the seeded list. The pipeline returns a JSON with `whatsapp_message`, `ca_report`, `reconciliation_status`, `issue_count`, and cost.

---

## Project structure

```
GSTAgent/
├── scripts/
│   ├── run_pipeline.py       # Main entry point (called by n8n or CLI)
│   ├── pipeline_server.py    # Flask HTTP wrapper around run_pipeline.py
│   ├── tally_parser.py       # Parses Tally ERP XML daybooks
│   ├── gstr2b_reader.py      # Loads + indexes GSTR-2B JSON
│   ├── reconciler.py         # 4-step deterministic reconciliation
│   ├── prompt_builder.py     # Converts ReconciliationResult → prompts
│   ├── claude_agent.py       # Calls Anthropic API (Haiku + Sonnet)
│   ├── gsp_client.py         # WhiteBooks GSP API + Supabase client
│   ├── mock_tally.py         # Mock Tally data generator for dry-run
│   └── cost_logger.py        # Logs API cost per run to JSONL
├── tests/
│   ├── test_reconciler.py
│   ├── test_tally_parser.py
│   ├── test_gstr2b_reader.py
│   ├── test_claude_agent.py  # Uses dry_run — no API key needed
│   ├── test_gsp_client.py    # dry-run always; --live for sandbox
│   └── run_tests.py
├── supabase/
│   ├── schema.sql            # Full DB schema (run once to set up)
│   ├── seed_test_clients.sql # 11 test clients + GSP sessions
│   └── secrets_setup.sql     # Supabase secrets configuration
├── n8n/
│   ├── workflow.json         # n8n Cloud workflow
│   └── workflow_local.json   # n8n local workflow
├── testcases/
│   └── mehta_textile_oct2024/  # Complete real-world test scenario
└── CLAUDE.md                 # Instructions for Claude Code
```

---

## Test scenario — Mehta Textile (Oct 2024)

Located in `testcases/mehta_textile_oct2024/`. Three intentional compliance issues:

1. **Cancelled GSTIN** (`24AAFVT9999Z1Z9`) — sale must be reclassified B2B → B2CS
2. **Missing ITC** — supplier didn't file GSTR-1, ₹5,040 ITC at risk
3. **HSN mismatch** — `5407` (synthetic fabric) recorded instead of `5208` (cotton weave)

Expected net GST payable: **₹31,440** (conservative, excluding at-risk ITC).

---

## GSP API — WhiteBooks sandbox

The live GSTR-2B API uses WhiteBooks (`apisandbox.whitebooks.in`). The sandbox follows real GST filing calendar dates:

- GSTR-2B for month M is available **after the 14th of month M+1** (after suppliers' GSTR-1 filing deadline)
- Past periods where GSTR-3B is already filed are locked

For this reason, **`dry_run=True` is recommended for demos and development**. Live API calls are only needed when onboarding real CA clients.

### Testing the GSP client
```bash
# Dry-run only (no credentials needed)
python3 tests/test_gsp_client.py

# Live sandbox test (requires GSP_* env vars)
python3 tests/test_gsp_client.py --live
```

---

## Key design principles

1. **Claude never does arithmetic.** All numbers computed in `reconciler.py`, injected as literals into prompts.
2. **`dry_run=True`** runs the full pipeline with no external API calls — safe for demos and CI.
3. **Graceful degradation** — API failures fall back to template responses rather than crashing.
4. **Two-tier model strategy** — Haiku for short/cheap outputs, Sonnet for detailed CA narrative.
