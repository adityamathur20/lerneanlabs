"""
tally_parser.py
---------------
Parses Tally ERP 9 / TallyPrime XML daybook exports into clean Python dicts.

Why lxml:
  - C-based parser: 5-10x faster than stdlib xml.etree
  - Full XPath 1.0 support: clean querying of nested Tally tags
  - Handles Tally's quirks: empty tags, inconsistent whitespace, & in ledger names

Usage:
  from tally_parser import TallyParser
  parser = TallyParser("sales_daybook_oct2024.xml")
  vouchers = parser.parse_sales_vouchers()
  purchases = parser.parse_purchase_vouchers()
"""

from lxml import etree
from dataclasses import dataclass, field
from typing import Optional
import re


# ---------------------------------------------------------------------------
# Data classes — typed containers for parsed Tally data
# ---------------------------------------------------------------------------

@dataclass
class TaxEntry:
    """Represents one tax ledger line inside a voucher (CGST/SGST/IGST)."""
    tax_type: str          # "CGST", "SGST", "IGST"
    rate: float            # 6.0, 12.0, 18.0 etc
    amount: float          # absolute value in INR


@dataclass
class InventoryItem:
    """One stock item line within a voucher."""
    name: str
    hsn_code: str
    gst_rate: float
    quantity: str          # "2500 Mtr", "1000 Kg" — kept as string (unit varies)
    rate_per_unit: str     # "48.00/Mtr"
    taxable_value: float


@dataclass
class SalesVoucher:
    """A fully parsed sales transaction from Tally daybook XML."""
    date: str              # "20241001" → we keep as-is, convert later
    voucher_number: str    # "MTT/OCT/001"
    guid: str
    buyer_name: str
    buyer_gstin: str       # Raw GSTIN as entered in Tally — may be wrong/cancelled
    place_of_supply: str   # State name e.g. "Gujarat", "Maharashtra"
    taxable_value: float
    invoice_total: float
    cgst: float
    sgst: float
    igst: float
    supply_type: str       # "INTRA" or "INTER" (derived from CGST/IGST presence)
    items: list[InventoryItem] = field(default_factory=list)

    @property
    def total_gst(self) -> float:
        return self.cgst + self.sgst + self.igst


@dataclass
class PurchaseVoucher:
    """A fully parsed purchase transaction from Tally daybook XML."""
    date: str
    voucher_number: str    # Supplier's invoice number (from VOUCHERNUMBER tag)
    guid: str
    supplier_name: str
    supplier_gstin: str
    taxable_value: float
    invoice_total: float
    cgst: float
    sgst: float
    igst: float
    items: list[InventoryItem] = field(default_factory=list)

    @property
    def total_gst(self) -> float:
        return self.cgst + self.sgst + self.igst

    @property
    def itc_claimable(self) -> float:
        """ITC = total GST paid on this purchase."""
        return self.total_gst


# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------

