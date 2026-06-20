"""
reconciler.py
-------------
Pure deterministic reconciliation logic. NO AI here.

This module takes parsed Tally data + GSTR-2B data and produces structured
findings: matched invoices, mismatches, GSTIN issues, HSN flags, tax calcs.

The AI (Claude) only gets called AFTER this module runs — it receives the
structured output and produces human-readable summaries and drafts.

Design principle:
  - All business logic is deterministic Python (no LLM calls)
  - Arithmetic is done in Python, not by AI (AI hallucinates numbers)
  - Output is a typed ReconciliationResult that feeds into Claude prompt
"""

from dataclasses import dataclass, field
from typing import Optional
from tally_parser import SalesVoucher, PurchaseVoucher
from gstr2b_reader import GSTR2BReader, GSTR2BInvoice


# ---------------------------------------------------------------------------
# Known HSN → item type mappings for validation
# (subset relevant to textile business — extend per client)
# ---------------------------------------------------------------------------

# Map: HSN prefix → expected item category keywords
HSN_CATEGORY_HINTS = {
    # Textiles
    "5007": ["silk"],
    "5201": ["cotton", "raw cotton", "bale"],
    "5208": ["cotton", "woven", "plain weave", "poplin", "cambric", "shirting", "printed shirting"],
    "5309": ["linen", "flax", "vegetable"],
    "5407": ["synthetic", "polyester", "nylon", "filament", "manmade", "suiting", "polyblend"],
    "5512": ["synthetic staple", "blended", "poly blend"],
    "3204": ["dye", "reactive", "pigment", "colour"],
    # Pharma
    "3004": ["tablet", "capsule", "injection", "syrup", "suspension", "drops",
             "amoxicillin", "paracetamol", "cetirizine", "metformin", "azithromycin",
             "atorvastatin", "omeprazole", "dolo", "medicine", "pharmaceutical"],
    "3005": ["bandage", "dressing", "plaster", "gauze", "wound care", "adhesive tape"],
    "4015": ["glove", "surgical glove", "examination glove", "latex glove", "rubber glove"],
    # Electronics
    "8471": ["computer", "laptop", "desktop", "notebook", "server", "pc", "workstation"],
    "8473": ["keyboard", "mouse", "hard disk", "ssd", "ram", "memory module",
             "processor", "cpu", "component", "part for computer", "hub", "usb hub"],
    "8517": ["phone", "mobile", "smartphone", "handset", "router", "modem",
             "switch", "access point", "wifi", "network device", "telecom"],
    "8542": ["semiconductor", "integrated circuit", "chip", "microchip", "ic"],
    # Food / Spices
    "0904": ["chilli", "chili", "pepper", "paprika", "red chilli", "green chilli"],
    "0910": ["turmeric", "ginger", "cumin", "coriander", "cardamom", "haldi",
             "jeera", "bay leaf", "spice mix", "masala powder"],
    "2103": ["sauce", "ketchup", "condiment", "chutney", "pickle", "mixed masala",
             "curry paste", "relish"],
    "2104": ["soup", "broth", "stock", "instant soup"],
    # Auto Parts
    "8708": ["brake pad", "clutch plate", "gear box", "axle", "bumper", "steering",
             "shock absorber", "car part", "automobile", "vehicle body", "exhaust"],
    "8714": ["motorcycle", "bike", "two wheeler", "scooter", "tyre", "cycle",
             "bicycle", "moped", "disc brake cable", "motorbike"],
}


def _hsn_matches_item(hsn: str, item_name: str) -> bool:
    """
    Heuristic: does this HSN code make sense for this item name?
    Returns False if there's a likely mismatch worth flagging.
    """
    hints = HSN_CATEGORY_HINTS.get(hsn[:4], None)
    if hints is None:
        return True  # unknown HSN, don't flag
    name_lower = item_name.lower()
    return any(keyword in name_lower for keyword in hints)


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class GSTINValidationResult:
    gstin: str
    name: str
    invoice_number: str
    status: str          # "Active", "Cancelled", "Suspended", "Unknown"
    issue: Optional[str] = None
    action: Optional[str] = None


