"""
mock_tally.py
-------------
Per-client mock Tally vouchers and GSTR-2B data for dry_run mode.
Returns SalesVoucher / PurchaseVoucher objects directly — no XML parsing.

Each client gets industry-realistic data with deterministic seeded bulk entries
plus specific "compliance issue" vouchers that trigger reconciliation flags.

Scenario mapping (based on GSTIN character sum % 4):
  0 = CRITICAL  — cancelled GSTIN sale + missing ITC + HSN mismatch
  1 = WARNING   — missing ITC + HSN mismatch (all GSTINs active)
  2 = WARNING   — cancelled GSTIN sale + HSN mismatch (all ITC matched)
  3 = CLEAN     — minor HSN flag only
"""

import random
from datetime import datetime, timedelta
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tally_parser import SalesVoucher, PurchaseVoucher, InventoryItem


# ─── Client specifications ────────────────────────────────────────────────────
#
# Each entry has everything needed to build realistic mock data:
#   industry        — sector label for readability
#   state           — 2-digit state code (matches GSTIN prefix)
#   gst_rate        — primary GST rate for this business
#   hsn_correct     — HSN that items SHOULD use
#   hsn_wrong       — HSN recorded in Tally (triggers flag)
#   item_name       — item description (triggers HSN flag because name mismatches hsn_wrong)
#   sale_range      — (min, max) taxable value per sales invoice
#   purchase_range  — (min, max) taxable value per purchase invoice
#   n_sales         — number of bulk sales vouchers to generate
#   n_purchases     — number of bulk purchase vouchers to generate
#   suppliers       — list of (gstin, name) for regular purchase suppliers
#   buyers          — list of (gstin, name) for regular sales customers
#   cancelled_buyer — (gstin, name) — buyer with cancelled GSTIN (scenarios 0, 2)
#   missing_supplier— (gstin, name, inv_num, taxable, gst) — not in GSTR-2B (scenarios 0, 1)

