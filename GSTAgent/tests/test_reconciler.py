"""
test_reconciler.py
------------------
Unit tests for the Reconciler engine — covers all four reconciliation steps,
tax calculation, HSN validation, and end-to-end pipeline.

Test groups:
  A. TaxCalculation dataclass    — properties, net_payable, edge values
  B. _hsn_matches_item()         — known matches, known mismatches, unknown HSN
  C. Reconciler._validate_gstins() — active, cancelled, suspended, unknown
  D. Reconciler._reconcile_itc()  — matched, missing, amount mismatch, tolerance
  E. Reconciler._check_hsn_codes() — flag, no-flag, unknown HSN passthrough
  F. Reconciler._calculate_tax()  — arithmetic, at-risk split, ITC > output
  G. Reconciler.run()             — full pipeline status, issue counts
  H. Integration                  — real testcase end-to-end
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tally_parser import SalesVoucher, PurchaseVoucher, InventoryItem
from gstr2b_reader import GSTR2BReader
from reconciler import (
    Reconciler, TaxCalculation, ReconciliationResult,
    GSTINValidationResult, ITCReconciliationResult, HSNFlagResult,
    _hsn_matches_item
)


# =============================================================================
# A. TaxCalculation — properties
# =============================================================================

class TestTaxCalculation:

    def _tc(self, out_igst=0, out_cgst=0, out_sgst=0,
            itc_igst=0, itc_cgst=0, itc_sgst=0,
            risk_igst=0, risk_cgst=0, risk_sgst=0):
        return TaxCalculation(
            output_igst=out_igst, output_cgst=out_cgst, output_sgst=out_sgst,
            itc_igst=itc_igst, itc_cgst=itc_cgst, itc_sgst=itc_sgst,
            at_risk_igst=risk_igst, at_risk_cgst=risk_cgst, at_risk_sgst=risk_sgst
        )

    def test_net_igst_simple(self):
        tc = self._tc(out_igst=10000, itc_igst=4000)
        assert tc.net_igst == 6000.0

    def test_net_cgst_simple(self):
        tc = self._tc(out_cgst=5000, itc_cgst=2000)
        assert tc.net_cgst == 3000.0

    def test_net_sgst_simple(self):
        tc = self._tc(out_sgst=5000, itc_sgst=2000)
        assert tc.net_sgst == 3000.0

    def test_net_payable_sum(self):
        tc = self._tc(out_cgst=5000, itc_cgst=2000, out_sgst=5000, itc_sgst=2000)
        assert tc.net_payable == 6000.0

    def test_net_never_negative_when_itc_exceeds_output(self):
        """ITC > output → net = 0, not negative (excess carried forward)."""
        tc = self._tc(out_cgst=2000, itc_cgst=5000)
        assert tc.net_cgst == 0.0
        assert tc.net_payable >= 0.0

    def test_total_at_risk(self):
        tc = self._tc(risk_igst=100, risk_cgst=200, risk_sgst=200)
        assert tc.total_at_risk == 500.0

    def test_all_zero(self):
        tc = self._tc()
        assert tc.net_payable == 0.0
        assert tc.total_at_risk == 0.0

    def test_net_payable_rounding(self):
        tc = self._tc(out_cgst=5000.005, itc_cgst=2000.002,
                       out_sgst=5000.005, itc_sgst=2000.002)
        # Ensure result is rounded to 2dp, not a floating-point mess
        assert tc.net_payable == round(tc.net_payable, 2)

    def test_full_mehta_scenario(self):
        """Verify exact numbers from Mehta testcase."""
        tc = TaxCalculation(
            output_igst=10200, output_cgst=28200, output_sgst=28200,
            itc_igst=0, itc_cgst=17580, itc_sgst=17580,
            at_risk_igst=0, at_risk_cgst=2520, at_risk_sgst=2520
        )
        assert tc.net_igst == 10200.0
        assert tc.net_cgst == 10620.0
        assert tc.net_sgst == 10620.0
        assert tc.net_payable == 31440.0


# =============================================================================
# B. _hsn_matches_item()
# =============================================================================

class TestHSNMatchesItem:

    def test_silk_hsn_silk_item(self):
        assert _hsn_matches_item("5007", "Raw Silk Dupioni") is True

    def test_cotton_hsn_cotton_item(self):
        assert _hsn_matches_item("5208", "Cotton Plain Weave Fabric") is True

    def test_synthetic_hsn_synthetic_item(self):
        assert _hsn_matches_item("5407", "Synthetic Polyester Fabric") is True

    def test_linen_hsn_linen_item(self):
        assert _hsn_matches_item("5309", "Linen Woven 58 inch") is True

    def test_dye_hsn_dye_item(self):
        assert _hsn_matches_item("3204", "Reactive Dyes Blue") is True

    def test_synthetic_hsn_cotton_item_is_mismatch(self):
        """5407 (synthetic) on a cotton item should return False."""
        assert _hsn_matches_item("5407", "Cotton Plain Weave Fabric 60x60") is False

    def test_cotton_hsn_silk_item_is_mismatch(self):
        assert _hsn_matches_item("5208", "Raw Silk Dupioni 44 inch") is False

    def test_silk_hsn_linen_item_is_mismatch(self):
        assert _hsn_matches_item("5007", "Linen Natural Fabric") is False

    def test_unknown_hsn_always_passes(self):
        """Unknown HSN prefix → don't flag (we don't know what it is)."""
        assert _hsn_matches_item("9999", "Mystery Item") is True

    def test_hsn_prefix_4_chars_used(self):
        """Only first 4 chars of HSN are checked against category map."""
        assert _hsn_matches_item("52081200", "Cotton Fabric") is True  # 5208 = cotton

    def test_case_insensitive_item_name(self):
        assert _hsn_matches_item("5208", "COTTON PLAIN WEAVE") is True

    def test_blended_fabric_hsn(self):
        assert _hsn_matches_item("5512", "Synthetic staple blended fabric") is True


# =============================================================================
# C. _validate_gstins()
# =============================================================================

class TestValidateGSTINs:

    def _run_validation(self, sales, gstr2b_dict):
        reader = GSTR2BReader.from_api_response(gstr2b_dict)
        r = Reconciler(sales=sales, purchases=[], gstr2b=reader,
                       gstin="24AABMT1234C1Z5", period="102024")
        return r._validate_gstins()

    def test_all_active_no_issues(self, sample_sales_vouchers, full_gstr2b_dict):
        # Replace sample_sales with one that only has active GSTIN
        from tally_parser import SalesVoucher
        sales = [SalesVoucher(
            date="20241001", voucher_number="INV/001", guid="G1",
            buyer_name="Good Buyer", buyer_gstin="24AABGT1234A1Z9",
            place_of_supply="Gujarat", taxable_value=100000.0,
            invoice_total=112000.0, cgst=6000.0, sgst=6000.0, igst=0.0,
            supply_type="INTRA", items=[]
        )]
        issues = self._run_validation(sales, full_gstr2b_dict)
        assert issues == []

    def test_cancelled_gstin_flagged(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            date="20241001", voucher_number="INV/CANC", guid="G2",
            buyer_name="Cancelled Buyer", buyer_gstin="24AABCX9999Z1Z9",
            place_of_supply="Gujarat", taxable_value=50000.0,
            invoice_total=56000.0, cgst=3000.0, sgst=3000.0, igst=0.0,
            supply_type="INTRA", items=[]
        )]
        issues = self._run_validation(sales, full_gstr2b_dict)
        assert len(issues) == 1
        assert issues[0].status == "Cancelled"
        assert issues[0].invoice_number == "INV/CANC"

    def test_cancelled_gstin_has_reclassify_action(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            date="20241001", voucher_number="INV/CANC2", guid="G3",
            buyer_name="Cancelled Buyer", buyer_gstin="24AABCX9999Z1Z9",
            place_of_supply="Gujarat", taxable_value=50000.0,
            invoice_total=56000.0, cgst=3000.0, sgst=3000.0, igst=0.0,
            supply_type="INTRA", items=[]
        )]
        issues = self._run_validation(sales, full_gstr2b_dict)
        assert issues[0].action is not None
        assert "B2CS" in issues[0].action

    def test_unknown_gstin_not_flagged(self, full_gstr2b_dict):
        """GSTIN not in verification data → no issue raised (API not called in test)."""
        sales = [SalesVoucher(
            date="20241001", voucher_number="INV/UNK", guid="G4",
            buyer_name="Unknown", buyer_gstin="24AABZZ9999Z1Z9",
            place_of_supply="Gujarat", taxable_value=50000.0,
            invoice_total=56000.0, cgst=3000.0, sgst=3000.0, igst=0.0,
            supply_type="INTRA", items=[]
        )]
        issues = self._run_validation(sales, full_gstr2b_dict)
        assert issues == []

    def test_duplicate_gstin_only_flagged_once(self, full_gstr2b_dict):
        """Same cancelled GSTIN on two invoices → only one issue (deduplication)."""
        sales = [
            SalesVoucher("20241001", "INV/D1", "G5", "Cancelled", "24AABCX9999Z1Z9",
                          "Gujarat", 50000, 56000, 3000, 3000, 0, "INTRA"),
            SalesVoucher("20241005", "INV/D2", "G6", "Cancelled", "24AABCX9999Z1Z9",
                          "Gujarat", 50000, 56000, 3000, 3000, 0, "INTRA"),
        ]
        issues = self._run_validation(sales, full_gstr2b_dict)
        assert len(issues) == 1


# =============================================================================
# D. _reconcile_itc()
# =============================================================================

class TestReconcileITC:

    def _run_reconcile(self, purchases, gstr2b_dict):
        reader = GSTR2BReader.from_api_response(gstr2b_dict)
        r = Reconciler(sales=[], purchases=purchases, gstr2b=reader,
                       gstin="24AABMT1234C1Z5", period="102024")
        return r._reconcile_itc()

    def test_matched_invoice_status(self, sample_purchase_vouchers, full_gstr2b_dict):
        """SM/001 exists in GSTR-2B → MATCHED."""
        matched = [sample_purchase_vouchers[0]]  # SM/001
        results = self._run_reconcile(matched, full_gstr2b_dict)
        assert results[0].status == "MATCHED"

    def test_matched_invoice_itc_claimed(self, sample_purchase_vouchers, full_gstr2b_dict):
        matched = [sample_purchase_vouchers[0]]
        results = self._run_reconcile(matched, full_gstr2b_dict)
        assert results[0].itc_claimed == 6000.0

    def test_matched_invoice_zero_at_risk(self, sample_purchase_vouchers, full_gstr2b_dict):
        matched = [sample_purchase_vouchers[0]]
        results = self._run_reconcile(matched, full_gstr2b_dict)
        assert results[0].itc_at_risk == 0.0

    def test_missing_invoice_status(self, sample_purchase_vouchers, full_gstr2b_dict):
        """MISSING/001 not in GSTR-2B → MISSING_FROM_GSTR2B."""
        missing = [sample_purchase_vouchers[1]]
        results = self._run_reconcile(missing, full_gstr2b_dict)
        assert results[0].status == "MISSING_FROM_GSTR2B"

    def test_missing_invoice_zero_claimed(self, sample_purchase_vouchers, full_gstr2b_dict):
        missing = [sample_purchase_vouchers[1]]
        results = self._run_reconcile(missing, full_gstr2b_dict)
        assert results[0].itc_claimed == 0.0

    def test_missing_invoice_at_risk_equals_itc(self, sample_purchase_vouchers, full_gstr2b_dict):
        missing = [sample_purchase_vouchers[1]]
        results = self._run_reconcile(missing, full_gstr2b_dict)
        # at_risk == total ITC of the purchase (cgst+sgst = 4800)
        assert results[0].itc_at_risk == 4800.0

    def test_missing_invoice_has_risk_reason(self, sample_purchase_vouchers, full_gstr2b_dict):
        missing = [sample_purchase_vouchers[1]]
        results = self._run_reconcile(missing, full_gstr2b_dict)
        assert results[0].risk_reason is not None
        assert len(results[0].risk_reason) > 0

    def test_amount_mismatch_flagged(self, full_gstr2b_dict):
        """Purchase ITC differs from GSTR-2B ITC by >₹1 → AMOUNT_MISMATCH."""
        purchase = PurchaseVoucher(
            date="20241003", voucher_number="SM/001", guid="PG_MISMATCH",
            supplier_name="Silk Mills Ltd", supplier_gstin="24AABSM1111A1Z8",
            taxable_value=50000.0, invoice_total=57000.0,
            cgst=3500.0, sgst=3500.0, igst=0.0  # 7000 vs GSTR-2B's 6000
        )
        results = self._run_reconcile([purchase], full_gstr2b_dict)
        assert results[0].status == "AMOUNT_MISMATCH"

    def test_amount_mismatch_claims_lower_amount(self, full_gstr2b_dict):
        """On mismatch, agent claims the GSTR-2B figure (safer)."""
        purchase = PurchaseVoucher(
            date="20241003", voucher_number="SM/001", guid="PG_MISMATCH2",
            supplier_name="Silk Mills Ltd", supplier_gstin="24AABSM1111A1Z8",
            taxable_value=50000.0, invoice_total=57000.0,
            cgst=3500.0, sgst=3500.0, igst=0.0
        )
        results = self._run_reconcile([purchase], full_gstr2b_dict)
        assert results[0].itc_claimed == 6000.0   # GSTR-2B value

    def test_rounding_within_tolerance_matched(self, full_gstr2b_dict):
        """Diff ≤ ₹1 must still be treated as MATCHED (rounding tolerance)."""
        purchase = PurchaseVoucher(
            date="20241003", voucher_number="SM/001", guid="PG_TOLERANCE",
            supplier_name="Silk Mills Ltd", supplier_gstin="24AABSM1111A1Z8",
            taxable_value=50000.0, invoice_total=56000.5,
            cgst=3000.25, sgst=3000.25, igst=0.0  # diff = 0.5 < ₹1
        )
        results = self._run_reconcile([purchase], full_gstr2b_dict)
        assert results[0].status == "MATCHED"

    def test_multiple_purchases_mixed_results(self, sample_purchase_vouchers, full_gstr2b_dict):
        """Two purchases: one matched, one missing."""
        results = self._run_reconcile(sample_purchase_vouchers, full_gstr2b_dict)
        statuses = [r.status for r in results]
        assert "MATCHED" in statuses
        assert "MISSING_FROM_GSTR2B" in statuses


# =============================================================================
# E. _check_hsn_codes()
# =============================================================================

class TestCheckHSNCodes:

    def _run_hsn_check(self, sales, gstr2b_dict):
        reader = GSTR2BReader.from_api_response(gstr2b_dict)
        r = Reconciler(sales=sales, purchases=[], gstr2b=reader,
                       gstin="24AABMT1234C1Z5", period="102024")
        return r._check_hsn_codes()

    def test_correct_hsn_no_flag(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            "20241001", "INV/OK", "G1", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Cotton Plain Weave", "5208", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags == []

    def test_mismatched_hsn_flagged(self, full_gstr2b_dict):
        """Cotton item with HSN 5407 (synthetic) → flag."""
        sales = [SalesVoucher(
            "20241001", "INV/BAD", "G2", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Cotton Plain Weave Fabric", "5407", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert len(flags) == 1

    def test_flag_has_suggested_hsn(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            "20241001", "INV/SUGG", "G3", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Cotton Plain Weave Fabric", "5407", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags[0].suggested_hsn == "5208"

    def test_flag_invoice_number_correct(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            "20241001", "INV/FLAGNUM", "G4", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Cotton Plain Weave", "5407", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags[0].invoice_number == "INV/FLAGNUM"

    def test_item_without_hsn_not_flagged(self, full_gstr2b_dict):
        """Item with blank HSN code must not crash or flag."""
        sales = [SalesVoucher(
            "20241001", "INV/NOHSN", "G5", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Mystery Item", "", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags == []

    def test_unknown_hsn_not_flagged(self, full_gstr2b_dict):
        """HSN not in category map → skip (we don't know the right answer)."""
        sales = [SalesVoucher(
            "20241001", "INV/UNK_HSN", "G6", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Electronic Component", "8542", 18, "100 Pcs", "1000/Pc", 100000)]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags == []

    def test_no_items_no_flags(self, full_gstr2b_dict):
        sales = [SalesVoucher(
            "20241001", "INV/NOITM", "G7", "Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA", items=[]
        )]
        flags = self._run_hsn_check(sales, full_gstr2b_dict)
        assert flags == []


# =============================================================================
# F. _calculate_tax()
# =============================================================================

class TestCalculateTax:

    def _make_matched_result(self, cgst, sgst, igst=0):
        from gstr2b_reader import GSTR2BInvoice
        inv = GSTR2BInvoice(
            invoice_number="TEST/001", invoice_date="01-10-2024",
            invoice_value=float(cgst + sgst + igst) * 10,
            supplier_gstin="24AABTS1234E1Z3", supplier_name="Supplier",
            place_of_supply="24", itc_available=True, reverse_charge=False,
            igst=float(igst), cgst=float(cgst), sgst=float(sgst), cess=0.0
        )
        purchase = PurchaseVoucher(
            "20241001", "TEST/001", "PG1", "Supplier", "24AABTS1234E1Z3",
            float(cgst + sgst) / 0.12 if (cgst + sgst) else 0,
            float(cgst + sgst + igst) * 10,
            float(cgst), float(sgst), float(igst)
        )
        return ITCReconciliationResult(
            purchase_voucher=purchase, gstr2b_invoice=inv,
            status="MATCHED", itc_claimed=cgst + sgst + igst, itc_at_risk=0.0
        )

    def _make_missing_result(self, itc_amount):
        purchase = PurchaseVoucher(
            "20241001", "MISS/001", "PG2", "Ghost", "24AABGS9999X1Z1",
            itc_amount / 0.12, itc_amount * 1.12,
            itc_amount / 2, itc_amount / 2, 0.0
        )
        return ITCReconciliationResult(
            purchase_voucher=purchase, gstr2b_invoice=None,
            status="MISSING_FROM_GSTR2B", itc_claimed=0.0, itc_at_risk=itc_amount
        )

    def _run_calc(self, sales, itc_results, gstr2b_dict):
        reader = GSTR2BReader.from_api_response(gstr2b_dict)
        r = Reconciler(sales=sales, purchases=[], gstr2b=reader,
                       gstin="24AABMT1234C1Z5", period="102024")
        return r._calculate_tax(itc_results)

    def test_output_igst_from_sales(self, full_gstr2b_dict):
        sales = [SalesVoucher("20241001", "INV/1", "G1", "B", "24AABGT1234A1Z9",
                               "MH", 100000, 112000, 0, 0, 12000, "INTER")]
        tc = self._run_calc(sales, [], full_gstr2b_dict)
        assert tc.output_igst == 12000.0

    def test_output_cgst_from_sales(self, full_gstr2b_dict):
        sales = [SalesVoucher("20241001", "INV/1", "G1", "B", "24AABGT1234A1Z9",
                               "GJ", 100000, 112000, 6000, 6000, 0, "INTRA")]
        tc = self._run_calc(sales, [], full_gstr2b_dict)
        assert tc.output_cgst == 6000.0

    def test_itc_from_matched_invoices_only(self, full_gstr2b_dict):
        matched = self._make_matched_result(cgst=3000, sgst=3000)
        missing = self._make_missing_result(itc_amount=5000)
        sales = []
        tc = self._run_calc(sales, [matched, missing], full_gstr2b_dict)
        assert tc.itc_cgst == 3000.0
        assert tc.itc_sgst == 3000.0

    def test_missing_itc_not_counted(self, full_gstr2b_dict):
        missing = self._make_missing_result(itc_amount=5000)
        tc = self._run_calc([], [missing], full_gstr2b_dict)
        assert tc.itc_cgst == 0.0

    def test_at_risk_total(self, full_gstr2b_dict):
        missing = self._make_missing_result(itc_amount=5040)
        tc = self._run_calc([], [missing], full_gstr2b_dict)
        assert tc.total_at_risk == 5040.0

    def test_net_payable_full_scenario(self, full_gstr2b_dict):
        """Output 10k CGST + 10k SGST, confirmed ITC 6k each → net 4k each = 8k total."""
        sales = [SalesVoucher("20241001", "INV/1", "G1", "B", "24AABGT1234A1Z9",
                               "GJ", 100000, 120000, 10000, 10000, 0, "INTRA")]
        matched = self._make_matched_result(cgst=6000, sgst=6000)
        tc = self._run_calc(sales, [matched], full_gstr2b_dict)
        assert tc.net_cgst == 4000.0
        assert tc.net_sgst == 4000.0
        assert tc.net_payable == 8000.0


# =============================================================================
# G. Reconciler.run() — full pipeline
# =============================================================================

class TestReconcilerRun:

    def _run(self, sales, purchases, gstr2b_dict, gstin="24AABMT1234C1Z5", period="102024"):
        reader = GSTR2BReader.from_api_response(gstr2b_dict)
        return Reconciler(sales, purchases, reader, gstin, period).run()

    def test_returns_reconciliation_result(self, sample_sales_vouchers,
                                            sample_purchase_vouchers, full_gstr2b_dict):
        result = self._run(sample_sales_vouchers, sample_purchase_vouchers, full_gstr2b_dict)
        assert isinstance(result, ReconciliationResult)

    def test_clean_run_status(self, full_gstr2b_dict):
        """Clean data (no cancelled GSTINs, no missing ITC, no HSN issues) → CLEAN."""
        sales = [SalesVoucher("20241001", "INV/CLEAN", "G1", "Good Buyer",
                               "24AABGT1234A1Z9", "Gujarat", 100000, 112000,
                               6000, 6000, 0, "INTRA",
                               items=[InventoryItem("Cotton Fabric", "5208", 12,
                                                     "1000 Mtr", "100/Mtr", 100000)])]
        purchases = [PurchaseVoucher("20241003", "SM/001", "PG1", "Silk Mills Ltd",
                                      "24AABSM1111A1Z8", 50000, 56000, 3000, 3000, 0)]
        result = self._run(sales, purchases, full_gstr2b_dict)
        assert result.status == "CLEAN"

    def test_cancelled_gstin_status_critical(self, sample_sales_vouchers, full_gstr2b_dict):
        result = self._run(sample_sales_vouchers, [], full_gstr2b_dict)
        assert result.status == "CRITICAL"
        assert result.has_critical_issues is True

    def test_issue_count_correct(self, sample_sales_vouchers, sample_purchase_vouchers, full_gstr2b_dict):
        result = self._run(sample_sales_vouchers, sample_purchase_vouchers, full_gstr2b_dict)
        # 1 cancelled GSTIN + 1 missing ITC = 2 issues (no HSN flags in this fixture)
        assert result.issue_count >= 2

    def test_gstin_stored(self, full_gstr2b_dict):
        result = self._run([], [], full_gstr2b_dict, gstin="24AABMT1234C1Z5")
        assert result.gstin == "24AABMT1234C1Z5"

    def test_period_stored(self, full_gstr2b_dict):
        result = self._run([], [], full_gstr2b_dict, period="102024")
        assert result.period == "102024"

    def test_total_sales_value(self, full_gstr2b_dict):
        sales = [
            SalesVoucher("20241001", "INV/1", "G1", "B", "24AABGT1234A1Z9",
                          "GJ", 100000, 112000, 6000, 6000, 0, "INTRA"),
            SalesVoucher("20241005", "INV/2", "G2", "C", "24AABGT1234A1Z9",
                          "GJ", 50000, 56000, 3000, 3000, 0, "INTRA"),
        ]
        result = self._run(sales, [], full_gstr2b_dict)
        assert result.total_sales_value == 150000.0

    def test_empty_run_clean(self, full_gstr2b_dict):
        """No sales, no purchases → CLEAN with zero payable."""
        result = self._run([], [], full_gstr2b_dict)
        assert result.status == "CLEAN"
        assert result.tax_calc.net_payable == 0.0

    def test_issues_found_status_non_critical(self, full_gstr2b_dict):
        """Only HSN flags (non-critical) → ISSUES_FOUND not CRITICAL."""
        sales = [SalesVoucher(
            "20241001", "INV/HSN", "G1", "Good Buyer", "24AABGT1234A1Z9",
            "Gujarat", 100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem("Cotton Plain Weave Fabric", "5407", 12, "1000 Mtr", "100/Mtr", 100000)]
        )]
        result = self._run(sales, [], full_gstr2b_dict)
        assert result.status == "ISSUES_FOUND"
        assert result.has_critical_issues is False


# =============================================================================
# H. Integration — real testcase
# =============================================================================

class TestRealTestcaseIntegration:

    @pytest.fixture
    def real_result(self):
        base = Path(__file__).parent.parent
        sales_xml = base / "testcases/mehta_textile_oct2024/tally_export/sales_daybook_oct2024.xml"
        purchase_xml = base / "testcases/mehta_textile_oct2024/tally_export/purchase_daybook_oct2024.xml"
        gstr2b_json = base / "testcases/mehta_textile_oct2024/gstr2b/gstr2b_oct2024.json"
        if not all(p.exists() for p in [sales_xml, purchase_xml, gstr2b_json]):
            pytest.skip("Testcase files not found")
        from tally_parser import TallyParser
        sales = TallyParser(str(sales_xml)).parse_sales_vouchers()
        purchases = TallyParser(str(purchase_xml)).parse_purchase_vouchers()
        reader = GSTR2BReader.from_file(str(gstr2b_json))
        return Reconciler(sales, purchases, reader, "24AABMT1234C1Z5", "102024").run()

    def test_status_critical(self, real_result):
        assert real_result.status == "CRITICAL"

    def test_exactly_three_issues(self, real_result):
        assert real_result.issue_count == 3

    def test_one_cancelled_gstin(self, real_result):
        assert len(real_result.gstin_issues) == 1
        assert real_result.gstin_issues[0].status == "Cancelled"

    def test_verma_traders_flagged(self, real_result):
        assert real_result.gstin_issues[0].gstin == "24AAFVT9999Z1Z9"

    def test_one_missing_itc(self, real_result):
        missing = [r for r in real_result.itc_results if r.status == "MISSING_FROM_GSTR2B"]
        assert len(missing) == 1

    def test_dye_masters_itc_at_risk(self, real_result):
        missing = [r for r in real_result.itc_results if r.status == "MISSING_FROM_GSTR2B"]
        assert missing[0].itc_at_risk == 5040.0

    def test_one_hsn_flag(self, real_result):
        assert len(real_result.hsn_flags) == 1

    def test_hsn_flag_on_invoice_003(self, real_result):
        assert real_result.hsn_flags[0].invoice_number == "MTT/OCT/003"

    def test_hsn_suggests_5208(self, real_result):
        assert real_result.hsn_flags[0].suggested_hsn == "5208"

    def test_net_payable_31440(self, real_result):
        assert real_result.tax_calc.net_payable == 31440.0

    def test_three_matched_itc(self, real_result):
        matched = [r for r in real_result.itc_results if r.status == "MATCHED"]
        assert len(matched) == 3

    def test_total_sales_value(self, real_result):
        assert real_result.total_sales_value == 555000.0