@dataclass
class ITCReconciliationResult:
    """Result of matching one purchase invoice against GSTR-2B."""
    purchase_voucher: PurchaseVoucher
    gstr2b_invoice: Optional[GSTR2BInvoice]
    status: str          # "MATCHED", "MISSING_FROM_GSTR2B", "AMOUNT_MISMATCH"
    itc_claimed: float   # what agent recommends claiming
    itc_at_risk: float   # what is excluded due to mismatch
    risk_reason: Optional[str] = None


@dataclass
class HSNFlagResult:
    invoice_number: str
    item_name: str
    hsn_in_tally: str
    suggested_hsn: Optional[str]
    description: str


@dataclass
class TaxCalculation:
    """Final tax liability calculation."""
    # Output (from sales)
    output_igst: float
    output_cgst: float
    output_sgst: float

    # ITC (from purchases, confirmed in GSTR-2B)
    itc_igst: float
    itc_cgst: float
    itc_sgst: float

    # At risk (in Tally but not in GSTR-2B)
    at_risk_igst: float
    at_risk_cgst: float
    at_risk_sgst: float

    @property
    def net_igst(self) -> float:
        return round(max(0, self.output_igst - self.itc_igst), 2)

    @property
    def net_cgst(self) -> float:
        return round(max(0, self.output_cgst - self.itc_cgst), 2)

    @property
    def net_sgst(self) -> float:
        return round(max(0, self.output_sgst - self.itc_sgst), 2)

    @property
    def net_payable(self) -> float:
        return round(self.net_igst + self.net_cgst + self.net_sgst, 2)

    @property
    def total_at_risk(self) -> float:
        return round(self.at_risk_igst + self.at_risk_cgst + self.at_risk_sgst, 2)


@dataclass
class ReconciliationResult:
    """
    Complete output of the reconciliation engine.
    This is what gets passed to Claude for narrative generation.
    """
    gstin: str
    period: str

    # Sales
    sales_vouchers: list[SalesVoucher]
    total_sales_value: float
    total_output_gst: float

    # Purchase / ITC
    purchase_vouchers: list[PurchaseVoucher]
    itc_results: list[ITCReconciliationResult]
    total_purchase_value: float
    confirmed_itc: float
    at_risk_itc: float

    # Issues
    gstin_issues: list[GSTINValidationResult]
    hsn_flags: list[HSNFlagResult]

    # Final tax
    tax_calc: TaxCalculation

    # Status
    has_critical_issues: bool
    issue_count: int
    status: str   # "CLEAN", "ISSUES_FOUND", "CRITICAL"


# ---------------------------------------------------------------------------
# Main reconciler
# ---------------------------------------------------------------------------

