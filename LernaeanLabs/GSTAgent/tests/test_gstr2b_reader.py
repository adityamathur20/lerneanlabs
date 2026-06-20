"""
test_gstr2b_reader.py
---------------------
Unit tests for GSTR2BReader — covers JSON loading, index building,
invoice lookup, supplier status, ITC summary, and edge cases.

Test groups:
  A. Instantiation    — from_file, from_api_response, wrapped/unwrapped data
  B. Invoice lookup   — hit, miss, case-insensitive, whitespace
  C. Supplier index   — status, invoice count, ITC totals
  D. ITC summary      — aggregation, ITC-unavailable exclusion
  E. GSTIN verification — active, cancelled, missing
  F. Edge cases       — empty b2b, comment-only blocks, zero tax
  G. GSTR2BInvoice    — total_itc property
  H. ITCSummary       — total_itc property
  I. Integration      — real testcase JSON
"""

import pytest
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from gstr2b_reader import GSTR2BReader, GSTR2BInvoice, SupplierFilingInfo, ITCSummary


# =============================================================================
# A. Instantiation
# =============================================================================

class TestInstantiation:

    def test_from_api_response(self, minimal_gstr2b_dict):
        reader = GSTR2BReader.from_api_response(minimal_gstr2b_dict)
        assert reader is not None

    def test_from_file(self, tmp_path, minimal_gstr2b_dict):
        p = tmp_path / "gstr2b.json"
        p.write_text(json.dumps(minimal_gstr2b_dict))
        reader = GSTR2BReader.from_file(str(p))
        assert reader is not None

    def test_unwrapped_data_accepted(self):
        """Data without outer 'data' key should still work."""
        raw = {
            "gstin": "24AABMT1234C1Z5",
            "rtnprd": "102024",
            "gendt": "14-11-2024",
            "docdata": {"b2b": []}
        }
        reader = GSTR2BReader.from_api_response(raw)
        assert reader.get_itc_summary().period == "102024"

    def test_wrapped_data_accepted(self, minimal_gstr2b_dict):
        """Data wrapped in 'data' key (API response format)."""
        reader = GSTR2BReader.from_api_response(minimal_gstr2b_dict)
        assert reader.get_itc_summary().period == "102024"

    def test_repr_contains_period(self, gstr2b_reader):
        assert "102024" in repr(gstr2b_reader)

    def test_repr_contains_invoice_count(self, gstr2b_reader):
        r = repr(gstr2b_reader)
        assert "invoices=3" in r

    def test_repr_contains_supplier_count(self, gstr2b_reader):
        r = repr(gstr2b_reader)
        assert "suppliers=3" in r


# =============================================================================
# B. Invoice lookup
# =============================================================================

