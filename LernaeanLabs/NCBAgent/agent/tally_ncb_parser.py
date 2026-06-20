"""
Tally Day Book XML parser → list[NCBTransaction]

Reads Tally ERP 9 / TallyPrime XML export format:
  ENVELOPE > BODY > EXPORTDATA > REQUESTDATA > TALLYMESSAGE > VOUCHER

Supported voucher types
-----------------------
  Purchase / Purchases  → NCBTransaction(txn_type="PURCHASE")
  Sales / Sale          → NCBTransaction(txn_type="SALE")
  Manufacturing Journal → NCBTransaction(txn_type="MANUFACTURE")  — produced items
  Manufacturing Journal → NCBTransaction(txn_type="CONSUMPTION")  — consumed items
  Stock Journal         → same as Manufacturing Journal
  (all others are silently skipped)

Multi-item vouchers
-------------------
A single Tally voucher may carry multiple ALLINVENTORYENTRIES.LIST entries
(e.g. a purchase of three chemicals on one invoice).  Each entry that maps
to a Schedule A substance becomes a separate NCBTransaction sharing the same
date, voucher number, and counterparty.

Counterparty URN resolution
---------------------------
Priority order:
  1. UDF field inside the voucher XML (field name configured in tally_config.yaml)
  2. counterparty_urn_lookup table in tally_config.yaml (operator-maintained map
     of company name → URN, populated once and updated as new suppliers are added)
  3. Empty string  →  flagged as URN_MISSING, requires human review at approval gate

Form G auto-detection
---------------------
SALE vouchers are expected to carry a Form G consignment note number as a UDF
field (configured via form_g_udf_field in tally_config.yaml).  If missing, the
transaction is flagged MISSING_FORM_G — the operator fills it during the human
approval gate, not at parse time.  No sequential auto-numbering is applied here
so the audit trail is never synthetic.

Manufacturing journal entry classification
------------------------------------------
TallyPrime uses FLOWTYPE ("Consumed" / "Produced") as the canonical indicator.
Older TallyERP 9 exports omit FLOWTYPE; we fall back to ISDEEMEDPOSITIVE
("Yes" = stock increase = Produced, "No" = stock decrease = Consumed).
When both are absent we fall back to the sign of ACTUALQTY.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from agent.config_loader import TallyConfig, load_client_profile, load_tally_config
from agent.models import NCBTransaction, ParseError, ParseResult
from agent.urn_validator import validate_urn

CLIENTS_BASE = Path("clients")

# Tally uses YYYYMMDD for all dates
_TALLY_DATE_FMT = "%Y%m%d"

# Voucher types that map to purchase/sale
_PURCHASE_TYPES = frozenset({"purchase", "purchases"})
_SALE_TYPES     = frozenset({"sales", "sale"})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_tally_xml(
    client_id: str,
    xml_path: Path,
    clients_base: Path = CLIENTS_BASE,
) -> ParseResult:
    config  = load_tally_config(client_id, clients_base)
    _profile = load_client_profile(client_id, clients_base)  # loaded for side-effect validation

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        return ParseResult(
            transactions=[],
            errors=[ParseError(str(xml_path), 0, "xml", "", f"XML parse error: {e}")],
        )

    root = tree.getroot()
    vouchers = root.findall(".//VOUCHER")

    transactions: list[NCBTransaction] = []
    errors: list[ParseError] = []

    for entry_no, voucher in enumerate(vouchers, start=1):
        txns, row_errors = _parse_voucher(client_id, config, voucher, entry_no, str(xml_path))
        errors.extend(row_errors)
        transactions.extend(txns)

    return ParseResult(transactions=transactions, errors=errors)


# ---------------------------------------------------------------------------
# Voucher dispatcher
# ---------------------------------------------------------------------------

def _parse_voucher(
    client_id: str,
    config: TallyConfig,
    voucher: ET.Element,
    entry_no: int,
    source_label: str,
) -> tuple[list[NCBTransaction], list[ParseError]]:

    vch_type_raw = (
        voucher.get("VCHTYPE")
        or _text(voucher, "VOUCHERTYPENAME")
        or ""
    ).strip()
    vch_type_lower = vch_type_raw.lower()

    if vch_type_lower in _PURCHASE_TYPES:
        return _parse_purchase_sale(client_id, config, voucher, entry_no, source_label,
                                    txn_type="PURCHASE")

    if vch_type_lower in _SALE_TYPES:
        return _parse_purchase_sale(client_id, config, voucher, entry_no, source_label,
                                    txn_type="SALE")

    mfg_lower = [v.lower() for v in config.manufacturing_voucher_types]
    if vch_type_lower in mfg_lower:
        return _parse_manufacturing_journal(client_id, config, voucher, entry_no, source_label)

    # All other voucher types (receipts, payments, contra, credit note, etc.) — skip
    return [], []


# ---------------------------------------------------------------------------
# Purchase / Sale parser  (handles multi-item vouchers)
# ---------------------------------------------------------------------------

def _parse_purchase_sale(
    client_id: str,
    config: TallyConfig,
    voucher: ET.Element,
    entry_no: int,
    source_label: str,
    txn_type: str,
) -> tuple[list[NCBTransaction], list[ParseError]]:

    errors: list[ParseError] = []

    parsed_date = _get_voucher_date(voucher, entry_no, source_label, errors)
    if parsed_date is None:
        return [], errors

    voucher_no   = _text(voucher, "VOUCHERNUMBER") or ""
    counterparty = _text(voucher, "PARTYLEDGERNAME") or ""

    udf_urn  = (_udf_text(voucher, config.urn_udf_field) or "").strip()
    form_g   = (_udf_text(voucher, config.form_g_udf_field) or "").strip()
    resolved_urn = config.resolve_counterparty_urn(counterparty, udf_urn)

    # Collect all inventory entries for this voucher
    inv_entries = voucher.findall("ALLINVENTORYENTRIES.LIST")
    if not inv_entries:
        return [], []  # ledger-only voucher, no stock movement

    transactions: list[NCBTransaction] = []

    for inv_idx, inv in enumerate(inv_entries):
        raw_substance = _text(inv, "STOCKITEMNAME") or ""
        resolved_substance = config.resolve_substance(raw_substance)
        if not resolved_substance:
            # Not a Schedule A substance — silently skip this line item
            continue

        raw_qty = _text(inv, "ACTUALQTY") or ""
        quantity_kg = _parse_tally_qty(raw_qty, config.quantity_unit)
        if quantity_kg is None:
            errors.append(ParseError(
                source_label, entry_no, "ACTUALQTY", raw_qty,
                f"Cannot parse quantity '{raw_qty}' for item '{raw_substance}'"
            ))
            continue

        quantity_kg = abs(quantity_kg)  # Tally sometimes stores as negative for sales

        raw_rate   = _text(inv, "RATE") or "0"
        raw_amount = _text(inv, "AMOUNT") or "0"
        rate   = _parse_tally_rate(raw_rate, config.quantity_unit)
        amount = _safe_decimal(raw_amount)

        anomaly_flags: list[str] = []
        if quantity_kg == Decimal("0.00"):
            anomaly_flags.append("ZERO_QUANTITY")
        if txn_type == "SALE" and not form_g:
            anomaly_flags.append("MISSING_FORM_G")

        urn_result = validate_urn(resolved_urn if resolved_urn else None)
        if urn_result.requires_manual_verification:
            anomaly_flags.append(f"URN_{urn_result.status.value}")

        transactions.append(NCBTransaction(
            client_id=client_id,
            date=parsed_date,
            voucher_no=voucher_no,
            txn_type=txn_type,
            substance=resolved_substance,
            item_code="",
            counterparty=counterparty,
            counterparty_urn=resolved_urn,
            quantity_kg=quantity_kg,
            rate_inr_per_kg=rate,
            amount_inr=amount,
            form_g_no=form_g,
            urn_status=urn_result.status.value,
            urn_requires_manual_verification=urn_result.requires_manual_verification,
            source="tally_xml",
            anomaly_flags=anomaly_flags,
            raw_substance=raw_substance,
            row_number=entry_no * 100 + inv_idx,  # unique across multi-item entries
        ))

    return transactions, errors


# ---------------------------------------------------------------------------
# Manufacturing Journal parser
# ---------------------------------------------------------------------------

def _parse_manufacturing_journal(
    client_id: str,
    config: TallyConfig,
    voucher: ET.Element,
    entry_no: int,
    source_label: str,
) -> tuple[list[NCBTransaction], list[ParseError]]:
    """
    Manufacturing Journal has no single counterparty (it's an internal stock movement).
    Each inventory entry is classified as MANUFACTURE (produced) or CONSUMPTION (consumed).

    Classification priority:
      1. FLOWTYPE tag: "Produced" → MANUFACTURE, "Consumed" → CONSUMPTION
      2. ISDEEMEDPOSITIVE: "Yes" → MANUFACTURE, "No" → CONSUMPTION
      3. Sign of ACTUALQTY: positive → MANUFACTURE, negative → CONSUMPTION
    """
    errors: list[ParseError] = []

    parsed_date = _get_voucher_date(voucher, entry_no, source_label, errors)
    if parsed_date is None:
        return [], errors

    voucher_no = _text(voucher, "VOUCHERNUMBER") or ""
    inv_entries = voucher.findall("ALLINVENTORYENTRIES.LIST")
    if not inv_entries:
        return [], []

    transactions: list[NCBTransaction] = []

    for inv_idx, inv in enumerate(inv_entries):
        raw_substance = _text(inv, "STOCKITEMNAME") or ""
        resolved_substance = config.resolve_substance(raw_substance)
        if not resolved_substance:
            continue

        raw_qty = _text(inv, "ACTUALQTY") or ""
        quantity_kg = _parse_tally_qty(raw_qty, config.quantity_unit)
        if quantity_kg is None:
            errors.append(ParseError(
                source_label, entry_no, "ACTUALQTY", raw_qty,
                f"Cannot parse manufacturing qty '{raw_qty}' for '{raw_substance}'"
            ))
            continue

        txn_type = _classify_mfg_entry(inv, quantity_kg)
        quantity_kg = abs(quantity_kg)

        anomaly_flags: list[str] = []
        if quantity_kg == Decimal("0.00"):
            anomaly_flags.append("ZERO_QUANTITY")

        transactions.append(NCBTransaction(
            client_id=client_id,
            date=parsed_date,
            voucher_no=voucher_no,
            txn_type=txn_type,
            substance=resolved_substance,
            item_code="",
            counterparty="",            # internal movement — no counterparty
            counterparty_urn="",
            quantity_kg=quantity_kg,
            rate_inr_per_kg=Decimal("0.00"),
            amount_inr=_safe_decimal(_text(inv, "AMOUNT") or "0"),
            form_g_no="",               # manufacturing journals don't carry Form G
            urn_status="NOT_APPLICABLE",
            urn_requires_manual_verification=False,
            source="tally_xml",
            anomaly_flags=anomaly_flags,
            raw_substance=raw_substance,
            row_number=entry_no * 100 + inv_idx,
        ))

    return transactions, errors


def _classify_mfg_entry(inv: ET.Element, quantity_kg: Decimal) -> str:
    """Determine MANUFACTURE vs CONSUMPTION from Tally inventory entry element."""
    flow_type = (_text(inv, "FLOWTYPE") or "").strip().lower()
    if flow_type == "produced":
        return "MANUFACTURE"
    if flow_type == "consumed":
        return "CONSUMPTION"

    deemed_positive = (_text(inv, "ISDEEMEDPOSITIVE") or "").strip().lower()
    if deemed_positive == "yes":
        return "MANUFACTURE"
    if deemed_positive == "no":
        return "CONSUMPTION"

    # Final fallback: positive qty = produced (stock increases), negative = consumed
    return "MANUFACTURE" if quantity_kg >= Decimal("0") else "CONSUMPTION"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_voucher_date(
    voucher: ET.Element,
    entry_no: int,
    source_label: str,
    errors: list[ParseError],
) -> Optional[date]:
    raw_date = _text(voucher, "DATE")
    parsed = _parse_tally_date(raw_date)
    if parsed is None:
        errors.append(ParseError(
            source_label, entry_no, "DATE", str(raw_date),
            f"Cannot parse Tally date '{raw_date}'"
        ))
    return parsed


def _text(element: ET.Element, tag: str) -> Optional[str]:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _udf_text(voucher: ET.Element, field_name: str) -> Optional[str]:
    """
    Tally stores UDF values in a nested structure:
      <FIELDNAME.LIST><FIELDNAME>value</FIELDNAME></FIELDNAME.LIST>
    Also handles namespace-prefixed tags.
    """
    for child in voucher:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == f"{field_name}.LIST":
            for subchild in child:
                sub_local = subchild.tag.split("}")[-1] if "}" in subchild.tag else subchild.tag
                if sub_local == field_name:
                    return subchild.text.strip() if subchild.text else None
    return None


def _parse_tally_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), _TALLY_DATE_FMT).date()
    except ValueError:
        return None


def _parse_tally_qty(raw: str, unit: str) -> Optional[Decimal]:
    """Parse '150 Kg', '-150.5 Kg', '150' → Decimal (sign preserved)."""
    if not raw:
        return None
    cleaned = re.sub(re.escape(unit), "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*(kg|kgs|kilogram[s]?)\s*", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        return Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None


def _parse_tally_rate(raw: str, unit: str) -> Decimal:
    """Parse '794.37/Kg' → Decimal('794.37')."""
    cleaned = raw.split("/")[0].strip().replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0.00")


def _safe_decimal(raw: str) -> Decimal:
    try:
        return abs(Decimal(raw.replace(",", "").strip()))
    except InvalidOperation:
        return Decimal("0.00")
