"""
test_tally_parser.py
--------------------
Unit tests for TallyParser — covers XML parsing, tax extraction,
inventory parsing, edge cases, and helper utilities.

Test groups:
  A. _amount()         — amount string parsing
  B. _clean_gstin()    — GSTIN normalisation
  C. _text()           — tag text extraction
  D. parse_sales_vouchers()   — full sales XML parsing
  E. parse_purchase_vouchers() — full purchase XML parsing
  F. _extract_tax_from_ledgers() — tax extraction (TAXTYPE + fallback)
  G. _parse_inventory_items()  — HSN, qty, rate extraction
  H. Edge cases        — malformed XML, missing tags, empty files
"""

import pytest
import sys
from pathlib import Path
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tally_parser import TallyParser, SalesVoucher, PurchaseVoucher, InventoryItem

# Pull XML builder helpers from conftest (imported automatically by pytest)
from conftest import (
    _wrap_vouchers, _make_sales_voucher_xml, _make_purchase_voucher_xml
)


# =============================================================================
# A. _amount() — amount string parsing
# =============================================================================

class TestAmountParsing:
    """TallyParser._amount() must handle all Tally amount formats."""

    def test_plain_float(self):
        assert TallyParser._amount("134400.00") == 134400.0

    def test_negative_amount(self):
        assert TallyParser._amount("-134400.00") == -134400.0

    def test_indian_comma_format(self):
        """₹1,34,400.00 — Indian number system uses commas differently."""
        assert TallyParser._amount("1,34,400.00") == 134400.0

    def test_western_comma_format(self):
        assert TallyParser._amount("134,400.00") == 134400.0

    def test_integer_string(self):
        assert TallyParser._amount("50000") == 50000.0

    def test_zero(self):
        assert TallyParser._amount("0") == 0.0
        assert TallyParser._amount("0.00") == 0.0

    def test_empty_string(self):
        assert TallyParser._amount("") == 0.0

    def test_none_input(self):
        assert TallyParser._amount(None) == 0.0

    def test_whitespace_only(self):
        assert TallyParser._amount("   ") == 0.0

    def test_invalid_string_returns_zero(self):
        assert TallyParser._amount("N/A") == 0.0

    def test_small_decimal(self):
        assert TallyParser._amount("2520.50") == 2520.5


# =============================================================================
# B. _clean_gstin() — GSTIN normalisation
# =============================================================================

class TestCleanGSTIN:
    """GSTIN must be uppercased and stripped of whitespace."""

    def test_already_clean(self):
        assert TallyParser._clean_gstin("27AABMT1234C1Z5") == "27AABMT1234C1Z5"

    def test_lowercase_converted(self):
        assert TallyParser._clean_gstin("27aabmt1234c1z5") == "27AABMT1234C1Z5"

    def test_leading_trailing_spaces(self):
        assert TallyParser._clean_gstin("  27AABMT1234C1Z5  ") == "27AABMT1234C1Z5"

    def test_internal_spaces_removed(self):
        assert TallyParser._clean_gstin("27 AABMT 1234C 1Z5") == "27AABMT1234C1Z5"

    def test_empty_string(self):
        assert TallyParser._clean_gstin("") == ""

    def test_mixed_case_with_spaces(self):
        assert TallyParser._clean_gstin("  24aAbMt1234C1z5  ") == "24AABMT1234C1Z5"


# =============================================================================
# C. _text() — tag text extraction
# =============================================================================