class TallyParser:
    """
    Parses Tally ERP 9 / TallyPrime XML exports.

    Tally XML structure (relevant hierarchy):
      ENVELOPE
        BODY
          EXPORTDATA
            TALLYMESSAGE           ← one per voucher
              VOUCHER [VCHTYPE]    ← "Sales" or "Purchase"
                DATE
                VOUCHERNUMBER
                BUYERGSTIN / SUPPLIERGSTIN
                ALLLEDGERENTRIES.LIST
                  LEDGERENTRY      ← repeated: party, sales/purchase, CGST, SGST, IGST
                    LEDGERNAME
                    AMOUNT
                    TAXTYPE        ← "CGST", "SGST", "IGST" (only on tax ledgers)
                    TAXRATE
                ALLINVENTORYENTRIES.LIST
                  INVENTORYENTRY   ← repeated per stock item
                    STOCKITEMNAME
                    AMOUNT
                    ACTUALQTY
                    GSTDETAILS.LIST
                      GSTDETAIL
                        HSNCODE
                        GSTRATE
    """

    def __init__(self, xml_path: str):
        self.xml_path = xml_path
        # lxml parse — recover=True handles minor Tally XML quirks
        self.tree = etree.parse(xml_path, parser=etree.XMLParser(recover=True))
        self.root = self.tree.getroot()

    # -----------------------------------------------------------------------
    # Public methods
    # -----------------------------------------------------------------------

    def parse_sales_vouchers(self) -> list[SalesVoucher]:
        """Extract all Sales type vouchers from the XML."""
        vouchers = []
        # XPath: find all VOUCHER elements with VCHTYPE="Sales"
        for voucher_el in self.root.xpath('//VOUCHER[@VCHTYPE="Sales"]'):
            try:
                v = self._parse_sales_voucher(voucher_el)
                vouchers.append(v)
            except Exception as e:
                vnum = self._text(voucher_el, 'VOUCHERNUMBER') or 'UNKNOWN'
                print(f"  [WARN] Could not parse sales voucher {vnum}: {e}")
        return vouchers

    def parse_purchase_vouchers(self) -> list[PurchaseVoucher]:
        """Extract all Purchase type vouchers from the XML."""
        vouchers = []
        for voucher_el in self.root.xpath('//VOUCHER[@VCHTYPE="Purchase"]'):
            try:
                v = self._parse_purchase_voucher(voucher_el)
                vouchers.append(v)
            except Exception as e:
                vnum = self._text(voucher_el, 'VOUCHERNUMBER') or 'UNKNOWN'
                print(f"  [WARN] Could not parse purchase voucher {vnum}: {e}")
        return vouchers

    # -----------------------------------------------------------------------
    # Private parsing helpers
    # -----------------------------------------------------------------------

    def _parse_sales_voucher(self, el: etree._Element) -> SalesVoucher:
        """Parse one <VOUCHER VCHTYPE="Sales"> element."""
        date = self._text(el, 'DATE') or ''
        vnum = self._text(el, 'VOUCHERNUMBER') or ''
        guid = el.get('GUID') or self._text(el, 'GUID') or ''
        buyer_name = self._text(el, 'BUYERNAME') or self._text(el, 'PARTYLEDGERNAME') or ''
        buyer_gstin = self._clean_gstin(self._text(el, 'BUYERGSTIN') or '')
        pos = self._text(el, 'PLACEOFDELIVERY') or ''
        invoice_total = self._amount(self._text(el, 'INVOICETOTALAMOUNT'))
        taxable_val = self._amount(self._text(el, 'TAXABLEAMOUNT'))

        # Extract tax amounts from ledger entries
        cgst, sgst, igst = self._extract_tax_from_ledgers(el)

        # Determine supply type
        supply_type = 'INTER' if igst > 0 else 'INTRA'

        # Parse inventory items
        items = self._parse_inventory_items(el)

        return SalesVoucher(
            date=date,
            voucher_number=vnum,
            guid=guid,
            buyer_name=buyer_name,
            buyer_gstin=buyer_gstin,
            place_of_supply=pos,
            taxable_value=taxable_val,
            invoice_total=invoice_total,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            supply_type=supply_type,
            items=items
        )

    def _parse_purchase_voucher(self, el: etree._Element) -> PurchaseVoucher:
        """Parse one <VOUCHER VCHTYPE="Purchase"> element."""
        date = self._text(el, 'DATE') or ''
        vnum = self._text(el, 'VOUCHERNUMBER') or ''
        guid = el.get('GUID') or self._text(el, 'GUID') or ''
        supplier_name = self._text(el, 'SUPPLIERNAME') or self._text(el, 'PARTYLEDGERNAME') or ''
        supplier_gstin = self._clean_gstin(self._text(el, 'SUPPLIERGSTIN') or '')
        invoice_total = self._amount(self._text(el, 'INVOICETOTALAMOUNT'))
        taxable_val = self._amount(self._text(el, 'TAXABLEAMOUNT'))

        cgst, sgst, igst = self._extract_tax_from_ledgers(el)
        items = self._parse_inventory_items(el)

        return PurchaseVoucher(
            date=date,
            voucher_number=vnum,
            guid=guid,
            supplier_name=supplier_name,
            supplier_gstin=supplier_gstin,
            taxable_value=taxable_val,
            invoice_total=invoice_total,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            items=items
        )

    def _extract_tax_from_ledgers(self, voucher_el: etree._Element) -> tuple[float, float, float]:
        """
        Walk ledger entries to extract CGST, SGST, IGST amounts.
        Tally stores tax as separate ledger lines with TAXTYPE tag.

        Returns: (cgst, sgst, igst) as positive floats
        """
        cgst = sgst = igst = 0.0

        for ledger in voucher_el.xpath('.//LEDGERENTRY'):
            tax_type = self._text(ledger, 'TAXTYPE') or ''
            amount_str = self._text(ledger, 'AMOUNT') or '0'
            amount = abs(self._amount(amount_str))  # always positive

            tax_type_upper = tax_type.upper().strip()
            if tax_type_upper == 'CGST':
                cgst += amount
            elif tax_type_upper == 'SGST':
                sgst += amount
            elif tax_type_upper == 'IGST':
                igst += amount

        # Fallback: if TAXTYPE not present, infer from LEDGERNAME
        # e.g. "CGST @6%", "IGST @12%"
        if cgst == 0 and sgst == 0 and igst == 0:
            for ledger in voucher_el.xpath('.//LEDGERENTRY'):
                name = (self._text(ledger, 'LEDGERNAME') or '').upper()
                amount = abs(self._amount(self._text(ledger, 'AMOUNT') or '0'))
                if 'CGST' in name:
                    cgst += amount
                elif 'SGST' in name:
                    sgst += amount
                elif 'IGST' in name:
                    igst += amount

        return round(cgst, 2), round(sgst, 2), round(igst, 2)

    def _parse_inventory_items(self, voucher_el: etree._Element) -> list[InventoryItem]:
        """Parse stock item lines within a voucher."""
        items = []
        for inv in voucher_el.xpath('.//INVENTORYENTRY'):
            name = self._text(inv, 'STOCKITEMNAME') or ''
            qty = self._text(inv, 'ACTUALQTY') or ''
            rate = self._text(inv, 'RATE') or ''
            amount = self._amount(self._text(inv, 'AMOUNT') or '0')

            # GST details nested inside inventory entry
            hsn = ''
            gst_rate = 0.0
            gst_detail = inv.find('.//GSTDETAIL')
            if gst_detail is not None:
                hsn = self._text(gst_detail, 'HSNCODE') or ''
                gst_rate = self._amount(self._text(gst_detail, 'GSTRATE') or '0')

            if name:
                items.append(InventoryItem(
                    name=name,
                    hsn_code=hsn,
                    gst_rate=gst_rate,
                    quantity=qty.strip(),
                    rate_per_unit=rate.strip(),
                    taxable_value=abs(amount)
                ))
        return items

    # -----------------------------------------------------------------------
    # Low-level helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _text(el: etree._Element, tag: str) -> Optional[str]:
        """Get text of first matching child tag. Returns None if missing."""
        found = el.find(tag)
        if found is not None and found.text:
            return found.text.strip()
        return None

    @staticmethod
    def _amount(value: Optional[str]) -> float:
        """
        Parse Tally amount strings to float.
        Handles: "134400.00", "-134400.00", "134,400.00", ""
        """
        if not value:
            return 0.0
        # Remove commas (Indian number format)
        cleaned = value.replace(',', '').strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _clean_gstin(gstin: str) -> str:
        """Normalize GSTIN: uppercase, strip spaces."""
        return re.sub(r'\s+', '', gstin).upper()
