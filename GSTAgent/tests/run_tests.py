"""
run_tests.py
------------
Standalone test runner using Python's built-in unittest.
No external dependencies — works without pytest installed.

Run:
  cd GSTAgent/tests
  python run_tests.py

Or for verbose:
  python run_tests.py -v
"""

import unittest
import sys
import json
import os
from pathlib import Path
from io import StringIO
from unittest.mock import patch

# Make agent/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from lxml import etree
from tally_parser import TallyParser, SalesVoucher, PurchaseVoucher, InventoryItem
from gstr2b_reader import GSTR2BReader, GSTR2BInvoice, SupplierFilingInfo, ITCSummary
from reconciler import (
    Reconciler, TaxCalculation, ReconciliationResult,
    GSTINValidationResult, ITCReconciliationResult, HSNFlagResult,
    _hsn_matches_item
)

# ---------------------------------------------------------------------------
# Re-export conftest helpers inline (no pytest conftest available in unittest)
# ---------------------------------------------------------------------------

def _wrap_vouchers(blocks):
    inner = "\n".join(f"<TALLYMESSAGE>{b}</TALLYMESSAGE>" for b in blocks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER>
  <BODY><EXPORTDATA>{inner}</EXPORTDATA></BODY>
</ENVELOPE>"""


def _make_sales_xml(blocks, path):
    content = _wrap_vouchers(blocks)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _sales_block(vnum="INV/001", buyer_gstin="24AABPT1234F1Z6", pos="Gujarat",
                  cgst="6000.00", sgst="6000.00", igst="0.00",
                  taxable="100000.00", invoice_total="112000.00",
                  item_name="Cotton Fabric", hsn="5208"):
    tax = ""
    if float(cgst) > 0:
        tax += f"""<LEDGERENTRY><LEDGERNAME>CGST @6%</LEDGERNAME>
          <AMOUNT>-{cgst}</AMOUNT><TAXTYPE>CGST</TAXTYPE></LEDGERENTRY>
          <LEDGERENTRY><LEDGERNAME>SGST @6%</LEDGERNAME>
          <AMOUNT>-{sgst}</AMOUNT><TAXTYPE>SGST</TAXTYPE></LEDGERENTRY>"""
    if float(igst) > 0:
        tax += f"""<LEDGERENTRY><LEDGERNAME>IGST @12%</LEDGERNAME>
          <AMOUNT>-{igst}</AMOUNT><TAXTYPE>IGST</TAXTYPE></LEDGERENTRY>"""
    return f"""<VOUCHER VCHTYPE="Sales" GUID="G-{vnum}">
      <DATE>20241001</DATE><VOUCHERNUMBER>{vnum}</VOUCHERNUMBER>
      <BUYERNAME>Test Buyer</BUYERNAME><BUYERGSTIN>{buyer_gstin}</BUYERGSTIN>
      <PARTYLEDGERNAME>Test Buyer</PARTYLEDGERNAME>
      <PLACEOFDELIVERY>{pos}</PLACEOFDELIVERY>
      <INVOICETOTALAMOUNT>{invoice_total}</INVOICETOTALAMOUNT>
      <TAXABLEAMOUNT>{taxable}</TAXABLEAMOUNT>
      <ALLLEDGERENTRIES.LIST>{tax}</ALLLEDGERENTRIES.LIST>
      <ALLINVENTORYENTRIES.LIST>
        <INVENTORYENTRY>
          <STOCKITEMNAME>{item_name}</STOCKITEMNAME>
          <AMOUNT>{taxable}</AMOUNT><ACTUALQTY>1000 Mtr</ACTUALQTY>
          <GSTDETAILS.LIST><GSTDETAIL>
            <HSNCODE>{hsn}</HSNCODE><GSTRATE>12</GSTRATE>
          </GSTDETAIL></GSTDETAILS.LIST>
        </INVENTORYENTRY>
      </ALLINVENTORYENTRIES.LIST>
    </VOUCHER>"""


def _purchase_block(vnum="PUR/001", supplier_gstin="24AABTS1234E1Z3",
                     cgst="3000.00", sgst="3000.00", igst="0.00",
                     taxable="50000.00", invoice_total="56000.00"):
    tax = ""
    if float(cgst) > 0:
        tax += f"""<LEDGERENTRY><LEDGERNAME>CGST Input @6%</LEDGERNAME>
          <AMOUNT>-{cgst}</AMOUNT><TAXTYPE>CGST</TAXTYPE></LEDGERENTRY>
          <LEDGERENTRY><LEDGERNAME>SGST Input @6%</LEDGERNAME>
          <AMOUNT>-{sgst}</AMOUNT><TAXTYPE>SGST</TAXTYPE></LEDGERENTRY>"""
    if float(igst) > 0:
        tax += f"""<LEDGERENTRY><LEDGERNAME>IGST Input @12%</LEDGERNAME>
          <AMOUNT>-{igst}</AMOUNT><TAXTYPE>IGST</TAXTYPE></LEDGERENTRY>"""
    return f"""<VOUCHER VCHTYPE="Purchase" GUID="PG-{vnum}">
      <DATE>20241005</DATE><VOUCHERNUMBER>{vnum}</VOUCHERNUMBER>
      <SUPPLIERNAME>Test Supplier</SUPPLIERNAME>
      <SUPPLIERGSTIN>{supplier_gstin}</SUPPLIERGSTIN>
      <PARTYLEDGERNAME>Test Supplier</PARTYLEDGERNAME>
      <INVOICETOTALAMOUNT>{invoice_total}</INVOICETOTALAMOUNT>
      <TAXABLEAMOUNT>{taxable}</TAXABLEAMOUNT>
      <ALLLEDGERENTRIES.LIST>{tax}</ALLLEDGERENTRIES.LIST>
    </VOUCHER>"""


def _full_gstr2b():
    return {
        "data": {
            "gstin": "24AABMT1234C1Z5", "rtnprd": "102024", "gendt": "14-11-2024",
            "docdata": {"b2b": [
                {"ctin": "24AABSM1111A1Z8", "suppName": "Silk Mills Ltd",
                 "suppFilingStatus": "Filed", "suppFilingDate": "10-11-2024",
                 "inv": [{"inum": "SM/001", "dt": "03-10-2024", "val": 56000,
                           "pos": "24", "rev": "N", "itcavl": "Y",
                           "items": [{"igst": 0, "cgst": 3000, "sgst": 3000, "cess": 0}]}]},
                {"ctin": "24AABCH2222B1Z6", "suppName": "Cotton Hub",
                 "suppFilingStatus": "Filed", "suppFilingDate": "08-11-2024",
                 "inv": [{"inum": "CH/001", "dt": "08-10-2024", "val": 22400,
                           "pos": "24", "rev": "N", "itcavl": "Y",
                           "items": [{"igst": 0, "cgst": 1200, "sgst": 1200, "cess": 0}]}]},
                {"ctin": "24AABPC3333C1Z4", "suppName": "Packaging Co",
                 "suppFilingStatus": "Filed", "suppFilingDate": "09-11-2024",
                 "inv": [{"inum": "PC/001", "dt": "18-10-2024", "val": 11200,
                           "pos": "24", "rev": "N", "itcavl": "Y",
                           "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]},
            ]},
            "gstin_verification": {
                "24AABGT1234A1Z9": {"gstin": "24AABGT1234A1Z9", "tradeName": "Good Buyer",
                                    "status": "Active", "cancellationDate": None},
                "24AABCX9999Z1Z9": {"gstin": "24AABCX9999Z1Z9", "tradeName": "Cancelled Buyer",
                                    "status": "Cancelled", "cancellationDate": "01-09-2024"},
            }
        }
    }


TESTCASE_BASE = Path(__file__).parent.parent / "testcases" / "mehta_textile_oct2024"


# ============================================================================
# TEST CLASSES
# ============================================================================

class TestAmountParsing(unittest.TestCase):
    """TallyParser._amount() — all Tally amount string formats."""

    def test_plain_float(self):
        self.assertEqual(TallyParser._amount("134400.00"), 134400.0)

    def test_negative_amount(self):
        self.assertEqual(TallyParser._amount("-134400.00"), -134400.0)

    def test_indian_comma_format(self):
        self.assertEqual(TallyParser._amount("1,34,400.00"), 134400.0)

    def test_western_comma_format(self):
        self.assertEqual(TallyParser._amount("134,400.00"), 134400.0)

    def test_integer_string(self):
        self.assertEqual(TallyParser._amount("50000"), 50000.0)

    def test_zero(self):
        self.assertEqual(TallyParser._amount("0"), 0.0)

    def test_empty_string(self):
        self.assertEqual(TallyParser._amount(""), 0.0)

    def test_none_input(self):
        self.assertEqual(TallyParser._amount(None), 0.0)

    def test_invalid_returns_zero(self):
        self.assertEqual(TallyParser._amount("N/A"), 0.0)

    def test_small_decimal(self):
        self.assertEqual(TallyParser._amount("2520.50"), 2520.5)


class TestCleanGSTIN(unittest.TestCase):
    """TallyParser._clean_gstin() normalisation."""

    def test_already_clean(self):
        self.assertEqual(TallyParser._clean_gstin("27AABMT1234C1Z5"), "27AABMT1234C1Z5")

    def test_lowercase_converted(self):
        self.assertEqual(TallyParser._clean_gstin("27aabmt1234c1z5"), "27AABMT1234C1Z5")

    def test_leading_trailing_spaces(self):
        self.assertEqual(TallyParser._clean_gstin("  27AABMT1234C1Z5  "), "27AABMT1234C1Z5")

    def test_internal_spaces_removed(self):
        self.assertEqual(TallyParser._clean_gstin("27 AABMT 1234C 1Z5"), "27AABMT1234C1Z5")

    def test_empty_string(self):
        self.assertEqual(TallyParser._clean_gstin(""), "")


class TestTextExtraction(unittest.TestCase):
    """TallyParser._text() tag extraction."""

    def _el(self, xml):
        return etree.fromstring(xml)

    def test_present_tag(self):
        el = self._el("<V><DATE>20241001</DATE></V>")
        self.assertEqual(TallyParser._text(el, "DATE"), "20241001")

    def test_missing_tag_returns_none(self):
        el = self._el("<V><DATE>20241001</DATE></V>")
        self.assertIsNone(TallyParser._text(el, "VOUCHERNUMBER"))

    def test_empty_tag_returns_none(self):
        el = self._el("<V><BUYERGSTIN></BUYERGSTIN></V>")
        self.assertIsNone(TallyParser._text(el, "BUYERGSTIN"))

    def test_whitespace_stripped(self):
        el = self._el("<V><NAME>  Sales  </NAME></V>")
        self.assertEqual(TallyParser._text(el, "NAME"), "Sales")


class TestParseSalesVouchers(unittest.TestCase):
    """TallyParser.parse_sales_vouchers() — XML parsing of sales."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def _write_xml(self, blocks, name="test.xml"):
        path = os.path.join(self.tmpdir, name)
        return _make_sales_xml(blocks, path)

    def test_single_voucher_count(self):
        path = self._write_xml([_sales_block()])
        self.assertEqual(len(TallyParser(path).parse_sales_vouchers()), 1)

    def test_voucher_number(self):
        path = self._write_xml([_sales_block(vnum="MTT/OCT/001")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.voucher_number, "MTT/OCT/001")

    def test_buyer_gstin_uppercased(self):
        path = self._write_xml([_sales_block(buyer_gstin="24aabpt1234f1z6")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.buyer_gstin, "24AABPT1234F1Z6")

    def test_intrastate_supply_type(self):
        path = self._write_xml([_sales_block(cgst="6000", sgst="6000", igst="0")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.supply_type, "INTRA")

    def test_interstate_supply_type(self):
        path = self._write_xml([_sales_block(cgst="0", sgst="0", igst="12000",
                                              invoice_total="112000")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.supply_type, "INTER")

    def test_intrastate_tax_amounts(self):
        path = self._write_xml([_sales_block(cgst="7200", sgst="7200", igst="0")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.cgst, 7200.0)
        self.assertEqual(v.sgst, 7200.0)
        self.assertEqual(v.igst, 0.0)

    def test_total_gst_property(self):
        path = self._write_xml([_sales_block(cgst="6000", sgst="6000", igst="0")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.total_gst, 12000.0)

    def test_multiple_vouchers(self):
        path = self._write_xml([
            _sales_block(vnum="INV/001"),
            _sales_block(vnum="INV/002"),
            _sales_block(vnum="INV/003"),
        ])
        vouchers = TallyParser(path).parse_sales_vouchers()
        self.assertEqual(len(vouchers), 3)

    def test_purchase_not_in_sales(self):
        xml = _wrap_vouchers([_sales_block(vnum="S/001"), _purchase_block(vnum="P/001")])
        path = os.path.join(self.tmpdir, "mixed.xml")
        with open(path, 'w') as f: f.write(xml)
        sales = TallyParser(path).parse_sales_vouchers()
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0].voucher_number, "S/001")

    def test_empty_xml_returns_empty_list(self):
        path = os.path.join(self.tmpdir, "empty.xml")
        with open(path, 'w') as f:
            f.write('<?xml version="1.0"?><ENVELOPE><BODY><EXPORTDATA></EXPORTDATA></BODY></ENVELOPE>')
        self.assertEqual(TallyParser(path).parse_sales_vouchers(), [])

    def test_hsn_code_parsed(self):
        path = self._write_xml([_sales_block(hsn="5208")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.items[0].hsn_code, "5208")

    def test_item_name_parsed(self):
        path = self._write_xml([_sales_block(item_name="Cotton Poplin 44 inch")])
        v = TallyParser(path).parse_sales_vouchers()[0]
        self.assertEqual(v.items[0].name, "Cotton Poplin 44 inch")

    @unittest.skipUnless(
        (Path(__file__).parent.parent / "testcases/mehta_textile_oct2024/tally_export/sales_daybook_oct2024.xml").exists(),
        "Testcase XML not found"
    )
    def test_real_testcase_5_sales(self):
        xml_path = TESTCASE_BASE / "tally_export" / "sales_daybook_oct2024.xml"
        self.assertEqual(len(TallyParser(str(xml_path)).parse_sales_vouchers()), 5)

    @unittest.skipUnless(
        (Path(__file__).parent.parent / "testcases/mehta_textile_oct2024/tally_export/purchase_daybook_oct2024.xml").exists(),
        "Testcase XML not found"
    )
    def test_real_testcase_4_purchases(self):
        xml_path = TESTCASE_BASE / "tally_export" / "purchase_daybook_oct2024.xml"
        self.assertEqual(len(TallyParser(str(xml_path)).parse_purchase_vouchers()), 4)


class TestGSTR2BReader(unittest.TestCase):
    """GSTR2BReader — indexing, lookup, supplier status, ITC summary."""

    def setUp(self):
        self.data = _full_gstr2b()
        self.reader = GSTR2BReader.from_api_response(self.data)

    def test_from_api_response_creates_reader(self):
        self.assertIsInstance(self.reader, GSTR2BReader)

    def test_from_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(self.data, f)
            fname = f.name
        reader = GSTR2BReader.from_file(fname)
        self.assertIsNotNone(reader)
        os.unlink(fname)

    def test_existing_invoice_found(self):
        result = self.reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        self.assertIsNotNone(result)

    def test_existing_invoice_is_gstr2b_invoice(self):
        result = self.reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        self.assertIsInstance(result, GSTR2BInvoice)

    def test_missing_invoice_returns_none(self):
        self.assertIsNone(self.reader.lookup_invoice("24AABSM1111A1Z8", "NONEXISTENT/999"))

    def test_wrong_supplier_returns_none(self):
        self.assertIsNone(self.reader.lookup_invoice("24AABXX9999Z1Z9", "SM/001"))

    def test_lookup_case_insensitive_gstin(self):
        self.assertIsNotNone(self.reader.lookup_invoice("24aabsm1111a1z8", "SM/001"))

    def test_lookup_strips_whitespace(self):
        self.assertIsNotNone(self.reader.lookup_invoice("  24AABSM1111A1Z8  ", "  SM/001  "))

    def test_invoice_cgst(self):
        inv = self.reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        self.assertEqual(inv.cgst, 3000.0)

    def test_invoice_total_itc(self):
        inv = self.reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        self.assertEqual(inv.total_itc, 6000.0)

    def test_itc_available_true(self):
        inv = self.reader.lookup_invoice("24AABSM1111A1Z8", "SM/001")
        self.assertTrue(inv.itc_available)

    def test_itc_unavailable(self):
        data = {"data": {"rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [{"ctin": "24AABBLK1234A1Z1", "suppName": "Blocked",
                                      "suppFilingStatus": "Filed",
                                      "inv": [{"inum": "BLK/001", "dt": "01-10-2024",
                                               "val": 1120, "pos": "24", "rev": "N",
                                               "itcavl": "N",
                                               "items": [{"igst": 0, "cgst": 60, "sgst": 60, "cess": 0}]}]}]}}}
        reader = GSTR2BReader.from_api_response(data)
        self.assertFalse(reader.lookup_invoice("24AABBLK1234A1Z1", "BLK/001").itc_available)

    def test_supplier_status_found(self):
        info = self.reader.get_supplier_status("24AABSM1111A1Z8")
        self.assertIsNotNone(info)

    def test_supplier_filing_status(self):
        info = self.reader.get_supplier_status("24AABSM1111A1Z8")
        self.assertEqual(info.filing_status, "Filed")

    def test_supplier_invoice_count(self):
        info = self.reader.get_supplier_status("24AABSM1111A1Z8")
        self.assertEqual(info.invoice_count, 1)

    def test_missing_supplier_returns_none(self):
        self.assertIsNone(self.reader.get_supplier_status("24AABXX9999Z1Z9"))

    def test_itc_summary_invoice_count(self):
        s = self.reader.get_itc_summary()
        self.assertEqual(s.invoice_count, 3)

    def test_itc_summary_supplier_count(self):
        s = self.reader.get_itc_summary()
        self.assertEqual(s.supplier_count, 3)

    def test_itc_summary_total_cgst(self):
        # Silk(3000) + Cotton(1200) + Packaging(600) = 4800
        s = self.reader.get_itc_summary()
        self.assertEqual(s.total_cgst, 4800.0)

    def test_itc_summary_total_itc(self):
        s = self.reader.get_itc_summary()
        self.assertEqual(s.total_itc, 9600.0)

    def test_empty_gstr2b_zero_itc(self):
        data = {"data": {"rtnprd": "102024", "gendt": "14-11-2024", "docdata": {"b2b": []}}}
        reader = GSTR2BReader.from_api_response(data)
        self.assertEqual(reader.get_itc_summary().total_itc, 0.0)

    def test_active_gstin_verification(self):
        result = self.reader.get_gstin_verification("24AABGT1234A1Z9")
        self.assertEqual(result["status"], "Active")

    def test_cancelled_gstin_verification(self):
        result = self.reader.get_gstin_verification("24AABCX9999Z1Z9")
        self.assertEqual(result["status"], "Cancelled")

    def test_unknown_gstin_returns_none(self):
        self.assertIsNone(self.reader.get_gstin_verification("24AABZZ9999Z1Z9"))

    def test_repr_contains_period(self):
        self.assertIn("102024", repr(self.reader))

    def test_comment_block_skipped(self):
        data = {"data": {"rtnprd": "102024", "gendt": "14-11-2024",
                "docdata": {"b2b": [
                    {"_comment": "no data here"},
                    {"ctin": "24AABRX1234A1Z1", "suppName": "Real",
                     "suppFilingStatus": "Filed",
                     "inv": [{"inum": "RX/001", "dt": "01-10-2024", "val": 1120,
                               "pos": "24", "rev": "N", "itcavl": "Y",
                               "items": [{"igst": 0, "cgst": 60, "sgst": 60, "cess": 0}]}]}
                ]}}}
        reader = GSTR2BReader.from_api_response(data)
        self.assertEqual(reader.get_itc_summary().supplier_count, 1)

    @unittest.skipUnless(
        (Path(__file__).parent.parent / "testcases/mehta_textile_oct2024/gstr2b/gstr2b_oct2024.json").exists(),
        "Testcase JSON not found"
    )
    def test_real_gstr2b_silk_mills_found(self):
        path = TESTCASE_BASE / "gstr2b" / "gstr2b_oct2024.json"
        reader = GSTR2BReader.from_file(str(path))
        self.assertIsNotNone(reader.lookup_invoice("24AABSM1111A1Z8", "SM/2024/1102"))

    @unittest.skipUnless(
        (Path(__file__).parent.parent / "testcases/mehta_textile_oct2024/gstr2b/gstr2b_oct2024.json").exists(),
        "Testcase JSON not found"
    )
    def test_real_gstr2b_dye_masters_absent(self):
        path = TESTCASE_BASE / "gstr2b" / "gstr2b_oct2024.json"
        reader = GSTR2BReader.from_file(str(path))
        self.assertIsNone(reader.lookup_invoice("24AABDM5678E1Z2", "DM/2024/387"))


class TestTaxCalculation(unittest.TestCase):
    """TaxCalculation dataclass properties."""

    def _tc(self, out_igst=0, out_cgst=0, out_sgst=0,
            itc_igst=0, itc_cgst=0, itc_sgst=0):
        return TaxCalculation(
            output_igst=out_igst, output_cgst=out_cgst, output_sgst=out_sgst,
            itc_igst=itc_igst, itc_cgst=itc_cgst, itc_sgst=itc_sgst,
            at_risk_igst=0, at_risk_cgst=0, at_risk_sgst=0
        )

    def test_net_igst(self):
        self.assertEqual(self._tc(out_igst=10000, itc_igst=4000).net_igst, 6000.0)

    def test_net_cgst(self):
        self.assertEqual(self._tc(out_cgst=5000, itc_cgst=2000).net_cgst, 3000.0)

    def test_net_sgst(self):
        self.assertEqual(self._tc(out_sgst=5000, itc_sgst=2000).net_sgst, 3000.0)

    def test_net_payable(self):
        tc = self._tc(out_cgst=5000, itc_cgst=2000, out_sgst=5000, itc_sgst=2000)
        self.assertEqual(tc.net_payable, 6000.0)

    def test_net_never_negative(self):
        tc = self._tc(out_cgst=2000, itc_cgst=5000)
        self.assertGreaterEqual(tc.net_cgst, 0.0)

    def test_all_zero(self):
        self.assertEqual(self._tc().net_payable, 0.0)

    def test_mehta_scenario(self):
        tc = TaxCalculation(
            output_igst=10200, output_cgst=28200, output_sgst=28200,
            itc_igst=0, itc_cgst=17580, itc_sgst=17580,
            at_risk_igst=0, at_risk_cgst=2520, at_risk_sgst=2520
        )
        self.assertEqual(tc.net_igst, 10200.0)
        self.assertEqual(tc.net_cgst, 10620.0)
        self.assertEqual(tc.net_sgst, 10620.0)
        self.assertEqual(tc.net_payable, 31440.0)


class TestHSNMatchesItem(unittest.TestCase):
    """_hsn_matches_item() heuristic matching."""

    def test_silk_match(self):    self.assertTrue(_hsn_matches_item("5007", "Raw Silk Dupioni"))
    def test_cotton_match(self):  self.assertTrue(_hsn_matches_item("5208", "Cotton Plain Weave"))
    def test_synth_match(self):   self.assertTrue(_hsn_matches_item("5407", "Synthetic Polyester"))
    def test_linen_match(self):   self.assertTrue(_hsn_matches_item("5309", "Linen Natural 58 inch"))
    def test_dye_match(self):     self.assertTrue(_hsn_matches_item("3204", "Reactive Dyes Blue"))

    def test_synth_hsn_cotton_item(self):
        self.assertFalse(_hsn_matches_item("5407", "Cotton Plain Weave Fabric 60x60"))

    def test_cotton_hsn_silk_item(self):
        self.assertFalse(_hsn_matches_item("5208", "Raw Silk Dupioni 44 inch"))

    def test_unknown_hsn_passes(self):
        self.assertTrue(_hsn_matches_item("9999", "Mystery Item"))

    def test_8digit_hsn_uses_prefix(self):
        self.assertTrue(_hsn_matches_item("52081200", "Cotton Fabric"))


class TestReconcilerRun(unittest.TestCase):
    """Reconciler.run() — integration tests using minimal fixtures."""

    def setUp(self):
        self.gstr2b = _full_gstr2b()
        self.reader = GSTR2BReader.from_api_response(self.gstr2b)

    def _make_sales(self, gstin, vnum="INV/001", hsn="5208", item="Cotton Fabric"):
        return [SalesVoucher(
            "20241001", vnum, "G1", "Buyer", gstin, "Gujarat",
            100000, 112000, 6000, 6000, 0, "INTRA",
            items=[InventoryItem(item, hsn, 12.0, "1000 Mtr", "100/Mtr", 100000)]
        )]

    def _make_purchases(self, gstin, vnum, cgst, sgst):
        return [PurchaseVoucher(
            "20241003", vnum, "PG1", "Supplier", gstin,
            (cgst + sgst) / 0.12, (cgst + sgst) * 1.1,
            float(cgst), float(sgst), 0.0
        )]

    def _run(self, sales, purchases):
        return Reconciler(sales, purchases, self.reader, "24AABMT1234C1Z5", "102024").run()

    def test_clean_status(self):
        sales = self._make_sales("24AABGT1234A1Z9")
        purchases = self._make_purchases("24AABSM1111A1Z8", "SM/001", 3000, 3000)
        result = self._run(sales, purchases)
        self.assertEqual(result.status, "CLEAN")

    def test_cancelled_gstin_critical(self):
        sales = self._make_sales("24AABCX9999Z1Z9")
        result = self._run(sales, [])
        self.assertEqual(result.status, "CRITICAL")
        self.assertTrue(result.has_critical_issues)

    def test_missing_itc_issues_found(self):
        purchases = self._make_purchases("24AABXX9999Z1Z9", "MISSING/001", 2400, 2400)
        result = self._run([], purchases)
        self.assertEqual(result.status, "ISSUES_FOUND")

    def test_hsn_mismatch_non_critical(self):
        sales = self._make_sales("24AABGT1234A1Z9", hsn="5407", item="Cotton Plain Weave Fabric")
        result = self._run(sales, [])
        self.assertFalse(result.has_critical_issues)

    def test_empty_run_clean(self):
        result = self._run([], [])
        self.assertEqual(result.status, "CLEAN")
        self.assertEqual(result.tax_calc.net_payable, 0.0)

    def test_returns_reconciliation_result(self):
        result = self._run([], [])
        self.assertIsInstance(result, ReconciliationResult)

    def test_gstin_stored(self):
        result = self._run([], [])
        self.assertEqual(result.gstin, "24AABMT1234C1Z5")

    def test_period_stored(self):
        result = self._run([], [])
        self.assertEqual(result.period, "102024")

    def test_net_payable_calculation(self):
        sales = [SalesVoucher("20241001", "INV/1", "G1", "B", "24AABGT1234A1Z9",
                               "GJ", 100000, 120000, 10000, 10000, 0, "INTRA")]
        purchases = self._make_purchases("24AABSM1111A1Z8", "SM/001", 3000, 3000)
        result = self._run(sales, purchases)
        # Out CGST 10000 - ITC CGST 3000 = 7000; same SGST; total = 14000
        self.assertEqual(result.tax_calc.net_payable, 14000.0)


class TestRealTestcaseEndToEnd(unittest.TestCase):
    """Full pipeline on Mehta Textile testcase data."""

    @classmethod
    def setUpClass(cls):
        sales_xml = TESTCASE_BASE / "tally_export" / "sales_daybook_oct2024.xml"
        purchase_xml = TESTCASE_BASE / "tally_export" / "purchase_daybook_oct2024.xml"
        gstr2b_json = TESTCASE_BASE / "gstr2b" / "gstr2b_oct2024.json"
        if not all(p.exists() for p in [sales_xml, purchase_xml, gstr2b_json]):
            raise unittest.SkipTest("Testcase files not found")
        from tally_parser import TallyParser
        sales = TallyParser(str(sales_xml)).parse_sales_vouchers()
        purchases = TallyParser(str(purchase_xml)).parse_purchase_vouchers()
        reader = GSTR2BReader.from_file(str(gstr2b_json))
        # Suppress print output during reconciler run
        with patch('builtins.print'):
            cls.result = Reconciler(sales, purchases, reader, "24AABMT1234C1Z5", "102024").run()

    def test_status_critical(self):
        self.assertEqual(self.result.status, "CRITICAL")

    def test_exactly_three_issues(self):
        self.assertEqual(self.result.issue_count, 3)

    def test_one_cancelled_gstin(self):
        self.assertEqual(len(self.result.gstin_issues), 1)
        self.assertEqual(self.result.gstin_issues[0].status, "Cancelled")

    def test_verma_traders_flagged(self):
        self.assertEqual(self.result.gstin_issues[0].gstin, "24AAFVT9999Z1Z9")

    def test_one_missing_itc(self):
        missing = [r for r in self.result.itc_results if r.status == "MISSING_FROM_GSTR2B"]
        self.assertEqual(len(missing), 1)

    def test_dye_masters_itc_at_risk_5040(self):
        missing = [r for r in self.result.itc_results if r.status == "MISSING_FROM_GSTR2B"]
        self.assertEqual(missing[0].itc_at_risk, 5040.0)

    def test_one_hsn_flag(self):
        self.assertEqual(len(self.result.hsn_flags), 1)

    def test_hsn_flag_on_invoice_003(self):
        self.assertEqual(self.result.hsn_flags[0].invoice_number, "MTT/OCT/003")

    def test_hsn_suggests_5208(self):
        self.assertEqual(self.result.hsn_flags[0].suggested_hsn, "5208")

    def test_net_payable_31440(self):
        self.assertEqual(self.result.tax_calc.net_payable, 31440.0)

    def test_three_matched_invoices(self):
        matched = [r for r in self.result.itc_results if r.status == "MATCHED"]
        self.assertEqual(len(matched), 3)

    def test_total_sales_555000(self):
        self.assertEqual(self.result.total_sales_value, 555000.0)

    def test_five_sales_vouchers(self):
        self.assertEqual(len(self.result.sales_vouchers), 5)

    def test_four_purchase_vouchers(self):
        self.assertEqual(len(self.result.purchase_vouchers), 4)


# ============================================================================
# RUNNER
# ============================================================================

if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestAmountParsing,
        TestCleanGSTIN,
        TestTextExtraction,
        TestParseSalesVouchers,
        TestGSTR2BReader,
        TestTaxCalculation,
        TestHSNMatchesItem,
        TestReconcilerRun,
        TestRealTestcaseEndToEnd,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
