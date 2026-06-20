"""
gstr2b_reader.py
----------------
Reads and indexes GSTR-2B JSON — either from a local file (testcase mode)
or from a live GSP API response (production mode).

GSTR-2B is pure JSON. No third-party library needed — just Python's built-in
json module. The key design decision here is INDEXING:

We build two indexes on load:
  1. invoice_index: {(supplier_gstin, invoice_number) -> invoice_data}
     → O(1) lookup when reconciling against Tally purchase register

  2. supplier_index: {supplier_gstin -> {filing_status, invoices[]}}
     → O(1) lookup to check if a supplier has filed at all

This avoids O(n*m) nested loops during reconciliation (which matters at scale).

GSTR-2B JSON structure (from GSTN/GSP API):
  data.docdata.b2b[]          ← purchases from registered suppliers
    .ctin                     ← supplier GSTIN
    .suppName
    .suppFilingStatus
    .inv[]                    ← list of invoices
      .inum                   ← invoice number
      .dt                     ← invoice date
      .val                    ← invoice total
      .itcavl                 ← "Y" or "N"
      .items[].cgst/sgst/igst ← tax amounts

Usage:
  from gstr2b_reader import GSTR2BReader
  reader = GSTR2BReader.from_file("gstr2b_oct2024.json")
  result = reader.lookup_invoice("24AABSM1111A1Z8", "SM/2024/1102")
  summary = reader.get_itc_summary()
"""

import json
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GSTR2BInvoice:
    """One invoice as it appears in GSTR-2B."""
    invoice_number: str
    invoice_date: str
    invoice_value: float
    supplier_gstin: str
    supplier_name: str
    place_of_supply: str
    itc_available: bool        # True if "Y", False if "N"
    reverse_charge: bool
    igst: float
    cgst: float
    sgst: float
    cess: float

    @property
    def total_itc(self) -> float:
        return round(self.igst + self.cgst + self.sgst + self.cess, 2)


@dataclass
class SupplierFilingInfo:
    """Filing status of one supplier for this period."""
    gstin: str
    name: str
    filing_status: str        # "Filed", "Not Filed", "Pending"
    filing_date: Optional[str]
    invoice_count: int
    total_itc: float


@dataclass
class ITCSummary:
    """Overall ITC available from GSTR-2B for the period."""
    period: str
    generated_date: str
    total_igst: float
    total_cgst: float
    total_sgst: float
    total_cess: float
    invoice_count: int
    supplier_count: int

    @property
    def total_itc(self) -> float:
        return round(self.total_igst + self.total_cgst + self.total_sgst + self.total_cess, 2)


# ---------------------------------------------------------------------------
# Reader class
# ---------------------------------------------------------------------------

