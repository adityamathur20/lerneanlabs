"""
Daily Register Module — Phase 2

Converts a flat list of NCBTransactions into per-substance daily register entries.

Supports both entity types as prescribed by RCS Order 2013:
  Trader (Form D):       closing = opening + received - dispatched - handling_loss
  Manufacturer (Form C): closing = opening + received + produced - dispatched - consumed - handling_loss

Transaction type mapping:
  PURCHASE    → receipts     (both trader and manufacturer)
  SALE        → dispatches   (both)
  MANUFACTURE → productions  (manufacturer only)
  CONSUMPTION → consumptions (manufacturer only — substance used as raw material)

The module processes the full input period internally. Callers filter by month via
SubstanceRegister.entries_for_month().
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from agent.config_loader import ClientProfile
from agent.models import DailyRegisterEntry, NCBTransaction, SubstanceRegister


VALID_TXN_TYPES = frozenset({"PURCHASE", "SALE", "MANUFACTURE", "CONSUMPTION"})


def generate_registers(
    transactions: list[NCBTransaction],
    profile: ClientProfile,
    period_start: date,
    period_end: date,
) -> dict[str, SubstanceRegister]:
    """
    Returns a dict mapping canonical substance name → SubstanceRegister.
    Processes all substances listed in client_profile, even if no transactions exist.
    """
    # Group transactions by substance then by date
    by_substance: dict[str, dict[date, list[NCBTransaction]]] = defaultdict(lambda: defaultdict(list))
    for txn in transactions:
        if txn.substance in profile.substances:
            by_substance[txn.substance][txn.date].append(txn)

    working_days = _get_working_days(period_start, period_end, profile)

    registers: dict[str, SubstanceRegister] = {}

    for substance in profile.substances:
        opening_kg = profile.opening_stock_kg.get(substance, Decimal("0.00"))
        txn_by_date = by_substance.get(substance, {})

        entries = _build_entries(
            client_id=profile.client_id,
            substance=substance,
            working_days=working_days,
            txn_by_date=txn_by_date,
            opening_kg=opening_kg,
            is_manufacturer=profile.entity_type in ("manufacturer", "both"),
        )

        registers[substance] = SubstanceRegister(
            client_id=profile.client_id,
            substance=substance,
            period_start=period_start,
            period_end=period_end,
            opening_stock_kg=opening_kg,
            entries=entries,
        )

    return registers


def _build_entries(
    client_id: str,
    substance: str,
    working_days: list[date],
    txn_by_date: dict[date, list[NCBTransaction]],
    opening_kg: Decimal,
    is_manufacturer: bool = False,
) -> list[DailyRegisterEntry]:

    entries: list[DailyRegisterEntry] = []
    current_opening = opening_kg
    serial_no = 1

    for working_day in working_days:
        day_txns = txn_by_date.get(working_day, [])
        receipts     = [t for t in day_txns if t.txn_type == "PURCHASE"]
        productions  = [t for t in day_txns if t.txn_type == "MANUFACTURE"]
        dispatches   = [t for t in day_txns if t.txn_type == "SALE"]
        consumptions = [t for t in day_txns if t.txn_type == "CONSUMPTION"]

        total_received   = _sum_qty(receipts)
        total_produced   = _sum_qty(productions)
        total_dispatched = _sum_qty(dispatches)
        total_consumed   = _sum_qty(consumptions)
        handling_loss    = Decimal("0.00")   # zero until client specifies documented losses

        if is_manufacturer:
            closing = (current_opening + total_received + total_produced
                       - total_dispatched - total_consumed - handling_loss)
        else:
            closing = current_opening + total_received - total_dispatched - handling_loss

        review_reasons: list[str] = []

        if closing < Decimal("0.00"):
            review_reasons.append(
                f"Negative closing stock: {closing:.3f} kg. "
                f"Opening={current_opening}, Received={total_received}, "
                + (f"Produced={total_produced}, " if is_manufacturer else "")
                + f"Dispatched={total_dispatched}"
                + (f", Consumed={total_consumed}" if is_manufacturer else "")
                + ". Check for missing entries or data entry error."
            )

        for txn in day_txns:
            for flag in txn.anomaly_flags:
                review_reasons.append(f"Voucher {txn.voucher_no}: {flag}")

        entry = DailyRegisterEntry(
            client_id=client_id,
            substance=substance,
            date=working_day,
            serial_no=serial_no,
            opening_kg=current_opening,
            receipts=receipts,
            total_received_kg=total_received,
            productions=productions,
            total_produced_kg=total_produced,
            dispatches=dispatches,
            total_dispatched_kg=total_dispatched,
            consumptions=consumptions,
            total_consumed_kg=total_consumed,
            handling_loss_kg=handling_loss,
            closing_kg=closing,
            nil_transaction=not bool(day_txns),
            balance_discrepancy=Decimal("0.00"),
            requires_human_review=bool(review_reasons),
            review_reasons=review_reasons,
        )

        entries.append(entry)
        current_opening = closing
        serial_no += 1

    return entries


def _get_working_days(start: date, end: date, profile: ClientProfile) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if profile.working_days.is_working_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def _sum_qty(transactions: list[NCBTransaction]) -> Decimal:
    return sum((t.quantity_kg for t in transactions), Decimal("0.00"))
