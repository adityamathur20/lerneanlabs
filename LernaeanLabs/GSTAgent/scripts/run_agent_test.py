"""
run_agent_test.py
-----------------
End-to-end test of the full pipeline:
  Tally XML → GSTR-2B → Reconciler → Claude Agent (WhatsApp + CA Report + Issues JSON)

Execute:
  cd GSTAgent/scripts
  python run_agent_test.py              # live Claude API (uses ANTHROPIC_API_KEY)
  python run_agent_test.py --dry-run   # no API calls, uses built-in templates
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tally_parser import TallyParser
from gstr2b_reader import GSTR2BReader
from reconciler import Reconciler
from claude_agent import GSTClaudeAgent, ClientConfig, save_agent_output

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE      = Path(__file__).parent.parent            # GSTAgent/
TESTCASE  = BASE / "testcases" / "mehta_textile_oct2024"
SALES_XML     = TESTCASE / "tally_export" / "sales_daybook_oct2024.xml"
PURCHASE_XML  = TESTCASE / "tally_export" / "purchase_daybook_oct2024.xml"
GSTR2B_JSON   = TESTCASE / "gstr2b" / "gstr2b_oct2024.json"
CONFIG_JSON   = TESTCASE / "testcase_config.json"
OUTPUT_DIR    = TESTCASE / "agent_output"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    dry_run = "--dry-run" in sys.argv

    print("\n" + "="*60)
    print("  GST AGENT — FULL PIPELINE TEST")
    print("  Client: Mehta Textile Traders")
    print("  Period: October 2024")
    print(f"  Mode: {'DRY RUN (templates)' if dry_run else 'LIVE (Claude API)'}")
    print("="*60)

    # ── Step 1: Parse Tally XML ──────────────────────────────────────────
    print("\n[1/4] Parsing Tally XML exports...")
    sales_vouchers    = TallyParser(str(SALES_XML)).parse_sales_vouchers()
    purchase_vouchers = TallyParser(str(PURCHASE_XML)).parse_purchase_vouchers()
    print(f"  {len(sales_vouchers)} sales vouchers, {len(purchase_vouchers)} purchase vouchers loaded")

    # ── Step 2: Load GSTR-2B ─────────────────────────────────────────────
    print("\n[2/4] Loading GSTR-2B...")
    gstr2b = GSTR2BReader.from_file(str(GSTR2B_JSON))
    print(f"  {gstr2b}")

    # ── Step 3: Reconcile ─────────────────────────────────────────────────
    print("\n[3/4] Running reconciliation engine...")
    result = Reconciler(
        sales=sales_vouchers,
        purchases=purchase_vouchers,
        gstr2b=gstr2b,
        gstin="24AABMT1234C1Z5",
        period="102024"
    ).run()
    print(f"  Status: {result.status} | Issues found: {result.issue_count}")
    print(f"  Net GST payable: ₹{result.tax_calc.net_payable:,.2f}")

    # ── Step 4: Run Claude Agent ──────────────────────────────────────────
    print("\n[4/4] Running Claude Agent...")

    cfg_raw = json.loads(CONFIG_JSON.read_text())
    firm    = cfg_raw["firm"]
    period  = cfg_raw["filing_period"]

    config = ClientConfig(
        firm_name           = firm["name"],
        gstin               = firm["gstin"],
        owner_name          = firm["name"].split()[0],   # "Mehta"
        ca_name             = firm["ca_name"],
        ca_email            = firm["ca_email"],
        filing_period_label = f"{period['month']} {period['year']}",
        gstr1_due_date      = period["gstr1_due_date"],
        gstr3b_due_date     = period["gstr3b_due_date"],
        whatsapp_number     = firm.get("whatsapp_number"),
        language_preference = "english",
    )

    agent  = GSTClaudeAgent(dry_run=dry_run)
    output = agent.run(result, config)

    # ── Save & Print Outputs ──────────────────────────────────────────────
    paths = save_agent_output(output, str(OUTPUT_DIR))

    print("\n" + "="*60)
    print("  WHATSAPP MESSAGE")
    print("="*60)
    print(output.whatsapp_message)

    print("\n" + "="*60)
    print("  CA HANDOFF REPORT (first 40 lines)")
    print("="*60)
    for line in output.ca_report.splitlines()[:40]:
        print(line)

    print("\n" + "="*60)
    print("  STRUCTURED ISSUES")
    print("="*60)
    print(json.dumps(output.issues_structured, indent=2))

    print("\n" + "="*60)
    print("  OUTPUT FILES")
    print("="*60)
    for key, path in paths.items():
        print(f"  {key:12s}: {path}")

    print(f"\n✅ Test complete. Fallback used: {output.fallback_used}\n")


if __name__ == "__main__":
    run()