class GSTR2BReader:
    """
    Loads and indexes a GSTR-2B JSON document.

    Two instantiation modes:
      1. from_file(path)         → local JSON file (testcase / batch mode)
      2. from_api_response(data) → dict already fetched from GSP API (production)
    """

    def __init__(self, raw_data: dict):
        self._raw = raw_data
        self._data = raw_data.get('data', raw_data)  # handle both wrapped and unwrapped

        # Build indexes on init — fast lookups later
        self._invoice_index: dict[tuple[str, str], GSTR2BInvoice] = {}
        self._supplier_index: dict[str, SupplierFilingInfo] = {}
        self._build_indexes()

    # -----------------------------------------------------------------------
    # Factory methods
    # -----------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> 'GSTR2BReader':
        """Load from a local JSON file (testcase mode or cached API response)."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(data)

    @classmethod
    def from_api_response(cls, response_dict: dict) -> 'GSTR2BReader':
        """Load from a live GSP API response dict (production mode)."""
        return cls(response_dict)

    # -----------------------------------------------------------------------
    # Public query methods
    # -----------------------------------------------------------------------

    def lookup_invoice(self, supplier_gstin: str, invoice_number: str) -> Optional[GSTR2BInvoice]:
        """
        Check if a specific invoice exists in GSTR-2B.

        Returns the invoice data if found, None if not found.
        None means: supplier did not file this invoice → ITC at risk.

        Args:
            supplier_gstin: GSTIN of the supplier (from Tally purchase entry)
            invoice_number:  Invoice number as in Tally (must match supplier's GSTR-1 exactly)
        """
        key = (supplier_gstin.upper().strip(), invoice_number.strip())
        return self._invoice_index.get(key)

    def get_supplier_status(self, supplier_gstin: str) -> Optional[SupplierFilingInfo]:
        """Get filing status of a supplier for this period."""
        return self._supplier_index.get(supplier_gstin.upper().strip())

    def get_all_invoices(self) -> list[GSTR2BInvoice]:
        """Return flat list of all invoices in GSTR-2B."""
        return list(self._invoice_index.values())

    def get_itc_summary(self) -> ITCSummary:
        """Compute total ITC available from all invoices in GSTR-2B."""
        all_inv = self.get_all_invoices()
        total_igst = sum(i.igst for i in all_inv if i.itc_available)
        total_cgst = sum(i.cgst for i in all_inv if i.itc_available)
        total_sgst = sum(i.sgst for i in all_inv if i.itc_available)
        total_cess = sum(i.cess for i in all_inv if i.itc_available)

        return ITCSummary(
            period=self._data.get('rtnprd', ''),
            generated_date=self._data.get('gendt', ''),
            total_igst=round(total_igst, 2),
            total_cgst=round(total_cgst, 2),
            total_sgst=round(total_sgst, 2),
            total_cess=round(total_cess, 2),
            invoice_count=len(all_inv),
            supplier_count=len(self._supplier_index)
        )

    def get_gstin_verification(self, gstin: str) -> Optional[dict]:
        """
        Get mock GSTIN verification result from the test data.
        In production, this comes from a separate GSTN Public API call.
        """
        verif = self._data.get('gstin_verification', {})
        return verif.get(gstin.upper().strip())

    def get_filing_status(self, gstin: str) -> Optional[dict]:
        """Get own filing status check for the taxpayer."""
        status = self._data.get('filing_status_check', {})
        return status.get(gstin.upper().strip())

    # -----------------------------------------------------------------------
    # Index builder — called once on init
    # -----------------------------------------------------------------------

    def _build_indexes(self):
        """
        Parse the docdata.b2b[] array and build both indexes.

        GSTR-2B b2b structure:
          [{
            "ctin": "24AABSM1111A1Z8",
            "suppName": "Silk Mills Ltd",
            "suppFilingStatus": "Filed",
            "suppFilingDate": "10-11-2024",
            "inv": [{
              "inum": "SM/2024/1102",
              "dt": "03-10-2024",
              "val": 201600.00,
              "pos": "24",
              "itcavl": "Y",
              "items": [{"rt": 12, "txval": 180000, "cgst": 10800, "sgst": 10800, ...}]
            }]
          }]
        """
        docdata = self._data.get('docdata', {})
        b2b_list = docdata.get('b2b', [])

        for supplier_block in b2b_list:
            # Skip comment-only blocks (our test data has these)
            if '_comment' in supplier_block and 'ctin' not in supplier_block:
                continue

            supplier_gstin = supplier_block.get('ctin', '').upper().strip()
            if not supplier_gstin:
                continue

            supplier_name = supplier_block.get('suppName', '')
            filing_status = supplier_block.get('suppFilingStatus', 'Unknown')
            filing_date = supplier_block.get('suppFilingDate')
            invoices_raw = supplier_block.get('inv', [])

            supplier_invoices: list[GSTR2BInvoice] = []

            for inv in invoices_raw:
                invoice_number = inv.get('inum', '').strip()
                if not invoice_number:
                    continue

                # Aggregate tax from items[] array
                items = inv.get('items', [])
                total_igst = sum(item.get('igst', 0) or 0 for item in items)
                total_cgst = sum(item.get('cgst', 0) or 0 for item in items)
                total_sgst = sum(item.get('sgst', 0) or 0 for item in items)
                total_cess = sum(item.get('cess', 0) or 0 for item in items)

                invoice_obj = GSTR2BInvoice(
                    invoice_number=invoice_number,
                    invoice_date=inv.get('dt', ''),
                    invoice_value=float(inv.get('val', 0)),
                    supplier_gstin=supplier_gstin,
                    supplier_name=supplier_name,
                    place_of_supply=inv.get('pos', ''),
                    itc_available=(inv.get('itcavl', 'N').upper() == 'Y'),
                    reverse_charge=(inv.get('rev', 'N').upper() == 'Y'),
                    igst=round(float(total_igst), 2),
                    cgst=round(float(total_cgst), 2),
                    sgst=round(float(total_sgst), 2),
                    cess=round(float(total_cess), 2)
                )

                # Index by (supplier_gstin, invoice_number) for O(1) lookup
                key = (supplier_gstin, invoice_number)
                self._invoice_index[key] = invoice_obj
                supplier_invoices.append(invoice_obj)

            # Build supplier index
            total_supplier_itc = sum(i.total_itc for i in supplier_invoices if i.itc_available)
            self._supplier_index[supplier_gstin] = SupplierFilingInfo(
                gstin=supplier_gstin,
                name=supplier_name,
                filing_status=filing_status,
                filing_date=filing_date,
                invoice_count=len(supplier_invoices),
                total_itc=round(total_supplier_itc, 2)
            )

    def __repr__(self) -> str:
        s = self.get_itc_summary()
        return (f"GSTR2BReader(period={s.period}, "
                f"invoices={s.invoice_count}, "
                f"suppliers={s.supplier_count}, "
                f"total_itc=₹{s.total_itc:,.2f})")