CLIENT_SPECS = {

    # ── Patel Cotton Mills, Maharashtra, Textiles ──────────────────────────
    '27AABCP1234D1Z3': dict(
        industry='textiles', state='27', gst_rate=12,
        hsn_correct='5208', hsn_wrong='5407',
        item_name='Cotton Printed Shirting Fabric',
        sale_range=(120_000, 450_000), purchase_range=(80_000, 280_000),
        n_sales=48, n_purchases=32,
        suppliers=[
            ('27AABCNG001A1Z5', 'Nagpur Cotton Ginners Ltd'),
            ('27AABCBY002B1Z3', 'Bhiwandi Yarn Wholesale'),
            ('27AABCSW003C1Z1', 'Solapur Weaving Mills'),
            ('24AABCDK004D1Z9', 'Dhollera Knitting Fabrics'),
            ('24AABCST005E1Z7', 'Surat Textile Finishers'),
        ],
        buyers=[
            ('27AABCMW006F1Z5', 'Mumbai Garment Exports'),
            ('27AABCPG007G1Z3', 'Pune Garment Manufacturers'),
            ('27AABCNB008H1Z1', 'Nashik Boutique Chains'),
            ('24AABCAS009J1Z9', 'Ahmedabad Suiting Store'),
            ('24AABCVT010K1Z7', 'Vadodara Textile Retailers'),
            ('07AABCDT011L1Z5', 'Delhi Textile Wholesale'),
        ],
        cancelled_buyer=('27AAFVR8888X1Z1', 'Rajan Textiles (P) Ltd'),
        missing_supplier=('27AABCWC012M1Z3', 'Wardha Cotton Co', 'WC/2024/0318',
                          95_000, 11_400),
    ),

    # ── Shah Synthetics Ltd, Maharashtra, Textiles ─────────────────────────
    '27AABCS5678E1Z1': dict(
        industry='textiles', state='27', gst_rate=12,
        hsn_correct='5407', hsn_wrong='5208',
        item_name='Polyblend Suiting Fabric',
        sale_range=(80_000, 320_000), purchase_range=(60_000, 200_000),
        n_sales=35, n_purchases=22,
        suppliers=[
            ('27AABCPF013N1Z1', 'Pimpri Filament Yarns'),
            ('27AABCNM014P1Z9', 'Navi Mumbai Synthetic Mills'),
            ('27AABCTH015Q1Z7', 'Thane Hi-tech Fabrics'),
            ('24AABCSR016R1Z5', 'Surat Ribbon & Lace'),
        ],
        buyers=[
            ('27AABCPB017S1Z3', 'Pune Boutique Network'),
            ('27AABCMS018T1Z1', 'Mumbai Saree Showrooms'),
            ('09AABCLK019U1Z9', 'Lucknow Kurta House'),
            ('08AABCJP020V1Z7', 'Jaipur Print & Dye'),
        ],
        cancelled_buyer=('27AAFVM7777Z1Z4', 'Manish Fabrics & Garments'),
        missing_supplier=None,  # scenario 2 — no missing ITC
    ),

    # ── Cadila Pharma Distributors, Gujarat, Pharma ────────────────────────
    '24AABCP9012F1Z5': dict(
        industry='pharma', state='24', gst_rate=12,
        hsn_correct='3004', hsn_wrong='3005',
        item_name='Amoxicillin 500mg Capsules',
        sale_range=(25_000, 180_000), purchase_range=(18_000, 130_000),
        n_sales=82, n_purchases=54,
        suppliers=[
            ('24AABCCA021W1Z5', 'Cipla API Distributors'),
            ('24AABCSA022X1Z3', 'Sun Pharma Wholesale Gujarat'),
            ('24AABCLA023Y1Z1', 'Lupin Pharma Depot'),
            ('24AABCZA024Z1Z9', 'Zydus Lifesciences Dist'),
            ('24AABCAA025A2Z7', 'Alembic Pharma Traders'),
            ('27AABCPA026B2Z5', 'Piramal Enterprises Mumbai'),
        ],
        buyers=[
            ('24AABCDR027C2Z3', 'Dr. Mehta Hospital Pharmacy'),
            ('24AABCCH028D2Z1', 'City Hospital Drugs'),
            ('24AABCGM029E2Z9', 'Gandhi Medical Stores'),
            ('24AABCSH030F2Z7', 'Saurashtra Health Clinic'),
            ('27AABCMH031G2Z5', 'Mumbai Hospital Pharmacy'),
            ('29AABCBM032H2Z3', 'Bengaluru MedPlus Dist'),
        ],
        cancelled_buyer=None,    # scenario 3 — CLEAN, no cancelled GSTIN
        missing_supplier=None,   # scenario 3 — no missing ITC
    ),

    # ── Apollo Medical Supplies, Gujarat, Pharma ───────────────────────────
    '24AABCA3456G1Z2': dict(
        industry='pharma', state='24', gst_rate=12,
        hsn_correct='4015', hsn_wrong='3004',
        item_name='Surgical Latex Examination Gloves',
        sale_range=(30_000, 150_000), purchase_range=(20_000, 100_000),
        n_sales=28, n_purchases=18,
        suppliers=[
            ('24AABCMD033J2Z1', 'Meditex Disposables Ahmedabad'),
            ('24AABCHS034K2Z9', 'HLL Medical Supplies'),
            ('24AABCCS035L2Z7', 'Care Surgical Products'),
        ],
        buyers=[
            ('24AABCAM036M2Z5', 'Apollo Hospitals Gujarat'),
            ('24AABCFS037N2Z3', 'Fortis Clinic Pharmacy'),
            ('24AABCKH038P2Z1', 'Kiran Hospital Ahmedabad'),
            ('24AABCNG039Q2Z9', 'Narayana Health Stores'),
        ],
        cancelled_buyer=('24AAFVD5555W1Z2', 'Dr. Modi Medical Centre'),
        missing_supplier=('24AABCCD040R2Z7', 'Cipla Disposables Dist',
                          'CD/2024/0421', 68_000, 8_160),
    ),

    # ── Bengaluru Electronics Hub, Karnataka, Electronics ──────────────────
    '29AABCE7890H1Z4': dict(
        industry='electronics', state='29', gst_rate=18,
        hsn_correct='8471', hsn_wrong='8473',
        item_name='Laptop Computer Intel Core i5 16GB',
        sale_range=(85_000, 420_000), purchase_range=(60_000, 300_000),
        n_sales=64, n_purchases=40,
        suppliers=[
            ('29AABCHA041S2Z5', 'HP India Authorized Dist Karnataka'),
            ('29AABCDL042T2Z3', 'Dell EMC Karnataka Depot'),
            ('29AABCLE043U2Z1', 'Lenovo India Electronics'),
            ('07AABCAS044V2Z9', 'ASUS India New Delhi'),
            ('29AABCAC045W2Z7', 'Acer Computers Bangalore'),
        ],
        buyers=[
            ('29AABCBT046X2Z5', 'BigTech Retail Chains'),
            ('29AABCCS047Y2Z3', 'Croma Stores Karnataka'),
            ('29AABCVB048Z2Z1', 'Vijay Sales Bengaluru'),
            ('32AABCKR049A3Z9', 'Kerala IT Solutions'),
            ('36AABCHP050B3Z7', 'Hyderabad PC World'),
        ],
        cancelled_buyer=('29AAFVM8888U1Z6', 'Manoj Electronics Traders'),
        missing_supplier=None,  # scenario 2 — no missing ITC
    ),

    # ── Infotech Components Pvt, Karnataka, Electronics ────────────────────
    '29AABCI1234J1Z6': dict(
        industry='electronics', state='29', gst_rate=18,
        hsn_correct='8517', hsn_wrong='8473',
        item_name='WiFi Router SOHO 300Mbps Dual Band',
        sale_range=(40_000, 200_000), purchase_range=(30_000, 150_000),
        n_sales=38, n_purchases=25,
        suppliers=[
            ('36AABCHD051C3Z5', 'Hyderabad Component Dist'),
            ('29AABCEC052D3Z3', 'Electronic Chips Bangalore'),
            ('29AABCIT053E3Z1', 'IT Components Pvt Ltd'),
            ('27AABCMC054F3Z9', 'Mumbai Component Hub'),
        ],
        buyers=[
            ('29AABCIS055G3Z7', 'Infotech Solutions Mgmt'),
            ('29AABCNT056H3Z5', 'Network Tech Resellers'),
            ('36AABCHS057J3Z3', 'Hyderabad Server Store'),
            ('32AABCTV058K3Z1', 'Trivandrum Tech World'),
        ],
        cancelled_buyer=('29AAFVK9999T1Z3', 'Krishna Tech Solutions'),
        missing_supplier=('36AABCHD059L3Z9', 'Hyderabad Chip Dist Pvt',
                          'HCD/2024/0112', 120_000, 21_600),
    ),

    # ── Delhi Spice Traders, Delhi, Food & FMCG ───────────────────────────
    '06AABCF5678K1Z3': dict(
        industry='food', state='06', gst_rate=5,
        hsn_correct='0904', hsn_wrong='2103',
        item_name='Kashmiri Red Chilli Powder Premium',
        sale_range=(15_000, 80_000), purchase_range=(10_000, 60_000),
        n_sales=112, n_purchases=68,
        suppliers=[
            ('24AABCGU060M3Z7', 'Gujarat Unjha Agro Spices'),
            ('08AABCJM061N3Z5', 'Jodhpur Masala Mills'),
            ('08AABCRK062P3Z3', 'Rajasthan Krishi Traders'),
            ('24AABCSP063Q3Z1', 'Spices Park India Ltd'),
            ('06AABCND064R3Z9', 'New Delhi Agro Depot'),
        ],
        buyers=[
            ('06AABCBM065S3Z7', 'Big Mart Retail Delhi'),
            ('06AABCSP066T3Z5', 'Super Provisions Karol Bagh'),
            ('06AABCRI067U3Z3', 'Reliance Mart India Delhi'),
            ('09AABCUP068V3Z1', 'UP Kirana Wholesale'),
            ('06AABCKR069W3Z9', 'Khari Baoli Wholesale'),
            ('03AABCWB070X3Z7', 'West Bengal Spice Depot'),
        ],
        cancelled_buyer=('06AAFVS1111R1Z7', 'Shah Provisions & Stores'),
        missing_supplier=None,  # scenario 2 — no missing ITC
    ),

    # ── Aggarwal Foods Pvt Ltd, Delhi, Food & FMCG ────────────────────────
    '06AABCA9012L1Z1': dict(
        industry='food', state='06', gst_rate=5,
        hsn_correct='0910', hsn_wrong='2103',
        item_name='Pure Haldi Turmeric Powder 1kg',
        sale_range=(12_000, 70_000), purchase_range=(8_000, 50_000),
        n_sales=52, n_purchases=34,
        suppliers=[
            ('06AABCAM071Y3Z5', 'Agra Mill Foods Pvt'),
            ('08AABCJF072Z3Z3', 'Jaipur Farm Products'),
            ('24AABCGF073A4Z1', 'Gujarat Farmfresh Spices'),
            ('09AABCLK074B4Z9', 'Lucknow Kirana Depot'),
        ],
        buyers=[
            ('06AABCDM075C4Z7', 'D-Mart Delhi Wholesale'),
            ('06AABCNG076D4Z5', 'Nature\'s Goodness Stores'),
            ('07AABCPS077E4Z3', 'Patanjali Store Network'),
            ('06AABCBF078F4Z1', 'Big Food Retail Chain'),
        ],
        cancelled_buyer=('06AAFVK2222Q1Z5', 'Kumar General Stores Delhi'),
        missing_supplier=None,  # scenario 2 — no missing ITC
    ),

    # ── Chennai Auto Ancillaries, Tamil Nadu, Auto Parts ──────────────────
    '33AABCA3456M1Z8': dict(
        industry='auto', state='33', gst_rate=18,
        hsn_correct='8714', hsn_wrong='8708',
        item_name='Motorcycle Disc Brake Cable Assembly',
        sale_range=(50_000, 280_000), purchase_range=(35_000, 200_000),
        n_sales=74, n_purchases=47,
        suppliers=[
            ('33AABCCA079G4Z9', 'Coimbatore Auto Forgings'),
            ('33AABCMP080H4Z7', 'Madurai Parts Mfg Co'),
            ('33AABCTP081J4Z5', 'TVS Parts Distributor'),
            ('27AABCBP082K4Z3', 'Bajaj Parts Mumbai'),
            ('29AABCAS083L4Z1', 'Automobile Spares Bangalore'),
        ],
        buyers=[
            ('33AABCMG084M4Z9', 'Murugan Garage Network'),
            ('33AABCSA085N4Z7', 'Sri Auto Service Chain'),
            ('33AABCKG086P4Z5', 'KG Motors Dealers'),
            ('32AABCKA087Q4Z3', 'Kerala Auto Dealers'),
            ('36AABCAP088R4Z1', 'AP Auto Parts Dist'),
        ],
        cancelled_buyer=('33AAFVR3333P1Z2', 'Raj Motors Pvt Ltd'),
        missing_supplier=('33AABCCA089S4Z9', 'Coimbatore Auto Castings',
                          'CAC/2024/0267', 185_000, 33_300),
    ),
}

