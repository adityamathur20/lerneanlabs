from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agent.models import SCHEDULE_A_SUBSTANCES
from agent.tally_ncb_parser import parse_tally_xml

DUMMY_XML    = Path("testcases/sample_trader_feb_may_2026/tally_export.xml")
DAYBOOK_XML  = Path("testcases/sample_tally_xml/daybook_april_2026.xml")
CLIENT_ID    = "mehta_chemical"


@pytest.fixture(scope="module")
def result():
    return parse_tally_xml(CLIENT_ID, DUMMY_XML)


@pytest.fixture(scope="module")
def daybook():
    return parse_tally_xml(CLIENT_ID, DAYBOOK_XML)


# --- Volume ---

def test_parses_all_vouchers(result):
    assert len(result.transactions) == 278

def test_no_parse_errors(result):
    assert len(result.errors) == 0

def test_all_txn_types_valid(result):
    types = {t.txn_type for t in result.transactions}
    assert types == {"PURCHASE", "SALE"}

def test_all_substances_schedule_a(result):
    substances = {t.substance for t in result.transactions}
    assert substances.issubset(SCHEDULE_A_SUBSTANCES)

def test_source_is_tally_xml(result):
    assert all(t.source == "tally_xml" for t in result.transactions)


# --- Date and date range ---

def test_all_dates_in_range(result):
    from datetime import date
    start = date(2026, 2, 10)
    end = date(2026, 5, 9)
    assert all(start <= t.date <= end for t in result.transactions)

def test_dates_parsed_correctly(result):
    # First voucher in the XML was PUR/2026/0001 on 2026-02-10
    first = result.transactions[0]
    from datetime import date
    assert first.date == date(2026, 2, 10)


# --- UDF field extraction ---

def test_counterparty_urn_extracted(result):
    # All non-anomaly entries should have URNs from UDF
    valid_txns = [t for t in result.transactions
                  if t.urn_status == "VALID"]
    assert len(valid_txns) > 250
    assert all(t.counterparty_urn.startswith("NCB-") for t in valid_txns)

def test_form_g_extracted(result):
    # All transactions should have a Form-G reference
    assert all(t.form_g_no.startswith("FG/") for t in result.transactions)


# --- Anomalies ---

def test_anomaly_a1_invalid_urn(result):
    # Entry 87 has URN NCB-GJ-2019-01234 (5 digits)
    entry = result.transactions[86]
    assert entry.urn_status == "INVALID_FORMAT"
    assert entry.urn_requires_manual_verification

def test_anomaly_a2_missing_urn(result):
    entry = result.transactions[142]
    assert entry.urn_status == "MISSING"
    assert entry.urn_requires_manual_verification

def test_anomaly_a3_zero_quantity(result):
    entry = result.transactions[200]
    assert entry.quantity_kg == Decimal("0.00")
    assert "ZERO_QUANTITY" in entry.anomaly_flags


# --- Quantity parsing ---

def test_quantities_are_decimal(result):
    for t in result.transactions:
        assert isinstance(t.quantity_kg, Decimal)

def test_non_anomaly_quantities_positive(result):
    non_zero = [t for t in result.transactions if "ZERO_QUANTITY" not in t.anomaly_flags]
    assert all(t.quantity_kg > 0 for t in non_zero)


# --- Consistency between Excel and Tally parsers ---

def test_same_transaction_count_as_excel():
    from agent.excel_ncb_parser import parse_excel
    excel_result = parse_excel(CLIENT_ID, Path("testcases/sample_trader_feb_may_2026/dummy_ledger.xlsx"))
    xml_result = parse_tally_xml(CLIENT_ID, DUMMY_XML)
    assert len(excel_result.transactions) == len(xml_result.transactions)

def test_substance_distribution_matches_excel():
    from agent.excel_ncb_parser import parse_excel
    from collections import Counter
    excel_result = parse_excel(CLIENT_ID, Path("testcases/sample_trader_feb_may_2026/dummy_ledger.xlsx"))
    xml_result = parse_tally_xml(CLIENT_ID, DUMMY_XML)
    excel_counts = Counter(t.substance for t in excel_result.transactions)
    xml_counts = Counter(t.substance for t in xml_result.transactions)
    assert excel_counts == xml_counts


# ---------------------------------------------------------------------------
# Daybook fixture tests — purchase/sale/manufacturing/multi-item/lookup
# ---------------------------------------------------------------------------

