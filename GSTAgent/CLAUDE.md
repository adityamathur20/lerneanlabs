# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GSTAgent is a Python-based GST (Goods and Services Tax) compliance automation system for Indian businesses. It reconciles accounting data from Tally ERP against government GSTR-2B filings and generates reports for Chartered Accountants (CAs). Python 3.10+ with minimal dependencies (only `lxml` beyond stdlib).

## Commands

**Run full pipeline (reconciliation + Claude outputs):**
```bash
cd scripts
python run_agent_test.py              # requires ANTHROPIC_API_KEY
python run_agent_test.py --dry-run   # no API key needed, uses templates
```

**Run reconciliation only (no Claude):**
```bash
cd scripts
python run_testcase.py
```

**Run all tests:**
```bash
cd tests
python run_tests.py
```

**Run a single test file:**
```bash
cd tests
python test_reconciler.py
python test_tally_parser.py
python test_gstr2b_reader.py
python test_claude_agent.py          # uses dry_run mode, no API key needed
```

**Environment:**
```bash
export ANTHROPIC_API_KEY=sk-...      # only needed for live API calls
```

## Architecture

### Data Flow

```
Tally XML exports  →  TallyParser  →  [SalesVoucher, PurchaseVoucher]
GSTR-2B JSON       →  GSTR2BReader →  O(1) indexed by (supplier_gstin, invoice_number)
                                  ↓
                            Reconciler (pure Python, deterministic)
                                  ↓ ReconciliationResult
                            PromptBuilder → (system_prompt, user_prompt)
                                  ↓
                            GSTClaudeAgent → ClaudeAPIClient
                            ├── Haiku → WhatsApp message (≤250 words)
                            ├── Sonnet → CA technical report
                            └── Haiku → issues_structured.json
                                  ↓
                            AgentOutput → saved files
```

### Module Responsibilities

- **`scripts/tally_parser.py`** — Parses Tally ERP XML daybooks (uses `lxml`). No business logic. Produces `SalesVoucher` and `PurchaseVoucher` dataclasses with GSTIN, tax amounts, HSN codes.
- **`scripts/gstr2b_reader.py`** — Loads GSTR-2B JSON (local file or API response). Indexes invoices for O(1) lookup. Supports both testcase files (`from_file()`) and live GSP API (`from_api_response()`).
- **`scripts/reconciler.py`** — 4-step deterministic reconciliation: GSTIN validation → ITC matching → HSN code checks → tax calculation. All arithmetic here; Claude never computes numbers.
- **`scripts/prompt_builder.py`** — Pure data transformation. Converts `ReconciliationResult` into `(system_prompt, user_prompt)` tuples. Numbers are injected as literals.
- **`scripts/claude_agent.py`** — Calls Anthropic API via `urllib` (no SDK). Two model tiers: Haiku for speed (WhatsApp, issues JSON), Sonnet for detail (CA report). `dry_run=True` uses built-in templates with no API calls. Falls back to templates on API failure.

### Key Design Principles

1. **Claude never does arithmetic.** All numbers are computed in `reconciler.py` and injected as literals into prompts. Claude only handles language and phrasing.
2. **`dry_run=True`** enables full pipeline testing without an API key — all tests use this mode.
3. **Graceful degradation:** API failures fall back to template responses rather than crashing.
4. **Two-tier model strategy:** Haiku for short/cheap outputs, Sonnet for detailed narrative.

### Test Scenario

`testcases/mehta_textile_oct2024/` contains a complete real-world scenario with three intentional compliance issues:
1. Cancelled GSTIN (`24AAFVT9999Z1Z9`) — sale must be reclassified B2B → B2CS
2. Supplier didn't file GSTR-1 — ITC ₹5,040 at risk, do not claim
3. HSN code mismatch — `5407` (synthetic) recorded instead of `5208` (cotton weave)

Expected net GST payable: ₹31,440 (conservative, excluding at-risk ITC). The `testcase_config.json` in that directory configures firm details, filing period, and expected issues.

### ReconciliationResult Structure

```python
@dataclass
class ReconciliationResult:
    gstin_issues: list[GSTINValidationResult]   # cancelled/suspended GSTINs
    itc_results: list[ITCReconciliationResult]  # MATCHED / MISSING_FROM_GSTR2B / AMOUNT_MISMATCH
    hsn_flags: list[HSNFlagResult]              # probable HSN mismatches
    tax_calc: TaxCalculation                    # net IGST/CGST/SGST payable
```