# Mehta Textile uses testcase XML directly — not handled here.
MEHTA_GSTIN = '24AABMT1234C1Z5'


# ─── Period helpers ───────────────────────────────────────────────────────────

def _period_to_date(period: str):
    """'032026' → datetime(2026, 3, 1)"""
    mm, yyyy = int(period[:2]), int(period[2:])
    return datetime(yyyy, mm, 1)


def _days_in_period(period: str) -> int:
    import calendar
    mm, yyyy = int(period[:2]), int(period[2:])
    return calendar.monthrange(yyyy, mm)[1]


def _fmt_date(dt: datetime) -> str:
    return dt.strftime('%d-%m-%Y')


# ─── Voucher generators ───────────────────────────────────────────────────────

def _make_sale(
    rng: random.Random,
    period: str,
    voucher_num: str,
    buyer_gstin: str,
    buyer_name: str,
    spec: dict,
    hsn_override: str = None,
    item_name_override: str = None,
) -> SalesVoucher:
    days = _days_in_period(period)
    base = _period_to_date(period)
    day = rng.randint(1, days)
    date = _fmt_date(base + timedelta(days=day - 1))

    rate = spec['gst_rate']
    taxable = round(rng.uniform(*spec['sale_range']), 2)
    half_gst = round(taxable * rate / 200, 2)

    hsn = hsn_override or spec['hsn_correct']
    item_name = item_name_override or _generic_item_name(hsn, rng)

    item = InventoryItem(
        name=item_name,
        hsn_code=hsn,
        gst_rate=float(rate),
        quantity=f"{rng.randint(10, 500)} Nos",
        rate_per_unit=str(round(taxable / rng.randint(10, 500), 2)),
        taxable_value=taxable,
    )

    state = spec['state']
    buyer_state = buyer_gstin[:2]
    if buyer_state == state:
        cgst, sgst, igst = half_gst, half_gst, 0.0
    else:
        cgst, sgst, igst = 0.0, 0.0, round(taxable * rate / 100, 2)

    return SalesVoucher(
        date=date,
        voucher_number=voucher_num,
        guid=f"guid-sale-{voucher_num}",
        buyer_name=buyer_name,
        buyer_gstin=buyer_gstin,
        place_of_supply=buyer_state,
        taxable_value=taxable,
        invoice_total=round(taxable + cgst + sgst + igst, 2),
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        supply_type='B2B',
        items=[item],
    )


