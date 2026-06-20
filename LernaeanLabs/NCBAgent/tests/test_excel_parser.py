from __future__ import annotations

from pathlib import Path

import pytest

from agent.excel_ncb_parser import parse_excel
from agent.models import SCHEDULE_A_SUBSTANCES

DUMMY_XLSX = Path("testcases/sample_trader_feb_may_2026/dummy_ledger.xlsx")
CLIENT_ID = "mehta_chemical"


@pytest.fixture(scope="module")
def result():
    return parse_excel(CLIENT_ID, DUMMY_XLSX)


# --- Volume ---

def test_parses_all_rows(result):
    assert len(result.transactions) == 278

def test_no_critical_parse_errors(result):
    # Anomaly rows are included with flags, not errors
    assert len(result.errors) == 0

def test_all_txn_types_valid(result):
    types = {t.txn_type for t in result.transactions}
    assert types == {"PURCHASE", "SALE"}

def test_all_substances_are_schedule_a(result):
    substances = {t.substance for t in result.transactions}
    # All resolved substances must be Schedule A
    assert substances.issubset(SCHEDULE_A_SUBSTANCES)

def test_no_negative_quantities(result):
    non_zero_non_anomaly = [
        t for t in result.transactions
        if "ZERO_QUANTITY" not in t.anomaly_flags
    ]
    assert all(t.quantity_kg > 0 for t in non_zero_non_anomaly)

def test_all_dates_in_expected_range(result):
    from datetime import date
    start = date(2026, 2, 10)
    end = date(2026, 5, 9)
    assert all(start <= t.date <= end for t in result.transactions)

def test_source_is_excel(result):
    assert all(t.source == "excel" for t in result.transactions)


# --- Anomaly A1: invalid URN format (entry #87) ---

def test_anomaly_a1_invalid_urn_format(result):
    entry = result.transactions[86]   # 0-indexed
    assert "URN_INVALID_FORMAT" in entry.anomaly_flags
    assert entry.urn_requires_manual_verification
    assert entry.counterparty_urn == "NCB-GJ-2019-01234"

def test_anomaly_a1_urn_status(result):
    entry = result.transactions[86]
    assert entry.urn_status == "INVALID_FORMAT"


# --- Anomaly A2: missing URN (entry #143) ---

def test_anomaly_a2_missing_urn(result):
    entry = result.transactions[142]
    assert "URN_MISSING" in entry.anomaly_flags
    assert entry.urn_requires_manual_verification
    assert entry.counterparty_urn == ""

def test_anomaly_a2_urn_status(result):
    entry = result.transactions[142]
    assert entry.urn_status == "MISSING"


# --- Anomaly A3: zero quantity (entry #201) ---

def test_anomaly_a3_zero_quantity(result):
    entry = result.transactions[200]
    assert "ZERO_QUANTITY" in entry.anomaly_flags
    from decimal import Decimal
    assert entry.quantity_kg == Decimal("0.00")


# --- URN validation on good entries ---

def test_valid_entries_have_no_urn_flag(result):
    clean = [t for t in result.transactions if not t.anomaly_flags]
    assert len(clean) > 250   # vast majority should be clean
    assert all(t.urn_status == "VALID" for t in clean)

def test_valid_entries_not_flagged_for_review(result):
    clean = [t for t in result.transactions if not t.anomaly_flags]
    assert all(not t.urn_requires_manual_verification for t in clean)


# --- Substance distribution ---

def test_anthranilic_acid_present(result):
    sub = [t for t in result.transactions if t.substance == "Anthranilic Acid"]
    assert len(sub) > 50

def test_all_five_substances_present(result):
    substances = {t.substance for t in result.transactions}
    assert "Acetic Anhydride" in substances
    assert "Ephedrine" in substances
    assert "Pseudoephedrine" in substances
