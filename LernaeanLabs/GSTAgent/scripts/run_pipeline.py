"""
run_pipeline.py
---------------
Machine-callable entry point for the GSTAgent pipeline. Designed to be
invoked by n8n's Execute Command node once per client per month.

What it does:
  1. Loads client config from Supabase (or testcase in dry-run)
  2. Fetches GSTR-2B via GSPClient (live or mock)
  3. Parses Tally XML (paths passed as args or loaded from Supabase client record)
  4. Runs Reconciler (pure Python, deterministic)
  5. Runs GSTClaudeAgent (Haiku + Sonnet, or templates in dry-run)
  6. Logs costs to cost_log.jsonl
  7. Prints a single JSON line to stdout for n8n to parse
  8. Exits 0 on success, 1 on any failure

n8n invocation (Execute Command node):
  cd $GSTAGENT_DIR && python3 scripts/run_pipeline.py \
    --gstin {{ $json.gstin }} \
    --period {{ $('Calculate Period').item.json.period }} \
    --tally-sales /path/to/sales.xml \
    --tally-purchases /path/to/purchases.xml

Output JSON (stdout, single line):
  {
    "success": true,
    "client_gstin": "...",
    "period": "102024",
    "reconciliation_status": "CRITICAL",
    "issue_count": 3,
    "net_payable_inr": 31440.0,
    "whatsapp_message": "...",
    "ca_report": "...",
    "issues_structured": [...],
    "model_whatsapp": "claude-haiku-4-5-20251001",
    "model_ca_report": "claude-sonnet-4-6",
    "fallback_used": false,
    "cost_usd": 0.00412,
    "cost_inr": 0.3461
  }

On failure:
  {"success": false, "error": "description of what went wrong"}
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from tally_parser import TallyParser
from gstr2b_reader import GSTR2BReader
from reconciler import Reconciler
from claude_agent import GSTClaudeAgent, ClientConfig
from gsp_client import GSPClient, SupabaseClient
from cost_logger import CostLogger


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description="GSTAgent pipeline — called by n8n")
    p.add_argument("--gstin",            required=True, help="Client GSTIN e.g. 24AABMT1234C1Z5")
    p.add_argument("--period",           required=True, help="Filing period MMYYYY e.g. 102024")
    p.add_argument("--tally-sales",      help="Path to Tally sales daybook XML")
    p.add_argument("--tally-purchases",  help="Path to Tally purchases daybook XML")
    p.add_argument("--dry-run",          action="store_true",
                   help="Use mock data and template Claude responses. No API calls.")
    p.add_argument("--cost-log",         default="cost_log.jsonl",
                   help="Path for cost log file (default: cost_log.jsonl in CWD)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Client config loader
# ---------------------------------------------------------------------------

def _load_client_config(gstin: str, period: str, dry_run: bool) -> ClientConfig:
    """
    Load client details from Supabase. Falls back to testcase data in dry-run.
    Returns a ClientConfig ready to pass to GSTClaudeAgent.
    """
    # Derive due dates from period (MMYYYY)
    mm, yyyy = int(period[:2]), int(period[2:])
    next_mm   = (mm % 12) + 1
    next_yyyy = yyyy + (1 if mm == 12 else 0)
    gstr1_due  = f"{next_yyyy}-{next_mm:02d}-11"
    gstr3b_due = f"{next_yyyy}-{next_mm:02d}-20"
    months = ["","January","February","March","April","May","June",
              "July","August","September","October","November","December"]
    period_label = f"{months[mm]} {yyyy}"

    if dry_run:
        # Try real client name from Supabase so each client looks different in demo.
        # Falls back to Mehta Textile testcase if Supabase is not reachable.
        try:
            db  = SupabaseClient()
            row = db.get_client_by_gstin(gstin)
            if row:
                return ClientConfig(
                    firm_name           = row["firm_name"],
                    gstin               = row["gstin"],
                    owner_name          = row["owner_name"],
                    ca_name             = row["ca_name"],
                    ca_email            = row["ca_email"],
                    filing_period_label = period_label,
                    gstr1_due_date      = gstr1_due,
                    gstr3b_due_date     = gstr3b_due,
                    whatsapp_number     = row.get("owner_whatsapp"),
                    language_preference = row.get("language_preference", "english"),
                )
        except Exception as e:
            print(f"[WARN] Supabase lookup failed for {gstin}: {e}", file=sys.stderr)

        base = Path(__file__).parent.parent / "testcases" / "mehta_textile_oct2024"
        cfg  = json.loads((base / "testcase_config.json").read_text())
        firm = cfg["firm"]
        return ClientConfig(
            firm_name           = firm["name"],
            gstin               = firm["gstin"],
            owner_name          = firm["name"].split()[0],
            ca_name             = firm["ca_name"],
            ca_email            = firm["ca_email"],
            filing_period_label = period_label,
            gstr1_due_date      = gstr1_due,
            gstr3b_due_date     = gstr3b_due,
            whatsapp_number     = firm.get("whatsapp_number"),
            language_preference = "english",
        )

    db  = SupabaseClient()
    row = db.get_client_by_gstin(gstin)
    if not row:
        raise ValueError(f"GSTIN {gstin} not found in Supabase clients table.")

    return ClientConfig(
        firm_name           = row["firm_name"],
        gstin               = row["gstin"],
        owner_name          = row["owner_name"],
        ca_name             = row["ca_name"],
        ca_email            = row["ca_email"],
        filing_period_label = period_label,
        gstr1_due_date      = gstr1_due,
        gstr3b_due_date     = gstr3b_due,
        whatsapp_number     = row.get("owner_whatsapp"),
        language_preference = row.get("language_preference", "english"),
    )


# ---------------------------------------------------------------------------
# Tally XML loader
# ---------------------------------------------------------------------------

def _load_tally_vouchers(gstin: str, period: str, sales_path: str, purchases_path: str, dry_run: bool):
    """
    Load Tally vouchers from XML files.
    In dry-run: Mehta uses testcase XML; all other clients use per-client mock generator.
    In live: uses --tally-sales / --tally-purchases args.
    """
    MEHTA_GSTIN = '24AABMT1234C1Z5'
    if dry_run and gstin != MEHTA_GSTIN:
        from mock_tally import get_sales_vouchers, get_purchase_vouchers
        return get_sales_vouchers(gstin, period), get_purchase_vouchers(gstin, period)

    if dry_run or (not sales_path and not purchases_path):
        base = Path(__file__).parent.parent / "testcases" / "mehta_textile_oct2024" / "tally_export"
        sales_path    = str(base / "sales_daybook_oct2024.xml")
        purchases_path = str(base / "purchase_daybook_oct2024.xml")

    if not Path(sales_path).exists():
        raise FileNotFoundError(f"Tally sales XML not found: {sales_path}")
    if not Path(purchases_path).exists():
        raise FileNotFoundError(f"Tally purchases XML not found: {purchases_path}")

    sales     = TallyParser(sales_path).parse_sales_vouchers()
    purchases = TallyParser(purchases_path).parse_purchase_vouchers()
    return sales, purchases


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(args) -> dict:
    gstin    = args.gstin
    period   = args.period
    dry_run  = args.dry_run

    # 1. Client config
    config = _load_client_config(gstin, period, dry_run)

    # 2. Tally vouchers
    sales, purchases = _load_tally_vouchers(
        gstin, period,
        args.tally_sales, args.tally_purchases,
        dry_run,
    )

    # 3. GSTR-2B
    gsp    = GSPClient(gstin=gstin, dry_run=dry_run)
    gstr2b_data = gsp.fetch_gstr2b(period)
    gstr2b = GSTR2BReader.from_api_response(gstr2b_data)

    # 4. Reconcile
    result = Reconciler(
        sales=sales,
        purchases=purchases,
        gstr2b=gstr2b,
        gstin=gstin,
        period=period,
    ).run()

    # 5. Claude agent
    cost_logger = CostLogger(log_path=args.cost_log)
    agent  = GSTClaudeAgent(dry_run=dry_run, verbose=False, cost_logger=cost_logger)
    output = agent.run(result, config)

    # 6. Collect cost for this run
    run_cost = cost_logger.run_summary(gstin, period)

    return {
        "success":                True,
        "client_gstin":           gstin,
        "period":                 period,
        "firm_name":              config.firm_name,
        "ca_email":               config.ca_email,
        "whatsapp_number":        config.whatsapp_number,
        "reconciliation_status":  result.status,
        "issue_count":            result.issue_count,
        "net_payable_inr":        float(result.tax_calc.net_payable),
        "whatsapp_message":       output.whatsapp_message,
        "ca_report":              output.ca_report,
        "issues_structured":      output.issues_structured,
        "model_whatsapp":         output.model_used_whatsapp,
        "model_ca_report":        output.model_used_ca_report,
        "fallback_used":          output.fallback_used,
        "cost_usd":               run_cost["cost_usd"],
        "cost_inr":               run_cost["cost_inr"],
        "generated_at":           output.generated_at,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = _parse_args()
    # Redirect all pipeline prints (reconciler, agent verbose logs) to stderr.
    # stdout is reserved exclusively for the single JSON output line that n8n parses.
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = run_pipeline(args)
        sys.stdout = _real_stdout
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        sys.stdout = _real_stdout
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