def _make_purchase(
    rng: random.Random,
    period: str,
    voucher_num: str,
    supplier_gstin: str,
    supplier_name: str,
    spec: dict,
    taxable: float = None,
    gst_amount: float = None,
) -> PurchaseVoucher:
    days = _days_in_period(period)
    base = _period_to_date(period)
    day = rng.randint(1, days)
    date = _fmt_date(base + timedelta(days=day - 1))

    rate = spec['gst_rate']
    if taxable is None:
        taxable = round(rng.uniform(*spec['purchase_range']), 2)
    half_gst = round(taxable * rate / 200, 2) if gst_amount is None else gst_amount / 2

    state = spec['state']
    supplier_state = supplier_gstin[:2]
    if supplier_state == state:
        cgst, sgst, igst = half_gst, half_gst, 0.0
    else:
        cgst, sgst, igst = 0.0, 0.0, gst_amount or round(taxable * rate / 100, 2)

    item = InventoryItem(
        name=_generic_item_name(spec['hsn_correct'], rng),
        hsn_code=spec['hsn_correct'],
        gst_rate=float(rate),
        quantity=f"{rng.randint(5, 200)} Nos",
        rate_per_unit=str(round(taxable / max(1, rng.randint(5, 200)), 2)),
        taxable_value=taxable,
    )

    return PurchaseVoucher(
        date=date,
        voucher_number=voucher_num,
        guid=f"guid-pur-{voucher_num}",
        supplier_name=supplier_name,
        supplier_gstin=supplier_gstin,
        taxable_value=taxable,
        invoice_total=round(taxable + cgst + sgst + igst, 2),
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        items=[item],
    )


