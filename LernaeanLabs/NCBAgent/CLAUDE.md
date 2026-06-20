# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**NCB Compliance Agent** — automates the daily physical register and quarterly Form IV/V return required under India's NDPS (Regulation of Controlled Substances) Order 2013. Bridges the gap between Tally (rupee-value ledger) and NCB's quantity-based (kg) compliance format.

Full product, market, and regulatory context: [`knowledge_db/claude.md`](knowledge_db/claude.md)

---

## Commands

No code exists yet. When modules are built, the expected run surface is:

```bash
# Run daily pipeline (no API key needed in dry-run)
PYTHONPATH=. python run_daily.py --dry-run
PYTHONPATH=. python run_daily.py --tally-xml=/path/to/export.xml

# Run full test suite
cd tests && python run_all_tests.py

# Run a single test file
PYTHONPATH=.. pytest test_daily_register.py -v

# On-premise CLI (once packaged)
ncb-agent run-daily --tally-xml=/path/to/export.xml
ncb-agent generate-quarterly --quarter=Q3-2025 --output=/path/
```

---

## Architecture

Single deterministic pipeline — not multi-agent, not LangGraph. Each layer has no knowledge of the layer above it.

```
tally_ncb_parser.py          ← Tally XML → typed dataclasses (rupees → kg conversion)
    ↓
daily_register_module.py     ← deterministic: nil-transaction fill, stock balance, URN check
    ↓
quarterly_return_module.py   ← Form IV/V compilation; balance mismatch → BLOCK (no bypass)
    ↓
ncb_claude_agent.py          ← AI reasoning only: discrepancy interpretation, WhatsApp alerts
    ↓
Human approval gate          ← NEVER auto-submit to precursorsncb.gov.in
```

**Claude's role is strictly interpretive.** It explains what discrepancies mean; all arithmetic is Python.

**Two models, two jobs:**
- `claude-haiku-4-5-20251001` — WhatsApp alerts (≤400 tokens)
- `claude-sonnet-4-6` — discrepancy interpretation + pre-inspection narrative (≤2000 tokens)

**GST Agent reference implementation** (same author, same pattern, 234 tests passing):
`/Users/adityamathur/Desktop/gitlabs_repo/GSTAgent/`
Start there when building analogous modules (`tally_parser.py`, `claude_agent.py`, `cost_logger.py`).

---

## Coding Conventions

```python
# Python 3.10+
# Quantities and monetary amounts: decimal.Decimal always, never float
from decimal import Decimal

# All domain models: dataclasses with full type hints
from dataclasses import dataclass

@dataclass
class DailyRegisterEntry:
    client_id: str
    substance: str
    date: str           # YYYYMMDD
    opening_kg: Decimal
    purchases_kg: Decimal
    sales_kg: Decimal
    closing_kg: Decimal
    nil_transaction: bool
    approved_by: str | None
    approved_at: str | None
```

- `dry_run=True` in all tests — zero network calls, deterministic stubs
- Exponential backoff, 3 attempts, handle HTTP 429/529
- Fallback templates for all Claude output types (register alert, inspection narrative)
- Test naming: `test_<module>_<scenario>`
- No magic constants — named variables for every threshold/limit

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...           # required for live runs
SUPABASE_URL=https://xxx.supabase.co   # Phase 2
SUPABASE_KEY=service-role-key          # Phase 2
NCB_PRE_REGISTER_SCRAPE_INTERVAL=86400 # URN cache refresh interval (seconds)
```

---

## Key Safety Constraints

1. **Balance check is a hard gate** — if stock reconciliation doesn't close, Form IV/V is not generated. No user bypass.
2. **Agent never auto-submits** to `precursorsncb.gov.in` or any government portal.
3. **Audit log is write-locked** on approval — immutable history required for NDPS inspections.
4. **URN format**: `^NCB-[A-Z]{2}-\d{4}-\d{6}$` — always validate format before any transaction is accepted.
5. **Record preservation**: 5 years (not 2 — 2013 Order updated from 1993 Order).
