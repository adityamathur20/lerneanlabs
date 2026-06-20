"""
run_testcase.py
---------------
Runs the full agent pipeline against the Mehta Textile testcase.

Execute:
  cd GSTAgent/agent
  python run_testcase.py

What this does:
  1. Parse Tally XML exports (sales + purchases)
  2. Load GSTR-2B JSON
  3. Run reconciliation engine
  4. Print detailed output showing all issues caught
  5. Write results to agent_output/ directory

This is the first working end-to-end test of the pipeline.
No Claude API calls yet — that comes next (claude_agent.py).
"""

import json
import sys
from pathlib import Path

# Add agent/ to path
sys.path.insert(0, str(Path(__file__).parent))

from tally_parser import TallyParser
from gstr2b_reader import GSTR2BReader
from reconciler import Reconciler


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent  # GSTAgent/
TESTCASE = BASE / "testcases" / "mehta_textile_oct2024"

SALES_XML    = TESTCASE / "tally_export" / "sales_daybook_oct2024.xml"
PURCHASE_XML = TESTCASE / "tally_export" / "purchase_daybook_oct2024.xml"
GSTR2B_JSON  = TESTCASE / "gstr2b" / "gstr2b_oct2024.json"
OUTPUT_DIR   = TESTCASE / "agent_output"