# Per-HSN item names — each list matches that HSN's category keywords so no false flags
HSN_ITEMS = {
    '5208': ['Cotton Shirting Fabric', 'Cotton Cambric Cloth', 'Cotton Poplin 60"',
             'Cotton Plain Weave Bale', 'Cotton Woven Grey Fabric', 'Cotton Printed Cloth'],
    '5407': ['Polyester Suiting Fabric', 'Synthetic Filament Yarn', 'Nylon Woven Fabric',
             'Polyblend Suiting Roll', 'Manmade Fibre Fabric', 'Polyester Grey Cloth'],
    '3004': ['Paracetamol 500mg Tablets', 'Amoxicillin 250mg Capsules',
             'Azithromycin 500mg Tabs', 'Cetirizine 10mg Strip',
             'Metformin 500mg Strip', 'Omeprazole 20mg Caps', 'Dolo 650 Tablets Strip'],
    '4015': ['Latex Examination Gloves Box/100', 'Surgical Gloves Sterile Pair',
             'Nitrile Examination Gloves', 'Latex Disposable Gloves M-Size',
             'Powdered Rubber Gloves Box', 'Surgical Latex Glove Set'],
    '8471': ['Laptop Computer 15.6" Core i5', 'Desktop PC Core i7 16GB',
             'Notebook Business i5 8GB', 'Workstation Intel Xeon',
             'All-in-One Desktop 21"', 'Mini PC Server Core i3'],
    '8517': ['WiFi Router SOHO 300Mbps', 'Network Switch 24-Port Managed',
             'Modem 4G LTE Dual Band', 'Access Point Enterprise AC1200',
             'VoIP Phone Desktop', 'Network Gateway Device'],
    '0904': ['Kashmiri Red Chilli Whole 25kg', 'Black Pepper Powder 10kg',
             'Green Chilli Crushed 5kg', 'Paprika Powder Fine 10kg',
             'White Pepper Whole 5kg', 'Dried Red Chilli Stemless 25kg'],
    '0910': ['Pure Haldi Turmeric Powder 25kg', 'Ground Cumin Jeera Powder 10kg',
             'Coriander Powder Dhaniya 10kg', 'Dry Ginger Saunth Powder 5kg',
             'Cardamom Elaichi Powder 1kg', 'Fenugreek Methi Powder 5kg'],
    '8708': ['Car Disc Brake Pad Set', 'Clutch Plate Pressure Assembly',
             'Gearbox Transmission Housing', 'Car Shock Absorber Front',
             'Automobile Bumper Assembly', 'Exhaust Manifold Car OEM'],
    '8714': ['Motorcycle Chain Sprocket Kit', 'Bike Disc Brake Cable',
             'Scooter Front Wheel Rim', 'Two Wheeler Tyre Tube Set',
             'Moped Brake Shoe Assembly', 'Motorcycle Fork Assembly'],
}