def test_daybook_no_parse_errors(daybook):
    assert daybook.errors == []


def test_daybook_payment_voucher_skipped(daybook):
    # Payment voucher (voucher 6) must be silently skipped — no inventory entries
    voucher_nos = {t.voucher_no for t in daybook.transactions}
    assert "PAY/2026/0031" not in voucher_nos


def test_daybook_purchase_single_item(daybook):
    txn = next(t for t in daybook.transactions if t.voucher_no == "PUR/2026/0042")
    assert txn.txn_type == "PURCHASE"
    assert txn.substance == "Anthranilic Acid"
    assert txn.quantity_kg == Decimal("498")
    assert txn.counterparty == "Deepak Nitrite Ltd"
    # URN from UDF field
    assert txn.counterparty_urn == "NCB-GJ-2014-012345"
    assert txn.urn_status == "VALID"


def test_daybook_purchase_multi_item(daybook):
    # PUR/2026/0048 has two Schedule A substances → two transactions
    multi = [t for t in daybook.transactions if t.voucher_no == "PUR/2026/0048"]
    assert len(multi) == 2
    substances = {t.substance for t in multi}
    assert substances == {"Anthranilic Acid", "Acetic Anhydride"}
    # Both share same counterparty
    assert all(t.counterparty == "Aarti Industries Ltd" for t in multi)


def test_daybook_urn_lookup_fallback(daybook):
    # SAL/2026/0168 — UDF is empty, must resolve from counterparty_urn_lookup
    txn = next(t for t in daybook.transactions if t.voucher_no == "SAL/2026/0168")
    assert txn.counterparty_urn == "NCB-GJ-2019-001234"
    assert txn.urn_status == "VALID"


def test_daybook_sale_form_g_present(daybook):
    txn = next(t for t in daybook.transactions if t.voucher_no == "SAL/2026/0168")
    assert txn.form_g_no == "FG/202604/0168"
    assert "MISSING_FORM_G" not in txn.anomaly_flags


def test_daybook_sale_missing_urn_and_form_g(daybook):
    txn = next(t for t in daybook.transactions if t.voucher_no == "SAL/2026/0180")
    assert "URN_MISSING" in txn.anomaly_flags or txn.urn_requires_manual_verification
    assert "MISSING_FORM_G" in txn.anomaly_flags


def test_daybook_manufacturing_journal_produces_two_txns(daybook):
    mfg = [t for t in daybook.transactions if t.voucher_no == "MFG/2026/0014"]
    assert len(mfg) == 2
    types = {t.txn_type for t in mfg}
    assert types == {"MANUFACTURE", "CONSUMPTION"}


def test_daybook_manufacture_txn(daybook):
    txn = next(t for t in daybook.transactions
               if t.voucher_no == "MFG/2026/0014" and t.txn_type == "MANUFACTURE")
    assert txn.substance == "N-Acetylanthranilic Acid"
    assert txn.quantity_kg == Decimal("93")
    assert txn.counterparty == ""   # internal movement


def test_daybook_consumption_txn(daybook):
    txn = next(t for t in daybook.transactions
               if t.voucher_no == "MFG/2026/0014" and t.txn_type == "CONSUMPTION")
    assert txn.substance == "Anthranilic Acid"
    assert txn.quantity_kg == Decimal("100")


def test_daybook_sale_quantity_always_positive(daybook):
    # Tally stores sales as negative ACTUALQTY; parser must return abs value
    sales = [t for t in daybook.transactions if t.txn_type == "SALE"]
    assert all(t.quantity_kg > 0 for t in sales)


def test_daybook_all_quantities_decimal(daybook):
    for t in daybook.transactions:
        assert isinstance(t.quantity_kg, Decimal)


# ---------------------------------------------------------------------------
# Google Sheet URL extraction (unit test — no network call)
# ---------------------------------------------------------------------------

def test_google_sheet_url_id_extraction():
    import re
    from agent.excel_ncb_parser import _SHEET_ID_RE
    url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit#gid=0"
    match = _SHEET_ID_RE.search(url)
    assert match is not None
    assert match.group(1) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


def test_google_sheet_url_invalid_raises():
    from agent.excel_ncb_parser import parse_google_sheet_url
    import pytest
    with pytest.raises(ValueError, match="Cannot extract sheet ID"):
        parse_google_sheet_url(CLIENT_ID, "https://not-a-sheets-url.com/something")