class TestInvoiceLookup:

    def test_existing_invoice_found(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert result is not None

    def test_existing_invoice_returns_gstr2b_invoice_type(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert isinstance(result, GSTR2BInvoice)

    def test_missing_invoice_returns_none(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "NONEXISTENT/999")
        assert result is None

    def test_wrong_supplier_returns_none(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("24AABXX9999Z1Z9", "SM/001")
        assert result is None

    def test_lookup_case_insensitive_gstin(self, gstr2b_reader):
        """GSTIN lookup should work regardless of input case."""
        result = gstr2b_reader.lookup_invoice("24aabsm1111a1z8", "SM/001")
        assert result is not None

    def test_lookup_strips_whitespace_gstin(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("  24AABSM1111A1Z8  ", "SM/001")
        assert result is not None

    def test_lookup_strips_whitespace_invoice(self, gstr2b_reader):
        result = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "  SM/001  ")
        assert result is not None

    def test_invoice_cgst_correct(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.cgst == 3000.0

    def test_invoice_sgst_correct(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.sgst == 3000.0

    def test_invoice_itc_available_true(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.itc_available is True

    def test_invoice_total_itc(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.total_itc == 6000.0  # cgst 3000 + sgst 3000

    def test_invoice_supplier_gstin_stored(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.supplier_gstin == "24AABSM1111A1Z8"

    def test_invoice_number_stored(self, gstr2b_reader):
        inv = gstr2b_reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        assert inv.invoice_number == "SM/001"

    def test_itc_unavailable_invoice(self):
        """itcavl='N' must set itc_available=False."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{
                    "ctin": "24AABXX1234A1Z1",
                    "suppName": "Blocked Supplier",
                    "suppFilingStatus": "Filed",
                    "inv": [{"inum": "BLK/001", "dt": "01-10-2024", "val": 11200,
                              "pos": "24", "rev": "N", "itcavl": "N",
                              "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]
                }]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        inv = reader.lookup_invoice("24AABXX1234A1Z1", "BLK/001")
        assert inv.itc_available is False

    def test_reverse_charge_flag(self):
        """rev='Y' must set reverse_charge=True."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{
                    "ctin": "24AABRC1234A1Z2",
                    "suppName": "RCM Supplier",
                    "suppFilingStatus": "Filed",
                    "inv": [{"inum": "RCM/001", "dt": "01-10-2024", "val": 11200,
                              "pos": "24", "rev": "Y", "itcavl": "Y",
                              "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]
                }]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        inv = reader.lookup_invoice("24AABRC1234A1Z2", "RCM/001")
        assert inv.reverse_charge is True


# =============================================================================
# C. Supplier index
# =============================================================================

class TestSupplierIndex:

    def test_existing_supplier_found(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert info is not None

    def test_supplier_returns_filing_info_type(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert isinstance(info, SupplierFilingInfo)

    def test_supplier_filing_status_filed(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert info.filing_status == "Filed"

    def test_supplier_name_correct(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert info.name == "Silk Mills Ltd"

    def test_supplier_invoice_count(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert info.invoice_count == 1

    def test_missing_supplier_returns_none(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABXX9999Z1Z9")
        assert info is None

    def test_supplier_case_insensitive_lookup(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24aabsm1111a1z8")
        assert info is not None

    def test_supplier_filing_date_stored(self, gstr2b_reader):
        info = gstr2b_reader.get_supplier_status("24AABSM1111A1Z8")
        assert info.filing_date == "10-11-2024"


# =============================================================================
# D. ITC Summary
# =============================================================================

class TestITCSummary:

    def test_summary_returns_itc_summary_type(self, gstr2b_reader):
        assert isinstance(gstr2b_reader.get_itc_summary(), ITCSummary)

    def test_invoice_count(self, gstr2b_reader):
        summary = gstr2b_reader.get_itc_summary()
        assert summary.invoice_count == 3

    def test_supplier_count(self, gstr2b_reader):
        summary = gstr2b_reader.get_itc_summary()
        assert summary.supplier_count == 3

    def test_period_stored(self, gstr2b_reader):
        assert gstr2b_reader.get_itc_summary().period == "102024"

    def test_generated_date_stored(self, gstr2b_reader):
        assert gstr2b_reader.get_itc_summary().generated_date == "14-11-2024"

    def test_total_cgst_aggregated(self, gstr2b_reader):
        """Silk(3000) + Cotton(1200) + Packaging(600) = 4800."""
        s = gstr2b_reader.get_itc_summary()
        assert s.total_cgst == 4800.0

    def test_total_sgst_aggregated(self, gstr2b_reader):
        s = gstr2b_reader.get_itc_summary()
        assert s.total_sgst == 4800.0

    def test_total_itc_property(self, gstr2b_reader):
        s = gstr2b_reader.get_itc_summary()
        assert s.total_itc == 9600.0   # cgst + sgst (no igst in this fixture)

    def test_itc_unavailable_excluded_from_summary(self):
        """Invoices with itcavl='N' must NOT be counted in ITC totals."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [
                    {
                        "ctin": "24AABAV1234A1Z1", "suppName": "Available",
                        "suppFilingStatus": "Filed",
                        "inv": [{"inum": "AV/001", "dt": "01-10-2024", "val": 11200,
                                  "pos": "24", "rev": "N", "itcavl": "Y",
                                  "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]
                    },
                    {
                        "ctin": "24AABNV1234B1Z2", "suppName": "Not Available",
                        "suppFilingStatus": "Filed",
                        "inv": [{"inum": "NA/001", "dt": "01-10-2024", "val": 11200,
                                  "pos": "24", "rev": "N", "itcavl": "N",
                                  "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]
                    }
                ]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        s = reader.get_itc_summary()
        # Only the "Available" invoice's ITC should count
        assert s.total_cgst == 600.0
        assert s.total_sgst == 600.0

    def test_empty_gstr2b_summary(self):
        """Empty GSTR-2B must return zero totals, not crash."""
        data = {"data": {"rtnprd": "102024", "gendt": "14-11-2024", "docdata": {"b2b": []}}}
        reader = GSTR2BReader.from_api_response(data)
        s = reader.get_itc_summary()
        assert s.total_itc == 0.0
        assert s.invoice_count == 0
        assert s.supplier_count == 0

    def test_get_all_invoices_returns_list(self, gstr2b_reader):
        all_inv = gstr2b_reader.get_all_invoices()
        assert isinstance(all_inv, list)
        assert len(all_inv) == 3


# =============================================================================
# E. GSTIN Verification
# =============================================================================

class TestGSTINVerification:

    def test_active_gstin_status(self, gstr2b_reader):
        result = gstr2b_reader.get_gstin_verification("24AABGT1234A1Z9")
        assert result["status"] == "Active"

    def test_cancelled_gstin_status(self, gstr2b_reader):
        result = gstr2b_reader.get_gstin_verification("24AABCX9999Z1Z9")
        assert result["status"] == "Cancelled"

    def test_unknown_gstin_returns_none(self, gstr2b_reader):
        result = gstr2b_reader.get_gstin_verification("24AABZZ9999Z1Z9")
        assert result is None

    def test_gstin_lookup_case_insensitive(self, gstr2b_reader):
        result = gstr2b_reader.get_gstin_verification("24aabgt1234a1z9")
        assert result is not None

    def test_cancelled_gstin_has_cancellation_date(self, gstr2b_reader):
        result = gstr2b_reader.get_gstin_verification("24AABCX9999Z1Z9")
        assert result["cancellationDate"] == "01-09-2024"


# =============================================================================
# F. Edge cases
# =============================================================================

class TestEdgeCases:

    def test_comment_only_block_skipped(self):
        """Blocks with _comment key but no ctin must be ignored."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [
                    {"_comment": "This is a note block with no data"},
                    {"ctin": "24AABREAL1234A1Z1", "suppName": "Real Supplier",
                     "suppFilingStatus": "Filed",
                     "inv": [{"inum": "REAL/001", "dt": "01-10-2024", "val": 1120,
                               "pos": "24", "rev": "N", "itcavl": "Y",
                               "items": [{"igst": 0, "cgst": 60, "sgst": 60, "cess": 0}]}]}
                ]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        assert reader.get_itc_summary().supplier_count == 1

    def test_invoice_with_no_items_array(self):
        """Invoice with no items[] array — tax should default to 0."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{
                    "ctin": "24AABNI1234A1Z3", "suppName": "No Items",
                    "suppFilingStatus": "Filed",
                    "inv": [{"inum": "NI/001", "dt": "01-10-2024", "val": 1000,
                              "pos": "24", "rev": "N", "itcavl": "Y"}]
                }]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        inv = reader.lookup_invoice("24AABNI1234A1Z3", "NI/001")
        assert inv is not None
        assert inv.total_itc == 0.0

    def test_invoice_with_null_tax_values(self):
        """items[] entries with null/None tax values should not crash."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{
                    "ctin": "24AABNL1234A1Z4", "suppName": "Null Tax",
                    "suppFilingStatus": "Filed",
                    "inv": [{"inum": "NT/001", "dt": "01-10-2024", "val": 1000,
                              "pos": "24", "rev": "N", "itcavl": "Y",
                              "items": [{"igst": None, "cgst": None, "sgst": None, "cess": None}]}]
                }]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        inv = reader.lookup_invoice("24AABNL1234A1Z4", "NT/001")
        assert inv.total_itc == 0.0

    def test_multiple_invoices_same_supplier(self):
        """Supplier with 3 invoices — all must be indexed separately."""
        data = {
            "data": {
                "rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{
                    "ctin": "24AABMX1234A1Z5", "suppName": "Multi Invoice",
                    "suppFilingStatus": "Filed",
                    "inv": [
                        {"inum": "MX/001", "dt": "01-10-2024", "val": 1120, "pos": "24",
                         "rev": "N", "itcavl": "Y", "items": [{"cgst": 60, "sgst": 60, "igst": 0, "cess": 0}]},
                        {"inum": "MX/002", "dt": "05-10-2024", "val": 2240, "pos": "24",
                         "rev": "N", "itcavl": "Y", "items": [{"cgst": 120, "sgst": 120, "igst": 0, "cess": 0}]},
                        {"inum": "MX/003", "dt": "15-10-2024", "val": 3360, "pos": "24",
                         "rev": "N", "itcavl": "Y", "items": [{"cgst": 180, "sgst": 180, "igst": 0, "cess": 0}]},
                    ]
                }]}
            }
        }
        reader = GSTR2BReader.from_api_response(data)
        assert reader.lookup_invoice("24AABMX1234A1Z5", "MX/001") is not None
        assert reader.lookup_invoice("24AABMX1234A1Z5", "MX/002") is not None
        assert reader.lookup_invoice("24AABMX1234A1Z5", "MX/003") is not None
        info = reader.get_supplier_status("24AABMX1234A1Z5")
        assert info.invoice_count == 3


# =============================================================================
# G. GSTR2BInvoice.total_itc property
# =============================================================================

class TestGSTR2BInvoiceTotalITC:

    def _make_invoice(self, igst=0, cgst=0, sgst=0, cess=0, itc_available=True):
        return GSTR2BInvoice(
            invoice_number="TEST/001", invoice_date="01-10-2024",
            invoice_value=10000.0, supplier_gstin="24AABTS1234E1Z3",
            supplier_name="Test", place_of_supply="24",
            itc_available=itc_available, reverse_charge=False,
            igst=float(igst), cgst=float(cgst), sgst=float(sgst), cess=float(cess)
        )

    def test_cgst_sgst_only(self):
        inv = self._make_invoice(cgst=600, sgst=600)
        assert inv.total_itc == 1200.0

    def test_igst_only(self):
        inv = self._make_invoice(igst=1200)
        assert inv.total_itc == 1200.0

    def test_all_components(self):
        inv = self._make_invoice(igst=1000, cgst=500, sgst=500, cess=100)
        assert inv.total_itc == 2100.0

    def test_all_zero(self):
        inv = self._make_invoice()
        assert inv.total_itc == 0.0

    def test_rounding(self):
        inv = self._make_invoice(cgst=600.005, sgst=600.005)
        assert inv.total_itc == round(1200.01, 2)


# =============================================================================
# H. ITCSummary.total_itc property
# =============================================================================

class TestITCSummaryTotalITC:

    def _make_summary(self, igst=0, cgst=0, sgst=0, cess=0):
        return ITCSummary(
            period="102024", generated_date="14-11-2024",
            total_igst=float(igst), total_cgst=float(cgst),
            total_sgst=float(sgst), total_cess=float(cess),
            invoice_count=1, supplier_count=1
        )

    def test_total_itc_intrastate(self):
        s = self._make_summary(cgst=5000, sgst=5000)
        assert s.total_itc == 10000.0

    def test_total_itc_interstate(self):
        s = self._make_summary(igst=10000)
        assert s.total_itc == 10000.0

    def test_total_itc_all_zero(self):
        s = self._make_summary()
        assert s.total_itc == 0.0


# =============================================================================
# I. Integration — real testcase JSON
# =============================================================================

class TestRealTestcaseJSON:

    @pytest.fixture
    def real_reader(self):
        base = Path(__file__).parent.parent
        path = base / "testcases" / "mehta_textile_oct2024" / "gstr2b" / "gstr2b_oct2024.json"
        if not path.exists():
            pytest.skip("Testcase GSTR-2B JSON not found")
        return GSTR2BReader.from_file(str(path))

    def test_three_suppliers_indexed(self, real_reader):
        assert real_reader.get_itc_summary().supplier_count == 3

    def test_three_invoices_indexed(self, real_reader):
        assert real_reader.get_itc_summary().invoice_count == 3

    def test_silk_mills_invoice_found(self, real_reader):
        result = real_reader.lookup_invoice("24AABSM1111A1Z8", "SM/2024/1102")
        assert result is not None

    def test_dye_masters_absent(self, real_reader):
        """Core test: Dye Masters must NOT appear in GSTR-2B."""
        result = real_reader.lookup_invoice("24AABDM5678E1Z2", "DM/2024/387")
        assert result is None

    def test_verma_traders_cancelled_gstin(self, real_reader):
        verif = real_reader.get_gstin_verification("24AAFVT9999Z1Z9")
        assert verif is not None
        assert verif["status"] == "Cancelled"

    def test_period_is_102024(self, real_reader):
        assert real_reader.get_itc_summary().period == "102024"