def _generic_item_name(hsn: str, rng: random.Random) -> str:
    items = HSN_ITEMS.get(hsn, ['General Goods', 'Traded Merchandise', 'Mixed Goods'])
    return rng.choice(items)


# ─── Public API ───────────────────────────────────────────────────────────────

def get_sales_vouchers(gstin: str, period: str) -> list:
    """Return list[SalesVoucher] for this client+period (dry_run mode)."""
    if gstin == MEHTA_GSTIN:
        raise ValueError("Use testcase XML for Mehta Textile")

    spec = CLIENT_SPECS.get(gstin)
    if not spec:
        # Unknown client — return minimal mock
        return _fallback_sales(gstin, period)

    scenario = sum(ord(c) for c in gstin) % 4
    rng = random.Random(hash(gstin + period))
    vouchers = []
    mm_yy = period[:2] + period[4:6]  # e.g. "0326"

    # Bulk B2B sales
    buyers = spec['buyers']
    for i in range(spec['n_sales']):
        buyer = buyers[i % len(buyers)]
        inv_num = f"SAL/{mm_yy}/{i+1:04d}"
        vouchers.append(_make_sale(rng, period, inv_num, buyer[0], buyer[1], spec))

    # HSN-mismatch sale (always included — produces the flag)
    hsn_sale_num = f"SAL/{mm_yy}/HSN1"
    vouchers.append(_make_sale(
        rng, period, hsn_sale_num,
        buyers[0][0], buyers[0][1], spec,
        hsn_override=spec['hsn_wrong'],
        item_name_override=spec['item_name'],
    ))

    # Cancelled-buyer sale (scenarios 0 and 2)
    if scenario in (0, 2) and spec['cancelled_buyer']:
        cb_gstin, cb_name = spec['cancelled_buyer']
        inv_num = f"SAL/{mm_yy}/CB01"
        taxable = round(rng.uniform(*spec['sale_range']), 2)
        rate = spec['gst_rate']
        half = round(taxable * rate / 200, 2)
        cb_state = cb_gstin[:2]
        state = spec['state']
        if cb_state == state:
            cgst, sgst, igst = half, half, 0.0
        else:
            cgst, sgst, igst = 0.0, 0.0, round(taxable * rate / 100, 2)

        vouchers.append(SalesVoucher(
            date=_fmt_date(_period_to_date(period) + timedelta(days=5)),
            voucher_number=inv_num,
            guid=f"guid-sale-{inv_num}",
            buyer_name=cb_name,
            buyer_gstin=cb_gstin,
            place_of_supply=cb_state,
            taxable_value=taxable,
            invoice_total=round(taxable + cgst + sgst + igst, 2),
            cgst=cgst, sgst=sgst, igst=igst,
            supply_type='B2B',
            items=[InventoryItem(
                name=spec['item_name'], hsn_code=spec['hsn_correct'],
                gst_rate=float(spec['gst_rate']),
                quantity='100 Nos', rate_per_unit=str(round(taxable / 100, 2)),
                taxable_value=taxable,
            )],
        ))

    return vouchers