class TestTextExtraction:
    """_text() must return stripped text or None for missing/empty tags."""

    def _el(self, xml_str: str):
        return etree.fromstring(xml_str)

    def test_present_tag(self):
        el = self._el("<VOUCHER><DATE>20241001</DATE></VOUCHER>")
        assert TallyParser._text(el, "DATE") == "20241001"

    def test_missing_tag_returns_none(self):
        el = self._el("<VOUCHER><DATE>20241001</DATE></VOUCHER>")
        assert TallyParser._text(el, "VOUCHERNUMBER") is None

    def test_empty_tag_returns_none(self):
        el = self._el("<VOUCHER><BUYERGSTIN></BUYERGSTIN></VOUCHER>")
        assert TallyParser._text(el, "BUYERGSTIN") is None

    def test_whitespace_stripped(self):
        el = self._el("<VOUCHER><LEDGERNAME>  Sales @12%  </LEDGERNAME></VOUCHER>")
        assert TallyParser._text(el, "LEDGERNAME") == "Sales @12%"

    def test_tag_with_only_whitespace_returns_none(self):
        el = self._el("<VOUCHER><DATE>   </DATE></VOUCHER>")
        # lxml .text returns "   " — stripped is "" — falsy → None
        assert TallyParser._text(el, "DATE") is None


# =============================================================================
# D. parse_sales_vouchers()
# =============================================================================

