"""
NCBAgent Demo Runner

Usage:
  python run_demo.py --client mehta_chemical --input testcases/sample_trader_feb_may_2026/dummy_ledger.xlsx
  python run_demo.py --client mehta_chemical --input testcases/sample_trader_feb_may_2026/tally_export.xml --source tally
  python run_demo.py --client mehta_chemical --source google_sheets   # requires service account setup

Outputs to: output/{client_id}/april_2026/
  {substance}/form_d_april_2026.pdf
  {substance}/form_g_bundle_april_2026.pdf
  consolidated_report.html
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from agent.config_loader import load_client_profile
from agent.daily_register_module import generate_registers
from agent.excel_ncb_parser import parse_excel, parse_google_sheets
from agent.tally_ncb_parser import parse_tally_xml
from agent.output.html_report import generate_html_report
from agent.output.pdf_generator import generate_daily_register, generate_form_g_bundle

OUTPUT_YEAR = 2026
OUTPUT_MONTH = 4   # April
PERIOD_START = date(2026, 2, 10)
PERIOD_END = date(2026, 5, 9)


def main() -> None:
    parser = argparse.ArgumentParser(description="NCBAgent Demo Runner")
    parser.add_argument("--client", required=True, help="Client ID (e.g. mehta_chemical)")
    parser.add_argument("--input", default=None, help="Path to .xlsx or .xml input file")
    parser.add_argument("--source", default="excel",
                        choices=["excel", "tally", "google_sheets"],
                        help="Input source type (default: excel)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  NCBAgent Demo  |  Client: {args.client}")
    print(f"  Period: {PERIOD_START} → {PERIOD_END}")
    print(f"  PDF output: April {OUTPUT_YEAR}")
    print(f"{'='*60}\n")

    # --- Step 1: Load config ---
    profile = load_client_profile(args.client)
    print(f"[Config] Client: {profile.client_name}")
    print(f"[Config] URN: {profile.urn}")
    print(f"[Config] Substances: {', '.join(profile.substances)}\n")

    # --- Step 2: Parse input ---
    if args.source == "excel":
        if not args.input:
            print("ERROR: --input is required for source=excel")
            sys.exit(1)
        print(f"[Parse] Reading Excel: {args.input}")
        result = parse_excel(args.client, Path(args.input))

    elif args.source == "tally":
        if not args.input:
            print("ERROR: --input is required for source=tally")
            sys.exit(1)
        print(f"[Parse] Reading Tally XML: {args.input}")
        result = parse_tally_xml(args.client, Path(args.input))

    elif args.source == "google_sheets":
        print("[Parse] Reading Google Sheets...")
        result = parse_google_sheets(args.client)

    print(f"[Parse] Transactions parsed: {len(result.transactions)}")
    if result.errors:
        print(f"[Parse] Parse errors: {len(result.errors)}")
        for e in result.errors[:5]:
            print(f"         Row {e.row_number}: {e.field} = '{e.raw_value}' — {e.reason}")
    if result.warnings:
        for w in result.warnings:
            print(f"[Parse] Warning: {w}")

    flagged = [t for t in result.transactions if t.is_flagged]
    print(f"[Parse] Flagged transactions: {len(flagged)}")
    for t in flagged:
        print(f"         Row {t.row_number} | {t.date} | {t.substance} | {t.anomaly_flags}")
    print()

    # --- Step 3: Generate daily registers ---
    print(f"[Register] Generating daily registers ({PERIOD_START} → {PERIOD_END})...")
    registers = generate_registers(result.transactions, profile, PERIOD_START, PERIOD_END)

    for substance, reg in registers.items():
        april_entries = reg.entries_for_month(OUTPUT_YEAR, OUTPUT_MONTH)
        nil_count = sum(1 for e in april_entries if e.nil_transaction)
        flagged_count = sum(1 for e in april_entries if e.requires_human_review)
        print(f"  {substance:<35} April entries: {len(april_entries):>3} "
              f"| Nil: {nil_count:>3} | Flagged: {flagged_count:>2} "
              f"| Opening: {april_entries[0].opening_kg if april_entries else 0:.1f} kg "
              f"| Closing: {april_entries[-1].closing_kg if april_entries else 0:.1f} kg")
    print()

    # --- Step 4: Create output directories ---
    out_base = Path("output") / args.client / f"april_{OUTPUT_YEAR}"
    out_base.mkdir(parents=True, exist_ok=True)
    for substance in profile.substances:
        sub_dir = out_base / _safe_name(substance)
        sub_dir.mkdir(exist_ok=True)

    # --- Step 5: Generate PDFs ---
    form_label = {"manufacturer": "Form C", "trader": "Form D", "both": "Form C + Form D"}
    print(f"[PDF] Generating {form_label.get(profile.entity_type, 'daily register')} "
          f"and Form G PDFs for April {OUTPUT_YEAR}...")
    for substance, reg in registers.items():
        sub_dir = out_base / _safe_name(substance)

        generate_daily_register(reg, profile, OUTPUT_YEAR, OUTPUT_MONTH, sub_dir)

        form_g_path = sub_dir / f"form_g_bundle_april_{OUTPUT_YEAR}.pdf"
        generate_form_g_bundle(reg, profile, OUTPUT_YEAR, OUTPUT_MONTH, form_g_path)

    # --- Step 6: Generate consolidated HTML ---
    print(f"\n[HTML] Generating consolidated report...")
    html_path = out_base / "consolidated_report.html"
    generate_html_report(
        registers=registers,
        profile=profile,
        year=OUTPUT_YEAR,
        month=OUTPUT_MONTH,
        output_path=html_path,
    )

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"  DONE. Output directory: {out_base.resolve()}")
    print(f"{'='*60}")
    print(f"  consolidated_report.html — open in any browser")
    print(f"  {{substance}}/form_d_april_{OUTPUT_YEAR}.pdf — daily register (Form D)")
    print(f"  {{substance}}/form_g_bundle_april_{OUTPUT_YEAR}.pdf — consignment notes (Form G)")
    print()
    print("  IMPORTANT: These are preparation documents only.")
    print("  All submissions to precursorsncb.gov.in require manual review and approval.")
    print(f"{'='*60}\n")


def _safe_name(substance: str) -> str:
    return substance.lower().replace(" ", "_").replace("-", "_")


if __name__ == "__main__":
    main()