def get_purchase_vouchers(gstin: str, period: str) -> list:
    """Return list[PurchaseVoucher] for this client+period (dry_run mode)."""
    if gstin == MEHTA_GSTIN:
        raise ValueError("Use testcase XML for Mehta Textile")

    spec = CLIENT_SPECS.get(gstin)
    if not spec:
        return _fallback_purchases(gstin, period)

    scenario = sum(ord(c) for c in gstin) % 4
    rng = random.Random(hash(gstin + period + 'pur'))
    vouchers = []
    suppliers = spec['suppliers']
    mm_yy = period[:2] + period[4:6]

    for i in range(spec['n_purchases']):
        sup = suppliers[i % len(suppliers)]
        inv_num = f"{sup[0][5:10]}/{period[:2]}{period[4:6]}/{i+1:03d}"
        vouchers.append(_make_purchase(rng, period, inv_num, sup[0], sup[1], spec))

    # Missing-supplier purchase (scenarios 0 and 1) — in Tally, NOT in GSTR-2B
    if scenario in (0, 1) and spec['missing_supplier']:
        ms_gstin, ms_name, ms_inv, ms_taxable, ms_gst = spec['missing_supplier']
        vouchers.append(_make_purchase(
            rng, period, ms_inv, ms_gstin, ms_name, spec,
            taxable=float(ms_taxable), gst_amount=float(ms_gst),
        ))

    return vouchers


def get_gstr2b(gstin: str, period: str) -> dict:
    """
    Return GSTR-2B dict derived from the same purchase vouchers Tally would export.
    Amounts match exactly (no synthetic re-generation). Missing supplier excluded per scenario.
    """
    if gstin == MEHTA_GSTIN:
        raise ValueError("Use testcase GSTR-2B for Mehta Textile")

    spec = CLIENT_SPECS.get(gstin)
    if not spec:
        return _fallback_gstr2b(gstin, period)

    scenario = sum(ord(c) for c in gstin) % 4
    missing_gstin = spec['missing_supplier'][0] if spec.get('missing_supplier') else None

    # Filing date = next-month 10th
    next_mm = int(period[:2]) % 12 + 1
    next_yyyy = int(period[2:]) + (1 if int(period[:2]) == 12 else 0)
    filing_date = f"10-{next_mm:02d}-{next_yyyy}"

    # Build GSTR-2B directly from the purchase vouchers (guarantees exact match)
    purchases = get_purchase_vouchers(gstin, period)

    from collections import defaultdict
    by_supplier = defaultdict(list)
    sup_name_map = {s[0]: s[1] for s in spec['suppliers']}
    if spec.get('missing_supplier'):
        ms = spec['missing_supplier']
        sup_name_map[ms[0]] = ms[1]

    for pv in purchases:
        # Skip missing supplier (scenarios 0, 1) — they are in Tally but NOT in GSTR-2B
        if scenario in (0, 1) and missing_gstin and pv.supplier_gstin == missing_gstin:
            continue
        by_supplier[pv.supplier_gstin].append(pv)

    b2b = []
    for sup_gstin, pvlist in by_supplier.items():
        invoices = [{
            'inum': pv.voucher_number,
            'dt': pv.date,
            'val': pv.invoice_total,
            'pos': spec['state'],
            'rev': 'N', 'itcavl': 'Y', 'rsn': '', 'elg': 'Input',
            'items': [{'num': 1, 'rt': spec['gst_rate'], 'txval': pv.taxable_value,
                       'igst': pv.igst, 'cgst': pv.cgst, 'sgst': pv.sgst, 'cess': 0.0}],
        } for pv in pvlist]

        b2b.append({
            'ctin': sup_gstin,
            'suppName': sup_name_map.get(sup_gstin, 'Unknown Supplier'),
            'suppFilingStatus': 'Filed',
            'suppFilingDate': filing_date,
            'inv': invoices,
        })

    return {
        'data': {
            'gstin': gstin,
            'rtnprd': period,
            'gendt': datetime.now().strftime('%d-%m-%Y'),
            'docdata': {'b2b': b2b, 'b2ba': [], 'cdnr': [], 'impg': [], 'imps': []},
        }
    }