class Reconciler:
    """
    Runs the full deterministic reconciliation pipeline.

    Usage:
        from reconciler import Reconciler
        result = Reconciler(
            sales=parser.parse_sales_vouchers(),
            purchases=parser.parse_purchase_vouchers(),
            gstr2b=reader,
            gstin="24AABMT1234C1Z5",
            period="102024"
        ).run()
    """

    def __init__(
        self,
        sales: list[SalesVoucher],
        purchases: list[PurchaseVoucher],
        gstr2b: GSTR2BReader,
        gstin: str,
        period: str
    ):
        self.sales = sales
        self.purchases = purchases
        self.gstr2b = gstr2b
        self.gstin = gstin
        self.period = period

    def run(self) -> ReconciliationResult:
        """Execute all reconciliation checks and return structured result."""
        print(f"\n{'='*60}")
        print(f"  RECONCILIATION: {self.gstin} | Period: {self.period}")
        print(f"{'='*60}")

        # Step 1: Validate customer GSTINs on sales invoices
        print("\n[1/4] Validating customer GSTINs...")
        gstin_issues = self._validate_gstins()

        # Step 2: Reconcile purchases against GSTR-2B
        print("[2/4] Reconciling purchases vs GSTR-2B...")
        itc_results = self._reconcile_itc()

        # Step 3: Check HSN codes
        print("[3/4] Checking HSN codes...")
        hsn_flags = self._check_hsn_codes()

        # Step 4: Calculate tax liability
        print("[4/4] Calculating tax liability...")
        tax_calc = self._calculate_tax(itc_results)

        # Aggregate totals
        total_sales = sum(v.taxable_value for v in self.sales)
        total_output_gst = sum(v.total_gst for v in self.sales)
        total_purchases = sum(v.taxable_value for v in self.purchases)
        confirmed_itc = sum(r.itc_claimed for r in itc_results)
        at_risk_itc = sum(r.itc_at_risk for r in itc_results)

        issue_count = len(gstin_issues) + len(hsn_flags) + sum(
            1 for r in itc_results if r.status != 'MATCHED'
        )

        has_critical = any(
            g.status in ('Cancelled', 'Suspended') for g in gstin_issues
        )

        status = 'CLEAN'
        if issue_count > 0:
            status = 'CRITICAL' if has_critical else 'ISSUES_FOUND'

        result = ReconciliationResult(
            gstin=self.gstin,
            period=self.period,
            sales_vouchers=self.sales,
            total_sales_value=round(total_sales, 2),
            total_output_gst=round(total_output_gst, 2),
            purchase_vouchers=self.purchases,
            itc_results=itc_results,
            total_purchase_value=round(total_purchases, 2),
            confirmed_itc=round(confirmed_itc, 2),
            at_risk_itc=round(at_risk_itc, 2),
            gstin_issues=gstin_issues,
            hsn_flags=hsn_flags,
            tax_calc=tax_calc,
            has_critical_issues=has_critical,
            issue_count=issue_count,
            status=status
        )

        self._print_summary(result)
        return result

    # -----------------------------------------------------------------------
    # Step 1: GSTIN Validation
    # -----------------------------------------------------------------------

    def _validate_gstins(self) -> list[GSTINValidationResult]:
        """
        Check each customer GSTIN on sales invoices against verification data.
        In testcase mode, validation data is embedded in gstr2b JSON.
        In production, this calls GSTN Public API per GSTIN.
        """
        issues = []
        seen = set()

        for voucher in self.sales:
            gstin = voucher.buyer_gstin
            if not gstin or gstin in seen:
                continue
            seen.add(gstin)

            # Look up verification result (mock data in testcase)
            verif = self.gstr2b.get_gstin_verification(gstin)

            if verif is None:
                # Not in our mock data — in production would call API
                continue

            status = verif.get('status', 'Unknown')

            if status != 'Active':
                action = None
                if status == 'Cancelled':
                    action = f"Reclassify invoice(s) with this GSTIN from B2B to B2CS in GSTR-1"

                issues.append(GSTINValidationResult(
                    gstin=gstin,
                    name=verif.get('tradeName', voucher.buyer_name),
                    invoice_number=voucher.voucher_number,
                    status=status,
                    issue=f"GSTIN {gstin} is {status} (cancelled: {verif.get('cancellationDate', 'N/A')})",
                    action=action
                ))
                print(f"  ⚠️  {voucher.voucher_number}: {voucher.buyer_name} GSTIN is {status}")
            else:
                print(f"  ✅ {voucher.voucher_number}: {voucher.buyer_name} ({gstin}) — Active")

        return issues

    # -----------------------------------------------------------------------
    # Step 2: ITC Reconciliation
    # -----------------------------------------------------------------------

    def _reconcile_itc(self) -> list[ITCReconciliationResult]:
        """
        Match each purchase voucher against GSTR-2B.
        Uses invoice_index for O(1) lookup per invoice.
        """
        results = []

        for purchase in self.purchases:
            gstr2b_invoice = self.gstr2b.lookup_invoice(
                purchase.supplier_gstin,
                purchase.voucher_number
            )

            if gstr2b_invoice is None:
                # Invoice in Tally but NOT in GSTR-2B → ITC at risk
                itc_risk = purchase.itc_claimable
                results.append(ITCReconciliationResult(
                    purchase_voucher=purchase,
                    gstr2b_invoice=None,
                    status='MISSING_FROM_GSTR2B',
                    itc_claimed=0.0,
                    itc_at_risk=itc_risk,
                    risk_reason=f"Invoice {purchase.voucher_number} from {purchase.supplier_name} "
                                f"({purchase.supplier_gstin}) not found in GSTR-2B. "
                                f"Supplier may not have filed GSTR-1."
                ))
                print(f"  🚨 {purchase.voucher_number} ({purchase.supplier_name}): "
                      f"MISSING from GSTR-2B — ITC ₹{itc_risk:,.2f} at risk")

            else:
                # Invoice found — check for amount mismatch
                tally_gst = purchase.itc_claimable
                gstr2b_gst = gstr2b_invoice.total_itc
                diff = abs(tally_gst - gstr2b_gst)

                if diff > 1.0:  # tolerance ₹1 for rounding
                    results.append(ITCReconciliationResult(
                        purchase_voucher=purchase,
                        gstr2b_invoice=gstr2b_invoice,
                        status='AMOUNT_MISMATCH',
                        itc_claimed=gstr2b_gst,  # claim lower amount (safer)
                        itc_at_risk=diff,
                        risk_reason=f"Amount mismatch: Tally ₹{tally_gst:,.2f} vs GSTR-2B ₹{gstr2b_gst:,.2f}"
                    ))
                    print(f"  ⚠️  {purchase.voucher_number}: Amount mismatch ₹{diff:,.2f}")
                else:
                    results.append(ITCReconciliationResult(
                        purchase_voucher=purchase,
                        gstr2b_invoice=gstr2b_invoice,
                        status='MATCHED',
                        itc_claimed=gstr2b_gst,
                        itc_at_risk=0.0
                    ))
                    print(f"  ✅ {purchase.voucher_number} ({purchase.supplier_name}): "
                          f"MATCHED — ITC ₹{gstr2b_gst:,.2f}")

        return results

    # -----------------------------------------------------------------------
    # Step 3: HSN Validation
    # -----------------------------------------------------------------------

    def _check_hsn_codes(self) -> list[HSNFlagResult]:
        """Flag likely HSN mismatches based on item name vs HSN code."""
        flags = []

        for voucher in self.sales:
            for item in voucher.items:
                if not item.hsn_code:
                    continue
                if not _hsn_matches_item(item.hsn_code, item.name):
                    suggested = self._suggest_hsn(item.name)
                    if suggested is None:
                        continue  # no concrete suggestion — not actionable
                    flags.append(HSNFlagResult(
                        invoice_number=voucher.voucher_number,
                        item_name=item.name,
                        hsn_in_tally=item.hsn_code,
                        suggested_hsn=suggested,
                        description=(
                            f"Item '{item.name}' may not match HSN {item.hsn_code}. "
                            f"{'Suggested: ' + suggested if suggested else 'Please verify.'}"
                        )
                    ))
                    print(f"  ⚠️  {voucher.voucher_number}: HSN {item.hsn_code} "
                          f"may be wrong for '{item.name}'")

        if not flags:
            print("  ✅ No HSN mismatches detected")

        return flags

    @staticmethod
    def _suggest_hsn(item_name: str) -> Optional[str]:
        """Simple reverse lookup: item name keywords → suggested HSN."""
        name_lower = item_name.lower()
        # Textiles
        if 'cotton' in name_lower and any(w in name_lower for w in ('plain', 'weave', 'poplin', 'shirting', 'printed')):
            return '5208'
        if 'linen' in name_lower or 'flax' in name_lower:
            return '5309'
        if 'silk' in name_lower:
            return '5007'
        if any(w in name_lower for w in ('polyester', 'synthetic', 'polyblend', 'suiting')):
            return '5407'
        if 'dye' in name_lower or 'reactive' in name_lower:
            return '3204'
        # Pharma
        if any(w in name_lower for w in ('tablet', 'capsule', 'syrup', 'injection', 'medicine')):
            return '3004'
        if any(w in name_lower for w in ('bandage', 'dressing', 'plaster', 'gauze')):
            return '3005'
        if any(w in name_lower for w in ('glove', 'surgical glove', 'latex')):
            return '4015'
        # Electronics
        if any(w in name_lower for w in ('laptop', 'computer', 'desktop', 'notebook')):
            return '8471'
        if any(w in name_lower for w in ('router', 'wifi', 'smartphone', 'mobile', 'phone')):
            return '8517'
        # Food / Spices
        if any(w in name_lower for w in ('chilli', 'chili', 'pepper', 'paprika')):
            return '0904'
        if any(w in name_lower for w in ('turmeric', 'haldi', 'cumin', 'coriander', 'ginger')):
            return '0910'
        # Auto Parts
        if any(w in name_lower for w in ('motorcycle', 'bike', 'two wheeler', 'disc brake cable')):
            return '8714'
        if any(w in name_lower for w in ('brake pad', 'clutch', 'gear box', 'car part')):
            return '8708'
        return None

    # -----------------------------------------------------------------------
    # Step 4: Tax Calculation
    # -----------------------------------------------------------------------

    def _calculate_tax(self, itc_results: list[ITCReconciliationResult]) -> TaxCalculation:
        """
        Compute output tax, confirmed ITC, and net payable.
        Pure arithmetic — no AI involved.
        """
        # Output tax from sales
        out_igst = sum(v.igst for v in self.sales)
        out_cgst = sum(v.cgst for v in self.sales)
        out_sgst = sum(v.sgst for v in self.sales)

        # Confirmed ITC (from GSTR-2B matched invoices)
        itc_igst = sum(
            r.gstr2b_invoice.igst
            for r in itc_results
            if r.status == 'MATCHED' and r.gstr2b_invoice
        )
        itc_cgst = sum(
            r.gstr2b_invoice.cgst
            for r in itc_results
            if r.status == 'MATCHED' and r.gstr2b_invoice
        )
        itc_sgst = sum(
            r.gstr2b_invoice.sgst
            for r in itc_results
            if r.status == 'MATCHED' and r.gstr2b_invoice
        )

        # At risk ITC
        risk_total = sum(r.itc_at_risk for r in itc_results if r.status == 'MISSING_FROM_GSTR2B')

        return TaxCalculation(
            output_igst=round(out_igst, 2),
            output_cgst=round(out_cgst, 2),
            output_sgst=round(out_sgst, 2),
            itc_igst=round(itc_igst, 2),
            itc_cgst=round(itc_cgst, 2),
            itc_sgst=round(itc_sgst, 2),
            at_risk_igst=0.0,
            at_risk_cgst=round(risk_total / 2, 2),
            at_risk_sgst=round(risk_total / 2, 2),
        )

    # -----------------------------------------------------------------------
    # Summary printer
    # -----------------------------------------------------------------------

    @staticmethod
    def _print_summary(result: ReconciliationResult):
        tc = result.tax_calc
        print(f"\n{'─'*60}")
        print(f"  RECONCILIATION COMPLETE — Status: {result.status}")
        print(f"{'─'*60}")
        print(f"  Sales: {len(result.sales_vouchers)} invoices | ₹{result.total_sales_value:,.2f}")
        print(f"  Output GST: IGST ₹{tc.output_igst:,.2f} | "
              f"CGST ₹{tc.output_cgst:,.2f} | SGST ₹{tc.output_sgst:,.2f}")
        print(f"  ITC Confirmed: IGST ₹{tc.itc_igst:,.2f} | "
              f"CGST ₹{tc.itc_cgst:,.2f} | SGST ₹{tc.itc_sgst:,.2f}")
        print(f"  ITC At Risk:  ₹{result.at_risk_itc:,.2f}")
        print(f"  Net Payable (conservative): ₹{tc.net_payable:,.2f}")
        print(f"  Issues: {result.issue_count} "
              f"({'CRITICAL' if result.has_critical_issues else 'non-critical'})")
        print(f"{'─'*60}\n")
