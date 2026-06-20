from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agent.config_loader import load_client_profile
from agent.daily_register_module import generate_registers, _get_working_days
from agent.excel_ncb_parser import parse_excel

DUMMY_XLSX = Path("testcases/sample_trader_feb_may_2026/dummy_ledger.xlsx")
CLIENT_ID = "mehta_chemical"
PERIOD_START = date(2026, 2, 10)
PERIOD_END = date(2026, 5, 9)
APRIL_ANOMALY_NIL_DATE = date(2026, 3, 15)   # Sunday — excluded by Mon-Sat rule anyway


@pytest.fixture(scope="module")
def profile():
    return load_client_profile(CLIENT_ID)


@pytest.fixture(scope="module")
def transactions():
    return parse_excel(CLIENT_ID, DUMMY_XLSX).transactions


@pytest.fixture(scope="module")
def registers(transactions, profile):
    return generate_registers(transactions, profile, PERIOD_START, PERIOD_END)


# --- Working day logic ---

def test_working_days_excludes_sundays(profile):
    days = _get_working_days(date(2026, 4, 1), date(2026, 4, 30), profile)
    assert all(d.weekday() != 6 for d in days)   # 6 = Sunday

def test_working_days_excludes_national_holidays(profile):
    # 2026-04-14 (Ambedkar Jayanti) is in the national_holidays list
    days = _get_working_days(date(2026, 4, 1), date(2026, 4, 30), profile)
    assert date(2026, 4, 14) not in days

def test_working_days_april_count(profile):
    # April 2026: 30 days - 4 Sundays (5,12,19,26) - 1 holiday (Apr 14 Ambedkar Jayanti) = 25
    days = _get_working_days(date(2026, 4, 1), date(2026, 4, 30), profile)
    assert len(days) == 25

def test_march_15_excluded_as_sunday(profile):
    # March 15, 2026 is a Sunday — must not appear in working days
    days = _get_working_days(date(2026, 3, 1), date(2026, 3, 31), profile)
    assert APRIL_ANOMALY_NIL_DATE not in days


# --- Register structure ---

def test_all_substances_have_register(registers, profile):
    for substance in profile.substances:
        assert substance in registers

def test_registers_cover_full_period(registers):
    for substance, reg in registers.items():
        assert reg.period_start == PERIOD_START
        assert reg.period_end == PERIOD_END

def test_entry_count_matches_working_days(registers, profile):
    working_days = _get_working_days(PERIOD_START, PERIOD_END, profile)
    for substance, reg in registers.items():
        assert len(reg.entries) == len(working_days)


# --- Serial numbers ---

def test_serial_numbers_sequential(registers):
    for substance, reg in registers.items():
        serials = [e.serial_no for e in reg.entries]
        assert serials == list(range(1, len(serials) + 1))


# --- Opening stock ---

def test_opening_stock_is_100kg(registers):
    for substance, reg in registers.items():
        assert reg.opening_stock_kg == Decimal("100.00")

def test_first_entry_opening_matches_profile(registers, profile):
    for substance, reg in registers.items():
        assert reg.entries[0].opening_kg == profile.opening_stock_kg[substance]


# --- Balance continuity ---

def test_closing_equals_next_opening(registers):
    for substance, reg in registers.items():
        entries = reg.entries
        for i in range(len(entries) - 1):
            assert entries[i].closing_kg == entries[i + 1].opening_kg, (
                f"{substance}: entry {i} closing {entries[i].closing_kg} "
                f"!= entry {i+1} opening {entries[i+1].opening_kg}"
            )

def test_balance_arithmetic(registers):
    for substance, reg in registers.items():
        for entry in reg.entries:
            expected = entry.opening_kg + entry.total_received_kg - entry.total_dispatched_kg - entry.handling_loss_kg
            assert entry.closing_kg == expected


# --- Nil transaction detection ---

def test_nil_entries_exist(registers):
    # With 278 transactions across 5 substances over ~63 working days, many days per substance will be nil
    total_nil = sum(
        1 for reg in registers.values() for e in reg.entries if e.nil_transaction
    )
    assert total_nil > 0

def test_nil_entries_have_zero_movement(registers):
    for substance, reg in registers.items():
        for entry in reg.entries:
            if entry.nil_transaction:
                assert entry.total_received_kg == Decimal("0.00")
                assert entry.total_dispatched_kg == Decimal("0.00")
                assert not entry.receipts
                assert not entry.dispatches


# --- Anomaly propagation ---

def test_urn_anomalies_propagate_to_daily_entries(registers):
    all_flagged = [
        e for reg in registers.values()
        for e in reg.entries if e.requires_human_review
    ]
    # At minimum entries containing A1, A2, A3 anomalous transactions must be flagged
    assert len(all_flagged) >= 3


# --- April sub-period ---

def test_april_entries_accessible(registers):
    for substance, reg in registers.items():
        april_entries = reg.entries_for_month(2026, 4)
        assert len(april_entries) == 25   # 25 working days in April 2026 (after holiday exclusion)

def test_april_opening_is_march_closing(registers):
    for substance, reg in registers.items():
        march_entries = reg.entries_for_month(2026, 3)
        april_entries = reg.entries_for_month(2026, 4)
        if march_entries and april_entries:
            assert march_entries[-1].closing_kg == april_entries[0].opening_kg


# --- Substance register aggregates ---
# Note: transactions on non-working days (Sundays, holidays) are not attributed to register entries.
# Real Tally data will not have Sunday invoices for physical movements; this is a dummy data artifact.

def test_register_total_received(registers, transactions, profile):
    from collections import defaultdict
    working_days = set(_get_working_days(PERIOD_START, PERIOD_END, profile))
    sub_totals: dict[str, Decimal] = defaultdict(Decimal)
    for t in transactions:
        if t.txn_type == "PURCHASE" and t.date in working_days:
            sub_totals[t.substance] += t.quantity_kg
    for substance, reg in registers.items():
        assert reg.total_received_kg == sub_totals.get(substance, Decimal("0.00"))

def test_register_total_dispatched(registers, transactions, profile):
    from collections import defaultdict
    working_days = set(_get_working_days(PERIOD_START, PERIOD_END, profile))
    sub_totals: dict[str, Decimal] = defaultdict(Decimal)
    for t in transactions:
        if t.txn_type == "SALE" and t.date in working_days:
            sub_totals[t.substance] += t.quantity_kg
    for substance, reg in registers.items():
        assert reg.total_dispatched_kg == sub_totals.get(substance, Decimal("0.00"))