def get_cancelled_gstins(gstin: str) -> set:
    """GSTINs that are cancelled for this client's scenario."""
    scenario = sum(ord(c) for c in gstin) % 4
    if scenario not in (0, 2):
        return set()
    spec = CLIENT_SPECS.get(gstin, {})
    cb = spec.get('cancelled_buyer')
    return {cb[0]} if cb else set()


# ─── Fallback for clients not in CLIENT_SPECS ─────────────────────────────────

def _fallback_sales(gstin, period):
    rng = random.Random(hash(gstin + period))
    base = _period_to_date(period)
    return [SalesVoucher(
        date=_fmt_date(base + timedelta(days=i * 3)),
        voucher_number=f"SAL/MOCK/{i+1:03d}",
        guid=f"guid-fb-s-{i}",
        buyer_name=f"Customer {i+1}",
        buyer_gstin=gstin[:2] + 'AABCX' + str(i).zfill(4) + 'Y1Z1',
        place_of_supply=gstin[:2],
        taxable_value=round(rng.uniform(50_000, 200_000), 2),
        invoice_total=round(rng.uniform(56_000, 224_000), 2),
        cgst=round(rng.uniform(3_000, 12_000), 2),
        sgst=round(rng.uniform(3_000, 12_000), 2),
        igst=0.0, supply_type='B2B',
        items=[InventoryItem('Goods', '9999', 12.0, '100 Nos', '500', 50_000)],
    ) for i in range(5)]


def _fallback_purchases(gstin, period):
    rng = random.Random(hash(gstin + period + 'p'))
    base = _period_to_date(period)
    return [PurchaseVoucher(
        date=_fmt_date(base + timedelta(days=i * 4)),
        voucher_number=f"PUR/MOCK/{i+1:03d}",
        guid=f"guid-fb-p-{i}",
        supplier_name=f"Supplier {i+1}",
        supplier_gstin=gstin[:2] + 'AABCY' + str(i).zfill(4) + 'Z1Z1',
        taxable_value=round(rng.uniform(30_000, 120_000), 2),
        invoice_total=round(rng.uniform(33_600, 134_400), 2),
        cgst=round(rng.uniform(1_800, 7_200), 2),
        sgst=round(rng.uniform(1_800, 7_200), 2),
        igst=0.0,
        items=[InventoryItem('Input Goods', '9999', 12.0, '50 Nos', '600', 30_000)],
    ) for i in range(4)]


def _fallback_gstr2b(gstin, period):
    return {
        'data': {
            'gstin': gstin, 'rtnprd': period,
            'gendt': datetime.now().strftime('%d-%m-%Y'),
            'docdata': {'b2b': [], 'b2ba': [], 'cdnr': [], 'impg': [], 'imps': []},
        }
    }


# ─── Summary (used by run_pipeline for logging) ───────────────────────────────

def client_summary(gstin: str, period: str) -> dict:
    """Return entry counts for logging."""
    if gstin == MEHTA_GSTIN:
        return {'sales': 5, 'purchases': 4, 'gstr2b_suppliers': 3}
    spec = CLIENT_SPECS.get(gstin)
    if not spec:
        return {'sales': 5, 'purchases': 4, 'gstr2b_suppliers': 0}
    scenario = sum(ord(c) for c in gstin) % 4
    n_sales = spec['n_sales'] + 1 + (1 if scenario in (0, 2) and spec['cancelled_buyer'] else 0)
    n_purchases = spec['n_purchases'] + (1 if scenario in (0, 1) and spec['missing_supplier'] else 0)
    return {
        'sales': n_sales,
        'purchases': n_purchases,
        'gstr2b_suppliers': len(spec['suppliers']),
        'scenario': scenario,
        'industry': spec['industry'],
    }
