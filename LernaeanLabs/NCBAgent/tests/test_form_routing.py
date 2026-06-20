"""
Tests for form routing logic and Form C (manufacturer daily register).

Covers:
  - entity_type drives correct form selection
  - Form C balance formula (includes produced and consumed)
  - Form C entries have production and consumption fields populated
  - generate_daily_register() raises for unknown entity_type
  - generate_daily_register() returns correct file names
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agent.config_loader import load_client_profile
from agent.daily_register_module import generate_registers
from agent.models import NCBTransaction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRADER_CLIENT_ID = "mehta_chemical"
MANUFACTURER_CLIENT_ID = "demo_manufacturer"
PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)


def _make_txn(txn_type: str, substance: str, qty: str, d: date,
              client_id: str = "demo_manufacturer") -> NCBTransaction:
    return NCBTransaction(
        client_id=client_id,
        date=d,
        voucher_no=f"V/{txn_type[:3]}/001",
        txn_type=txn_type,
        substance=substance,
        item_code="",
        counterparty="Test Party",
        counterparty_urn="NCB-MH-2019-003456",
        quantity_kg=Decimal(qty),
        rate_inr_per_kg=Decimal("0.00"),
        amount_inr=Decimal("0.00"),
        form_g_no="FG/TEST/001",
        urn_status="VALID",
        urn_requires_manual_verification=False,
        source="test",
    )


@pytest.fixture(scope="module")
def trader_profile():
    return load_client_profile(TRADER_CLIENT_ID)


@pytest.fixture(scope="module")
def manufacturer_profile():
    return load_client_profile(MANUFACTURER_CLIENT_ID)


# ---------------------------------------------------------------------------
# entity_type field
# ---------------------------------------------------------------------------

def test_trader_profile_entity_type(trader_profile):
    assert trader_profile.entity_type == "trader"

def test_manufacturer_profile_entity_type(manufacturer_profile):
    assert manufacturer_profile.entity_type == "manufacturer"


# ---------------------------------------------------------------------------
# Register generation — manufacturer balance formula
# ---------------------------------------------------------------------------

def test_manufacturer_balance_includes_produced(manufacturer_profile):
    """closing = opening + received + produced - dispatched - consumed"""
    txns = [
        _make_txn("PURCHASE",    "Acetic Anhydride", "100.000", date(2026, 4, 1)),
        _make_txn("MANUFACTURE", "Acetic Anhydride", "50.000",  date(2026, 4, 1)),
        _make_txn("SALE",        "Acetic Anhydride", "80.000",  date(2026, 4, 1)),
        _make_txn("CONSUMPTION", "Acetic Anhydride", "20.000",  date(2026, 4, 1)),
    ]
    regs = generate_registers(txns, manufacturer_profile, PERIOD_START, PERIOD_END)
    entry = regs["Acetic Anhydride"].entries[0]   # April 1

    opening = manufacturer_profile.opening_stock_kg["Acetic Anhydride"]
    expected_closing = opening + Decimal("100") + Decimal("50") - Decimal("80") - Decimal("20")

    assert entry.total_produced_kg == Decimal("50.000")
    assert entry.total_consumed_kg == Decimal("20.000")
    assert entry.closing_kg == expected_closing


def test_trader_balance_ignores_produce_consume(trader_profile):
    """Trader register: only PURCHASE and SALE affect balance."""
    txns = [
        _make_txn("PURCHASE", "Anthranilic Acid", "100.000", date(2026, 4, 1),
                  client_id="mehta_chemical"),
        _make_txn("SALE",     "Anthranilic Acid", "40.000",  date(2026, 4, 1),
                  client_id="mehta_chemical"),
    ]
    regs = generate_registers(txns, trader_profile, PERIOD_START, PERIOD_END)
    entry = regs["Anthranilic Acid"].entries[0]

    opening = trader_profile.opening_stock_kg["Anthranilic Acid"]
    expected_closing = opening + Decimal("100") - Decimal("40")

    assert entry.closing_kg == expected_closing
    assert entry.total_produced_kg == Decimal("0.00")
    assert entry.total_consumed_kg == Decimal("0.00")


def test_manufacturer_nil_when_no_txns(manufacturer_profile):
    regs = generate_registers([], manufacturer_profile, PERIOD_START, PERIOD_END)
    for reg in regs.values():
        assert all(e.nil_transaction for e in reg.entries)


def test_manufacturer_continuity(manufacturer_profile):
    """Balance must chain day-to-day: closing[i] == opening[i+1]."""
    txns = [
        _make_txn("PURCHASE",    "Acetic Anhydride", "100.000", date(2026, 4, 1)),
        _make_txn("MANUFACTURE", "Acetic Anhydride", "30.000",  date(2026, 4, 2)),
        _make_txn("SALE",        "Acetic Anhydride", "50.000",  date(2026, 4, 3)),
    ]
    regs = generate_registers(txns, manufacturer_profile, PERIOD_START, PERIOD_END)
    entries = regs["Acetic Anhydride"].entries
    for i in range(len(entries) - 1):
        assert entries[i].closing_kg == entries[i + 1].opening_kg


def test_manufacturer_negative_stock_flagged(manufacturer_profile):
    """A dispatch larger than opening should flag the entry for review."""
    opening = manufacturer_profile.opening_stock_kg["Anthranilic Acid"]
    huge_sale = str(opening + Decimal("9999"))
    txns = [_make_txn("SALE", "Anthranilic Acid", huge_sale, date(2026, 4, 1))]
    regs = generate_registers(txns, manufacturer_profile, PERIOD_START, PERIOD_END)
    entry = regs["Anthranilic Acid"].entries[0]
    assert entry.requires_human_review
    assert entry.closing_kg < Decimal("0")


# ---------------------------------------------------------------------------
# PDF routing — generate_daily_register()
# ---------------------------------------------------------------------------

def test_routing_trader_produces_form_d(tmp_path, trader_profile):
    from agent.output.pdf_generator import generate_daily_register
    from agent.daily_register_module import generate_registers as gr

    regs = gr([], trader_profile, date(2026, 4, 1), date(2026, 4, 30))
    for substance, reg in regs.items():
        paths = generate_daily_register(reg, trader_profile, 2026, 4, tmp_path)
        assert len(paths) == 1
        assert paths[0].name.startswith("form_d_")


def test_routing_manufacturer_produces_form_c(tmp_path, manufacturer_profile):
    from agent.output.pdf_generator import generate_daily_register
    from agent.daily_register_module import generate_registers as gr

    regs = gr([], manufacturer_profile, date(2026, 4, 1), date(2026, 4, 30))
    for substance, reg in regs.items():
        paths = generate_daily_register(reg, manufacturer_profile, 2026, 4, tmp_path)
        assert len(paths) == 1
        assert paths[0].name.startswith("form_c_")


def test_routing_both_produces_form_c_and_d(tmp_path, manufacturer_profile):
    from agent.output.pdf_generator import generate_daily_register
    from agent.daily_register_module import generate_registers as gr
    from agent.config_loader import ClientProfile

    both_profile = ClientProfile(
        **{**manufacturer_profile.__dict__, "entity_type": "both"}
    )
    regs = gr([], both_profile, date(2026, 4, 1), date(2026, 4, 30))
    for substance, reg in regs.items():
        paths = generate_daily_register(reg, both_profile, 2026, 4, tmp_path)
        names = [p.name for p in paths]
        assert any(n.startswith("form_c_") for n in names)
        assert any(n.startswith("form_d_") for n in names)


def test_routing_unknown_entity_type_raises(tmp_path, manufacturer_profile):
    from agent.output.pdf_generator import generate_daily_register
    from agent.daily_register_module import generate_registers as gr
    from agent.config_loader import ClientProfile

    bad_profile = ClientProfile(
        **{**manufacturer_profile.__dict__, "entity_type": "wholesaler"}
    )
    regs = gr([], bad_profile, date(2026, 4, 1), date(2026, 4, 30))
    reg = next(iter(regs.values()))
    with pytest.raises(ValueError, match="Unknown entity_type"):
        generate_daily_register(reg, bad_profile, 2026, 4, tmp_path)


def test_form_c_pdf_renders_without_crash(tmp_path, manufacturer_profile):
    """Smoke test — Form C PDF must be written without exceptions."""
    from agent.output.pdf_generator import generate_form_c
    from agent.daily_register_module import generate_registers as gr

    txns = [
        _make_txn("PURCHASE",    "Acetic Anhydride", "200.000", date(2026, 4, 6)),
        _make_txn("MANUFACTURE", "Acetic Anhydride", "100.000", date(2026, 4, 7)),
        _make_txn("SALE",        "Acetic Anhydride", "150.000", date(2026, 4, 8)),
        _make_txn("CONSUMPTION", "Acetic Anhydride", "30.000",  date(2026, 4, 9)),
    ]
    regs = gr(txns, manufacturer_profile, date(2026, 4, 1), date(2026, 4, 30))
    out = tmp_path / "form_c_test.pdf"
    generate_form_c(regs["Acetic Anhydride"], manufacturer_profile, 2026, 4, out)
    assert out.exists()
    assert out.stat().st_size > 5_000   # non-trivial PDF