class TestParseSalesVouchers:

    def test_single_intrastate_voucher_count(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        vouchers = parser.parse_sales_vouchers()
        assert len(vouchers) == 1

    def test_single_voucher_is_sales_voucher_type(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert isinstance(v, SalesVoucher)

    def test_voucher_number_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.voucher_number == "INV/001"

    def test_buyer_gstin_uppercased(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.buyer_gstin == "24AABPT1234F1Z6"

    def test_intrastate_tax_amounts(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.cgst == 6000.0
        assert v.sgst == 6000.0
        assert v.igst == 0.0

    def test_intrastate_supply_type(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.supply_type == "INTRA"

    def test_interstate_supply_type(self, single_interstate_sales_xml):
        parser = TallyParser(single_interstate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.supply_type == "INTER"

    def test_interstate_igst_amount(self, single_interstate_sales_xml):
        parser = TallyParser(single_interstate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.igst == 12000.0
        assert v.cgst == 0.0
        assert v.sgst == 0.0

    def test_total_gst_property_intrastate(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.total_gst == 12000.0   # cgst + sgst

    def test_total_gst_property_interstate(self, single_interstate_sales_xml):
        parser = TallyParser(single_interstate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.total_gst == 12000.0   # igst only

    def test_taxable_value_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.taxable_value == 100000.0

    def test_invoice_total_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.invoice_total == 112000.0

    def test_date_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.date == "20241001"

    def test_multiple_vouchers_count(self, multi_sales_xml):
        parser = TallyParser(multi_sales_xml)
        vouchers = parser.parse_sales_vouchers()
        assert len(vouchers) == 3

    def test_multiple_vouchers_numbers(self, multi_sales_xml):
        parser = TallyParser(multi_sales_xml)
        nums = [v.voucher_number for v in parser.parse_sales_vouchers()]
        assert nums == ["INV/001", "INV/002", "INV/003"]

    def test_purchase_vouchers_not_included_in_sales(self, make_sales_xml, make_purchase_xml, tmp_path):
        """Sales parser must not pick up Purchase vouchers."""
        sales_block = _make_sales_voucher_xml(vnum="S/001")
        purchase_block = _make_purchase_voucher_xml(vnum="P/001")
        # Write both into same XML
        content = f"""<?xml version="1.0"?>
<ENVELOPE><HEADER/><BODY><EXPORTDATA>
  <TALLYMESSAGE>{sales_block}</TALLYMESSAGE>
  <TALLYMESSAGE>{purchase_block}</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "mixed.xml"
        p.write_text(content)
        parser = TallyParser(str(p))
        sales = parser.parse_sales_vouchers()
        assert len(sales) == 1
        assert sales[0].voucher_number == "S/001"

    def test_empty_xml_returns_empty_list(self, tmp_path):
        p = tmp_path / "empty.xml"
        p.write_text('<?xml version="1.0"?><ENVELOPE><BODY><EXPORTDATA></EXPORTDATA></BODY></ENVELOPE>')
        parser = TallyParser(str(p))
        assert parser.parse_sales_vouchers() == []

    def test_buyer_name_falls_back_to_partyledger(self, make_sales_xml, tmp_path):
        """If BUYERNAME is absent, use PARTYLEDGERNAME."""
        xml = f"""<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <VOUCHERNUMBER>FB/001</VOUCHERNUMBER>
    <DATE>20241001</DATE>
    <PARTYLEDGERNAME>Fallback Buyer</PARTYLEDGERNAME>
    <BUYERGSTIN>24AABFB1234Z1Z9</BUYERGSTIN>
    <INVOICETOTALAMOUNT>0.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>0.00</TAXABLEAMOUNT>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "fallback.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        v = parser.parse_sales_vouchers()[0]
        assert v.buyer_name == "Fallback Buyer"


# =============================================================================
# E. parse_purchase_vouchers()
# =============================================================================

class TestParsePurchaseVouchers:

    def test_single_purchase_count(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        assert len(parser.parse_purchase_vouchers()) == 1

    def test_purchase_is_correct_type(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert isinstance(p, PurchaseVoucher)

    def test_supplier_gstin(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert p.supplier_gstin == "24AABTS1234E1Z3"

    def test_supplier_name(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert p.supplier_name == "Test Supplier"

    def test_purchase_tax_amounts(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert p.cgst == 3000.0
        assert p.sgst == 3000.0
        assert p.igst == 0.0

    def test_itc_claimable_property(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert p.itc_claimable == 6000.0   # cgst + sgst

    def test_purchase_taxable_value(self, single_purchase_xml):
        parser = TallyParser(single_purchase_xml)
        p = parser.parse_purchase_vouchers()[0]
        assert p.taxable_value == 50000.0

    def test_supplier_name_fallback_to_partyledger(self, tmp_path):
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Purchase">
    <VOUCHERNUMBER>P/FALLBACK</VOUCHERNUMBER>
    <DATE>20241001</DATE>
    <PARTYLEDGERNAME>Fallback Supplier</PARTYLEDGERNAME>
    <SUPPLIERGSTIN>24AABFS1234Y1Z8</SUPPLIERGSTIN>
    <INVOICETOTALAMOUNT>0.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>0.00</TAXABLEAMOUNT>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "pfallback.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        pv = parser.parse_purchase_vouchers()[0]
        assert pv.supplier_name == "Fallback Supplier"

    def test_sales_not_included_in_purchases(self, multi_sales_xml):
        parser = TallyParser(multi_sales_xml)
        assert parser.parse_purchase_vouchers() == []


# =============================================================================
# F. Tax extraction — TAXTYPE tag + ledger name fallback
# =============================================================================

class TestTaxExtraction:
    """Tax can be identified either via TAXTYPE tag or ledger name — test both."""

    def _parser_from_xml(self, xml: str, tmp_path) -> TallyParser:
        p = tmp_path / "tax_test.xml"
        p.write_text(xml)
        return TallyParser(str(p))

    def test_taxtype_tag_primary_method(self, make_sales_xml):
        """TAXTYPE tag used when present."""
        block = _make_sales_voucher_xml(cgst="7200.00", sgst="7200.00", igst="0.00")
        xml_path = make_sales_xml([block])
        parser = TallyParser(xml_path)
        v = parser.parse_sales_vouchers()[0]
        assert v.cgst == 7200.0
        assert v.sgst == 7200.0

    def test_ledger_name_fallback_cgst(self, tmp_path):
        """When TAXTYPE is absent, infer from ledger name containing 'CGST'."""
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>INV/FALLBACK</VOUCHERNUMBER>
    <INVOICETOTALAMOUNT>11200.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>10000.00</TAXABLEAMOUNT>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERENTRY>
        <LEDGERNAME>CGST @6%</LEDGERNAME>
        <AMOUNT>-600.00</AMOUNT>
      </LEDGERENTRY>
      <LEDGERENTRY>
        <LEDGERNAME>SGST @6%</LEDGERNAME>
        <AMOUNT>-600.00</AMOUNT>
      </LEDGERENTRY>
    </ALLLEDGERENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "fallback_tax.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        v = parser.parse_sales_vouchers()[0]
        assert v.cgst == 600.0
        assert v.sgst == 600.0

    def test_igst_interstate(self, single_interstate_sales_xml):
        parser = TallyParser(single_interstate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert v.igst == 12000.0
        assert v.cgst == 0.0
        assert v.sgst == 0.0

    def test_no_tax_entries_all_zero(self, tmp_path):
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>INV/NOTAX</VOUCHERNUMBER>
    <INVOICETOTALAMOUNT>10000.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>10000.00</TAXABLEAMOUNT>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERENTRY>
        <LEDGERNAME>Exempt Sales</LEDGERNAME>
        <AMOUNT>-10000.00</AMOUNT>
      </LEDGERENTRY>
    </ALLLEDGERENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "notax.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        v = parser.parse_sales_vouchers()[0]
        assert v.cgst == 0.0
        assert v.sgst == 0.0
        assert v.igst == 0.0

    def test_tax_amounts_are_positive(self, make_sales_xml):
        """Tally stores some amounts as negative — parser must return positive."""
        block = _make_sales_voucher_xml(cgst="5000.00", sgst="5000.00")
        parser = TallyParser(make_sales_xml([block]))
        v = parser.parse_sales_vouchers()[0]
        assert v.cgst > 0
        assert v.sgst > 0


# =============================================================================
# G. Inventory item parsing
# =============================================================================

class TestInventoryParsing:

    def test_single_item_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        v = parser.parse_sales_vouchers()[0]
        assert len(v.items) == 1

    def test_item_is_inventory_item_type(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert isinstance(item, InventoryItem)

    def test_hsn_code_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert item.hsn_code == "5208"

    def test_item_name_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert item.name == "Cotton Fabric"

    def test_gst_rate_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert item.gst_rate == 12.0

    def test_quantity_parsed(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert item.quantity == "1000 Mtr"

    def test_taxable_value_is_positive(self, single_intrastate_sales_xml):
        parser = TallyParser(single_intrastate_sales_xml)
        item = parser.parse_sales_vouchers()[0].items[0]
        assert item.taxable_value == 100000.0

    def test_no_inventory_entries_returns_empty_list(self, tmp_path):
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>INV/NOINV</VOUCHERNUMBER>
    <INVOICETOTALAMOUNT>11200.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>10000.00</TAXABLEAMOUNT>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "noinv.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        v = parser.parse_sales_vouchers()[0]
        assert v.items == []


# =============================================================================
# H. Edge cases
# =============================================================================

class TestEdgeCases:

    def test_malformed_xml_recovered(self, tmp_path):
        """lxml recover=True should handle minor XML issues without crashing."""
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>INV/RECOVER</VOUCHERNUMBER>
    <INVOICETOTALAMOUNT>56000.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>50000.00</TAXABLEAMOUNT>
    <UNCLOSED_TAG>some text
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "malformed.xml"
        p.write_text(xml)
        # Should not raise — recover=True handles it
        parser = TallyParser(str(p))
        vouchers = parser.parse_sales_vouchers()
        assert isinstance(vouchers, list)

    def test_ampersand_in_ledger_name(self, tmp_path):
        """Tally sometimes has & in names — XML must handle entity encoding."""
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Purchase">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>P/AMP/001</VOUCHERNUMBER>
    <SUPPLIERNAME>Dyes &amp; Chemicals Ltd</SUPPLIERNAME>
    <SUPPLIERGSTIN>24AABDC1234A1Z5</SUPPLIERGSTIN>
    <INVOICETOTALAMOUNT>11200.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>10000.00</TAXABLEAMOUNT>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "ampersand.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        pv = parser.parse_purchase_vouchers()[0]
        assert "Dyes" in pv.supplier_name

    def test_gstin_with_lowercase_normalized(self, make_sales_xml):
        block = _make_sales_voucher_xml(buyer_gstin="27aabmt1234c1z5")
        parser = TallyParser(make_sales_xml([block]))
        v = parser.parse_sales_vouchers()[0]
        assert v.buyer_gstin == "27AABMT1234C1Z5"

    def test_multiple_items_in_voucher(self, tmp_path):
        xml = """<?xml version="1.0"?>
<ENVELOPE><BODY><EXPORTDATA>
<TALLYMESSAGE>
  <VOUCHER VCHTYPE="Sales">
    <DATE>20241001</DATE>
    <VOUCHERNUMBER>INV/MULTI</VOUCHERNUMBER>
    <INVOICETOTALAMOUNT>22400.00</INVOICETOTALAMOUNT>
    <TAXABLEAMOUNT>20000.00</TAXABLEAMOUNT>
    <ALLINVENTORYENTRIES.LIST>
      <INVENTORYENTRY>
        <STOCKITEMNAME>Item A</STOCKITEMNAME>
        <AMOUNT>10000.00</AMOUNT>
        <ACTUALQTY>100 Mtr</ACTUALQTY>
        <GSTDETAILS.LIST><GSTDETAIL><HSNCODE>5208</HSNCODE><GSTRATE>12</GSTRATE></GSTDETAIL></GSTDETAILS.LIST>
      </INVENTORYENTRY>
      <INVENTORYENTRY>
        <STOCKITEMNAME>Item B</STOCKITEMNAME>
        <AMOUNT>10000.00</AMOUNT>
        <ACTUALQTY>200 Kg</ACTUALQTY>
        <GSTDETAILS.LIST><GSTDETAIL><HSNCODE>5007</HSNCODE><GSTRATE>12</GSTRATE></GSTDETAIL></GSTDETAILS.LIST>
      </INVENTORYENTRY>
    </ALLINVENTORYENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>
</EXPORTDATA></BODY></ENVELOPE>"""
        p = tmp_path / "multiitem.xml"
        p.write_text(xml)
        parser = TallyParser(str(p))
        v = parser.parse_sales_vouchers()[0]
        assert len(v.items) == 2
        assert v.items[0].name == "Item A"
        assert v.items[1].name == "Item B"

    def test_real_testcase_xml_parses_5_sales(self):
        """Integration: real testcase XML must produce 5 sales vouchers."""
        base = Path(__file__).parent.parent
        xml_path = base / "testcases" / "mehta_textile_oct2024" / "tally_export" / "sales_daybook_oct2024.xml"
        if not xml_path.exists():
            pytest.skip("Testcase XML not found")
        parser = TallyParser(str(xml_path))
        vouchers = parser.parse_sales_vouchers()
        assert len(vouchers) == 5

    def test_real_testcase_xml_parses_4_purchases(self):
        """Integration: real testcase XML must produce 4 purchase vouchers."""
        base = Path(__file__).parent.parent
        xml_path = base / "testcases" / "mehta_textile_oct2024" / "tally_export" / "purchase_daybook_oct2024.xml"
        if not xml_path.exists():
            pytest.skip("Testcase XML not found")
        parser = TallyParser(str(xml_path))
        vouchers = parser.parse_purchase_vouchers()
        assert len(vouchers) == 4

    def test_real_testcase_cancelled_gstin_present(self):
        """Integration: Verma Traders (cancelled GSTIN) must appear in sales."""
        base = Path(__file__).parent.parent
        xml_path = base / "testcases" / "mehta_textile_oct2024" / "tally_export" / "sales_daybook_oct2024.xml"
        if not xml_path.exists():
            pytest.skip("Testcase XML not found")
        parser = TallyParser(str(xml_path))
        gstins = [v.buyer_gstin for v in parser.parse_sales_vouchers()]
        assert "24AAFVT9999Z1Z9" in gstins