def run():
    print("\n" + "="*60)
    print("  GST AGENT — TESTCASE RUN")
    print("  Client: Mehta Textile Traders (24AABMT1234C1Z5)")
    print("  Period: October 2024")
    print("="*60)

    # ── Step 1: Parse Tally XML ──────────────────────────────────────────
    print("\n📂 Parsing Tally XML exports...")

    sales_parser = TallyParser(str(SALES_XML))
    sales_vouchers = sales_parser.parse_sales_vouchers()
    print(f"  Loaded {len(sales_vouchers)} sales vouchers from Tally")

    for v in sales_vouchers:
        print(f"    {v.voucher_number}: {v.buyer_name} ({v.buyer_gstin}) "
              f"| Taxable: ₹{v.taxable_value:,.2f} "
              f"| GST: CGST ₹{v.cgst:,.2f} SGST ₹{v.sgst:,.2f} IGST ₹{v.igst:,.2f}")

    purchase_parser = TallyParser(str(PURCHASE_XML))
    purchase_vouchers = purchase_parser.parse_purchase_vouchers()
    print(f"\n  Loaded {len(purchase_vouchers)} purchase vouchers from Tally")

    for p in purchase_vouchers:
        print(f"    {p.voucher_number}: {p.supplier_name} ({p.supplier_gstin}) "
              f"| Taxable: ₹{p.taxable_value:,.2f} "
              f"| ITC: ₹{p.itc_claimable:,.2f}")

    # ── Step 2: Load GSTR-2B ─────────────────────────────────────────────
    print(f"\n📥 Loading GSTR-2B...")
    gstr2b = GSTR2BReader.from_file(str(GSTR2B_JSON))
    print(f"  {gstr2b}")

    itc_summary = gstr2b.get_itc_summary()
    print(f"  ITC available — IGST: ₹{itc_summary.total_igst:,.2f} | "
          f"CGST: ₹{itc_summary.total_cgst:,.2f} | "
          f"SGST: ₹{itc_summary.total_sgst:,.2f}")

    # ── Step 3: Run Reconciliation ────────────────────────────────────────
    print(f"\n⚙️  Running reconciliation engine...")
    reconciler = Reconciler(
        sales=sales_vouchers,
        purchases=purchase_vouchers,
        gstr2b=gstr2b,
        gstin="24AABMT1234C1Z5",
        period="102024"
    )
    result = reconciler.run()

    # ── Step 4: Print Issues ──────────────────────────────────────────────
    print("\n" + "="*60)
    print("  ISSUES FOUND")
    print("="*60)

    if result.gstin_issues:
        print(f"\n🚨 GSTIN Issues ({len(result.gstin_issues)}):")
        for issue in result.gstin_issues:
            print(f"  Invoice: {issue.invoice_number}")
            print(f"  Buyer: {issue.name} ({issue.gstin})")
            print(f"  Status: {issue.status}")
            print(f"  Issue: {issue.issue}")
            print(f"  Action: {issue.action}")
            print()

    missing_itc = [r for r in result.itc_results if r.status == 'MISSING_FROM_GSTR2B']
    if missing_itc:
        print(f"\n⚠️  ITC At Risk ({len(missing_itc)} invoices):")
        for r in missing_itc:
            print(f"  Invoice: {r.purchase_voucher.voucher_number}")
            print(f"  Supplier: {r.purchase_voucher.supplier_name} ({r.purchase_voucher.supplier_gstin})")
            print(f"  ITC At Risk: ₹{r.itc_at_risk:,.2f}")
            print(f"  Reason: {r.risk_reason}")
            print()

    if result.hsn_flags:
        print(f"\nℹ️  HSN Code Flags ({len(result.hsn_flags)}):")
        for flag in result.hsn_flags:
            print(f"  Invoice: {flag.invoice_number}")
            print(f"  Item: {flag.item_name}")
            print(f"  HSN in Tally: {flag.hsn_in_tally}")
            print(f"  Suggested: {flag.suggested_hsn}")
            print(f"  Note: {flag.description}")
            print()

    # ── Step 5: Tax Summary ───────────────────────────────────────────────
    tc = result.tax_calc
    print("="*60)
    print("  TAX LIABILITY SUMMARY")
    print("="*60)
    print(f"  Output Tax:      IGST ₹{tc.output_igst:>10,.2f} | CGST ₹{tc.output_cgst:>10,.2f} | SGST ₹{tc.output_sgst:>10,.2f}")
    print(f"  Confirmed ITC:   IGST ₹{tc.itc_igst:>10,.2f} | CGST ₹{tc.itc_cgst:>10,.2f} | SGST ₹{tc.itc_sgst:>10,.2f}")
    print(f"  {'─'*56}")
    print(f"  Net Payable:     IGST ₹{tc.net_igst:>10,.2f} | CGST ₹{tc.net_cgst:>10,.2f} | SGST ₹{tc.net_sgst:>10,.2f}")
    print(f"  {'─'*56}")
    print(f"  TOTAL NET PAYABLE: ₹{tc.net_payable:,.2f}")
    print(f"  ITC At Risk (excluded): ₹{result.at_risk_itc:,.2f}")
    print()

    # ── Step 6: Write machine-readable output ─────────────────────────────
    output_data = {
        "gstin": result.gstin,
        "period": result.period,
        "status": result.status,
        "issue_count": result.issue_count,
        "sales": {
            "invoice_count": len(result.sales_vouchers),
            "total_value": result.total_sales_value,
            "total_output_gst": result.total_output_gst
        },
        "purchases": {
            "invoice_count": len(result.purchase_vouchers),
            "total_value": result.total_purchase_value,
            "confirmed_itc": result.confirmed_itc,
            "at_risk_itc": result.at_risk_itc
        },
        "gstin_issues": [
            {"invoice": i.invoice_number, "gstin": i.gstin, "name": i.name,
             "status": i.status, "action": i.action}
            for i in result.gstin_issues
        ],
        "itc_mismatches": [
            {"invoice": r.purchase_voucher.voucher_number,
             "supplier": r.purchase_voucher.supplier_name,
             "status": r.status, "at_risk": r.itc_at_risk,
             "reason": r.risk_reason}
            for r in result.itc_results if r.status != 'MATCHED'
        ],
        "hsn_flags": [
            {"invoice": f.invoice_number, "item": f.item_name,
             "hsn_tally": f.hsn_in_tally, "suggested": f.suggested_hsn}
            for f in result.hsn_flags
        ],
        "tax_calculation": {
            "output_igst": tc.output_igst, "output_cgst": tc.output_cgst, "output_sgst": tc.output_sgst,
            "itc_igst": tc.itc_igst, "itc_cgst": tc.itc_cgst, "itc_sgst": tc.itc_sgst,
            "net_igst": tc.net_igst, "net_cgst": tc.net_cgst, "net_sgst": tc.net_sgst,
            "net_payable": tc.net_payable,
            "at_risk": result.at_risk_itc
        }
    }

    out_path = OUTPUT_DIR / "reconciliation_result_computed.json"
    with open(out_path, 'w') as f:
        json.dump(output_data, f, indent=2)
    print(f"✅ Results written to: {out_path}")
    print("\n🔜 Next step: run claude_agent.py to generate WhatsApp alert + CA report\n")


if __name__ == "__main__":
    run()
