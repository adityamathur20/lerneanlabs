"""
conftest.py
-----------
Shared pytest fixtures. Loaded automatically by pytest for all test files.

Fixtures defined here:
  - xml_factory         : builds minimal valid Tally XML strings in-memory
  - sales_xml_path      : writes a sales XML file to tmp_path
  - purchase_xml_path   : writes a purchase XML file to tmp_path
  - minimal_gstr2b_dict : bare-minimum GSTR-2B dict for reader tests
  - full_gstr2b_dict    : full realistic GSTR-2B with 3 suppliers
  - gstr2b_reader       : GSTR2BReader instance from full_gstr2b_dict
  - sample_sales_vouchers   : list of SalesVoucher objects
  - sample_purchase_vouchers: list of PurchaseVoucher objects
"""

import pytest
import sys
from pathlib import Path

# Make agent/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tally_parser import (
    TallyParser, SalesVoucher, PurchaseVoucher, InventoryItem
)
from gstr2b_reader import GSTR2BReader, GSTR2BInvoice


# ---------------------------------------------------------------------------
# XML building helper (used by multiple fixtures)
# ---------------------------------------------------------------------------

def _wrap_vouchers(voucher_xml_blocks: list[str]) -> str:
    """Wrap voucher XML blocks in a minimal valid Tally ENVELOPE."""
    inner = "\n".join(
        f"<TALLYMESSAGE>{block}</TALLYMESSAGE>"
        for block in voucher_xml_blocks
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER>
  <BODY><EXPORTDATA>{inner}</EXPORTDATA></BODY>
</ENVELOPE>"""


def _make_sales_voucher_xml(
    vnum="INV/001",
    date="20241001",
    guid="GUID-001",
    buyer_name="Test Buyer",
    buyer_gstin="27AABCT1234D1Z5",
    pos="Maharashtra",
    invoice_total="112000.00",
    taxable="100000.00",
    cgst="6000.00",
    sgst="6000.00",
    igst="0.00",
    hsn="5208",
    gst_rate="12",
    item_name="Cotton Fabric",
    qty="1000 Mtr",
    rate="100.00/Mtr",
) -> str:
    tax_ledgers = ""
    if float(cgst) > 0:
        tax_ledgers += f"""
        <LEDGERENTRY>
          <LEDGERNAME>CGST @6%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>-{cgst}</AMOUNT>
          <TAXTYPE>CGST</TAXTYPE>
          <TAXRATE>6</TAXRATE>
        </LEDGERENTRY>
        <LEDGERENTRY>
          <LEDGERNAME>SGST @6%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>-{sgst}</AMOUNT>
          <TAXTYPE>SGST</TAXTYPE>
          <TAXRATE>6</TAXRATE>
        </LEDGERENTRY>"""
    if float(igst) > 0:
        tax_ledgers += f"""
        <LEDGERENTRY>
          <LEDGERNAME>IGST @12%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>-{igst}</AMOUNT>
          <TAXTYPE>IGST</TAXTYPE>
          <TAXRATE>12</TAXRATE>
        </LEDGERENTRY>"""

    return f"""
      <VOUCHER VCHTYPE="Sales" GUID="{guid}">
        <DATE>{date}</DATE>
        <VOUCHERNUMBER>{vnum}</VOUCHERNUMBER>
        <BUYERNAME>{buyer_name}</BUYERNAME>
        <BUYERGSTIN>{buyer_gstin}</BUYERGSTIN>
        <PARTYLEDGERNAME>{buyer_name}</PARTYLEDGERNAME>
        <PLACEOFDELIVERY>{pos}</PLACEOFDELIVERY>
        <INVOICETOTALAMOUNT>{invoice_total}</INVOICETOTALAMOUNT>
        <TAXABLEAMOUNT>{taxable}</TAXABLEAMOUNT>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERENTRY>
            <LEDGERNAME>{buyer_name}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{invoice_total}</AMOUNT>
            <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
          </LEDGERENTRY>
          <LEDGERENTRY>
            <LEDGERNAME>Sales @12%</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{taxable}</AMOUNT>
          </LEDGERENTRY>
          {tax_ledgers}
        </ALLLEDGERENTRIES.LIST>
        <ALLINVENTORYENTRIES.LIST>
          <INVENTORYENTRY>
            <STOCKITEMNAME>{item_name}</STOCKITEMNAME>
            <RATE>{rate}</RATE>
            <AMOUNT>{taxable}</AMOUNT>
            <ACTUALQTY>{qty}</ACTUALQTY>
            <GSTDETAILS.LIST>
              <GSTDETAIL>
                <HSNCODE>{hsn}</HSNCODE>
                <GSTRATE>{gst_rate}</GSTRATE>
              </GSTDETAIL>
            </GSTDETAILS.LIST>
          </INVENTORYENTRY>
        </ALLINVENTORYENTRIES.LIST>
      </VOUCHER>"""


def _make_purchase_voucher_xml(
    vnum="PUR/001",
    date="20241005",
    guid="PGUID-001",
    supplier_name="Test Supplier",
    supplier_gstin="24AABTS1234E1Z3",
    invoice_total="56000.00",
    taxable="50000.00",
    cgst="3000.00",
    sgst="3000.00",
    igst="0.00",
    hsn="5007",
    item_name="Raw Silk",
    qty="500 Kg",
    rate="100.00/Kg",
) -> str:
    tax_ledgers = ""
    if float(cgst) > 0:
        tax_ledgers += f"""
        <LEDGERENTRY>
          <LEDGERNAME>CGST Input @6%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{cgst}</AMOUNT>
          <TAXTYPE>CGST</TAXTYPE>
        </LEDGERENTRY>
        <LEDGERENTRY>
          <LEDGERNAME>SGST Input @6%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{sgst}</AMOUNT>
          <TAXTYPE>SGST</TAXTYPE>
        </LEDGERENTRY>"""
    if float(igst) > 0:
        tax_ledgers += f"""
        <LEDGERENTRY>
          <LEDGERNAME>IGST Input @12%</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{igst}</AMOUNT>
          <TAXTYPE>IGST</TAXTYPE>
        </LEDGERENTRY>"""

    return f"""
      <VOUCHER VCHTYPE="Purchase" GUID="{guid}">
        <DATE>{date}</DATE>
        <VOUCHERNUMBER>{vnum}</VOUCHERNUMBER>
        <SUPPLIERNAME>{supplier_name}</SUPPLIERNAME>
        <SUPPLIERGSTIN>{supplier_gstin}</SUPPLIERGSTIN>
        <PARTYLEDGERNAME>{supplier_name}</PARTYLEDGERNAME>
        <INVOICETOTALAMOUNT>{invoice_total}</INVOICETOTALAMOUNT>
        <TAXABLEAMOUNT>{taxable}</TAXABLEAMOUNT>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERENTRY>
            <LEDGERNAME>{supplier_name}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{invoice_total}</AMOUNT>
            <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
          </LEDGERENTRY>
          <LEDGERENTRY>
            <LEDGERNAME>Purchase @12%</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{taxable}</AMOUNT>
          </LEDGERENTRY>
          {tax_ledgers}
        </ALLLEDGERENTRIES.LIST>
        <ALLINVENTORYENTRIES.LIST>
          <INVENTORYENTRY>
            <STOCKITEMNAME>{item_name}</STOCKITEMNAME>
            <RATE>{rate}</RATE>
            <AMOUNT>{taxable}</AMOUNT>
            <ACTUALQTY>{qty}</ACTUALQTY>
            <GSTDETAILS.LIST>
              <GSTDETAIL>
                <HSNCODE>{hsn}</HSNCODE>
                <GSTRATE>12</GSTRATE>
              </GSTDETAIL>
            </GSTDETAILS.LIST>
          </INVENTORYENTRY>
        </ALLINVENTORYENTRIES.LIST>
      </VOUCHER>"""


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_sales_xml(tmp_path):
    """
    Factory fixture: returns a callable that writes sales XML to a temp file.
    Usage: path = make_sales_xml([voucher_xml_1, voucher_xml_2])
    """
    def _factory(voucher_blocks: list[str], filename="sales.xml") -> str:
        content = _wrap_vouchers(voucher_blocks)
        p = tmp_path / filename
        p.write_text(content, encoding="utf-8")
        return str(p)
    return _factory


@pytest.fixture
def make_purchase_xml(tmp_path):
    """Factory fixture for purchase XML files."""
    def _factory(voucher_blocks: list[str], filename="purchases.xml") -> str:
        content = _wrap_vouchers(voucher_blocks)
        p = tmp_path / filename
        p.write_text(content, encoding="utf-8")
        return str(p)
    return _factory


@pytest.fixture
def single_intrastate_sales_xml(make_sales_xml):
    """One clean intra-state sales voucher (CGST+SGST)."""
    block = _make_sales_voucher_xml(
        vnum="INV/001", buyer_gstin="24AABPT1234F1Z6",
        pos="Gujarat", cgst="6000.00", sgst="6000.00", igst="0.00"
    )
    return make_sales_xml([block])


@pytest.fixture
def single_interstate_sales_xml(make_sales_xml):
    """One inter-state sales voucher (IGST only)."""
    block = _make_sales_voucher_xml(
        vnum="INV/002", buyer_gstin="27AABST5678G1Z3",
        pos="Maharashtra", cgst="0.00", sgst="0.00", igst="12000.00",
        invoice_total="112000.00"
    )
    return make_sales_xml([block])


@pytest.fixture
def multi_sales_xml(make_sales_xml):
    """Three sales vouchers: 2 intra-state + 1 inter-state."""
    blocks = [
        _make_sales_voucher_xml(vnum="INV/001", buyer_gstin="24AABPT1234F1Z6",
                                 pos="Gujarat", cgst="6000.00", sgst="6000.00", igst="0.00"),
        _make_sales_voucher_xml(vnum="INV/002", buyer_gstin="27AABST5678G1Z3",
                                 pos="Maharashtra", cgst="0.00", sgst="0.00", igst="12000.00",
                                 invoice_total="112000.00"),
        _make_sales_voucher_xml(vnum="INV/003", buyer_gstin="24AABXT9999H1Z1",
                                 pos="Gujarat", cgst="3000.00", sgst="3000.00", igst="0.00",
                                 taxable="50000.00", invoice_total="56000.00"),
    ]
    return make_sales_xml(blocks)


@pytest.fixture
def single_purchase_xml(make_purchase_xml):
    """One clean purchase voucher."""
    block = _make_purchase_voucher_xml()
    return make_purchase_xml([block])


@pytest.fixture
def minimal_gstr2b_dict():
    """Smallest valid GSTR-2B dict with one supplier and one invoice."""
    return {
        "data": {
            "gstin": "24AABMT1234C1Z5",
            "rtnprd": "102024",
            "gendt": "14-11-2024",
            "docdata": {
                "b2b": [
                    {
                        "ctin": "24AABTS1234E1Z3",
                        "suppName": "Test Supplier",
                        "suppFilingStatus": "Filed",
                        "suppFilingDate": "10-11-2024",
                        "inv": [
                            {
                                "inum": "PUR/001",
                                "dt": "05-10-2024",
                                "val": 56000.00,
                                "pos": "24",
                                "rev": "N",
                                "itcavl": "Y",
                                "items": [
                                    {"rt": 12, "txval": 50000, "igst": 0, "cgst": 3000, "sgst": 3000, "cess": 0}
                                ]
                            }
                        ]
                    }
                ]
            }
        }
    }


@pytest.fixture
def full_gstr2b_dict():
    """
    Realistic GSTR-2B with 3 suppliers:
      - Silk Mills: 1 invoice, matched
      - Cotton Hub: 1 invoice, matched
      - Packaging:  1 invoice, matched
      (Dye Masters is intentionally ABSENT to test missing ITC detection)
    Also includes gstin_verification for 2 GSTINs.
    """
    return {
        "data": {
            "gstin": "24AABMT1234C1Z5",
            "rtnprd": "102024",
            "gendt": "14-11-2024",
            "docdata": {
                "b2b": [
                    {
                        "ctin": "24AABSM1111A1Z8",
                        "suppName": "Silk Mills Ltd",
                        "suppFilingStatus": "Filed",
                        "suppFilingDate": "10-11-2024",
                        "inv": [{"inum": "SM/001", "dt": "03-10-2024", "val": 56000.00,
                                 "pos": "24", "rev": "N", "itcavl": "Y",
                                 "items": [{"igst": 0, "cgst": 3000, "sgst": 3000, "cess": 0}]}]
                    },
                    {
                        "ctin": "24AABCH2222B1Z6",
                        "suppName": "Cotton Hub",
                        "suppFilingStatus": "Filed",
                        "suppFilingDate": "08-11-2024",
                        "inv": [{"inum": "CH/001", "dt": "08-10-2024", "val": 22400.00,
                                 "pos": "24", "rev": "N", "itcavl": "Y",
                                 "items": [{"igst": 0, "cgst": 1200, "sgst": 1200, "cess": 0}]}]
                    },
                    {
                        "ctin": "24AABPC3333C1Z4",
                        "suppName": "Packaging Co",
                        "suppFilingStatus": "Filed",
                        "suppFilingDate": "09-11-2024",
                        "inv": [{"inum": "PC/001", "dt": "18-10-2024", "val": 11200.00,
                                 "pos": "24", "rev": "N", "itcavl": "Y",
                                 "items": [{"igst": 0, "cgst": 600, "sgst": 600, "cess": 0}]}]
                    },
                ]
            },
            "gstin_verification": {
                "24AABGT1234A1Z9": {"gstin": "24AABGT1234A1Z9", "tradeName": "Good Buyer",
                                    "status": "Active", "cancellationDate": None},
                "24AABCX9999Z1Z9": {"gstin": "24AABCX9999Z1Z9", "tradeName": "Cancelled Buyer",
                                    "status": "Cancelled", "cancellationDate": "01-09-2024"},
            }
        }
    }


@pytest.fixture
def gstr2b_reader(full_gstr2b_dict):
    """Ready-to-use GSTR2BReader built from full_gstr2b_dict."""
    return GSTR2BReader.from_api_response(full_gstr2b_dict)


@pytest.fixture
def sample_sales_vouchers():
    """Direct SalesVoucher objects (no XML parsing needed)."""
    return [
        SalesVoucher(
            date="20241001", voucher_number="INV/001", guid="G1",
            buyer_name="Good Buyer", buyer_gstin="24AABGT1234A1Z9",
            place_of_supply="Gujarat",
            taxable_value=100000.0, invoice_total=112000.0,
            cgst=6000.0, sgst=6000.0, igst=0.0, supply_type="INTRA",
            items=[InventoryItem(name="Cotton Fabric", hsn_code="5208",
                                  gst_rate=12.0, quantity="1000 Mtr",
                                  rate_per_unit="100/Mtr", taxable_value=100000.0)]
        ),
        SalesVoucher(
            date="20241005", voucher_number="INV/002", guid="G2",
            buyer_name="Cancelled Buyer", buyer_gstin="24AABCX9999Z1Z9",
            place_of_supply="Gujarat",
            taxable_value=50000.0, invoice_total=56000.0,
            cgst=3000.0, sgst=3000.0, igst=0.0, supply_type="INTRA",
            items=[]
        ),
    ]


@pytest.fixture
def sample_purchase_vouchers():
    """Direct PurchaseVoucher objects covering matched + missing cases."""
    return [
        PurchaseVoucher(
            date="20241003", voucher_number="SM/001", guid="PG1",
            supplier_name="Silk Mills Ltd", supplier_gstin="24AABSM1111A1Z8",
            taxable_value=50000.0, invoice_total=56000.0,
            cgst=3000.0, sgst=3000.0, igst=0.0, items=[]
        ),
        PurchaseVoucher(
            date="20241010", voucher_number="MISSING/001", guid="PG2",
            supplier_name="Ghost Supplier", supplier_gstin="24AABGS9999X1Z1",
            taxable_value=40000.0, invoice_total=44800.0,
            cgst=2400.0, sgst=2400.0, igst=0.0, items=[]
        ),
    ]
