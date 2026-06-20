from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

SCHEDULE_A_SUBSTANCES = frozenset({
    "Acetic Anhydride",
    "Ephedrine",
    "Pseudoephedrine",
    "N-Acetylanthranilic Acid",
    "Anthranilic Acid",
})


@dataclass
class NCBTransaction:
    """Canonical representation of one buy/sell transaction after parsing any input source."""
    client_id: str
    date: date
    voucher_no: str
    txn_type: str                       # "PURCHASE" | "SALE"
    substance: str                      # canonical Schedule A name
    item_code: str
    counterparty: str
    counterparty_urn: str
    quantity_kg: Decimal
    rate_inr_per_kg: Decimal
    amount_inr: Decimal
    form_g_no: str
    urn_status: str                     # URNStatus.value from urn_validator
    urn_requires_manual_verification: bool
    source: str                         # "excel" | "google_sheets" | "tally_xml"
    anomaly_flags: list[str] = field(default_factory=list)
    raw_substance: str = ""             # original value before alias resolution
    row_number: int = 0                 # source row/entry for error tracing

    @property
    def is_flagged(self) -> bool:
        return bool(self.anomaly_flags) or self.urn_requires_manual_verification


@dataclass
class DailyRegisterEntry:
    """
    One row in the daily register (per substance, per working day).
    Used for both Form C (manufacturer) and Form D (trader).

    Trader balance:   closing = opening + received - dispatched - handling_loss
    Manufacturer:     closing = opening + received + produced - dispatched - consumed - handling_loss
    """
    client_id: str
    substance: str
    date: date
    serial_no: int                      # running serial number, resets annually
    opening_kg: Decimal
    receipts: list[NCBTransaction] = field(default_factory=list)
    total_received_kg: Decimal = Decimal("0.00")
    productions: list[NCBTransaction] = field(default_factory=list)   # MANUFACTURE txns (Form C only)
    total_produced_kg: Decimal = Decimal("0.00")
    dispatches: list[NCBTransaction] = field(default_factory=list)
    total_dispatched_kg: Decimal = Decimal("0.00")
    consumptions: list[NCBTransaction] = field(default_factory=list)  # CONSUMPTION txns (Form C only)
    total_consumed_kg: Decimal = Decimal("0.00")
    handling_loss_kg: Decimal = Decimal("0.00")
    closing_kg: Decimal = Decimal("0.00")
    nil_transaction: bool = False
    balance_discrepancy: Decimal = Decimal("0.00")
    requires_human_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    @property
    def all_transactions(self) -> list[NCBTransaction]:
        return self.receipts + self.productions + self.dispatches + self.consumptions


@dataclass
class SubstanceRegister:
    """Full daily register for one substance over a date range."""
    client_id: str
    substance: str
    period_start: date
    period_end: date
    opening_stock_kg: Decimal
    entries: list[DailyRegisterEntry] = field(default_factory=list)

    @property
    def closing_stock_kg(self) -> Decimal:
        return self.entries[-1].closing_kg if self.entries else self.opening_stock_kg

    @property
    def total_received_kg(self) -> Decimal:
        return sum((e.total_received_kg for e in self.entries), Decimal("0.00"))

    @property
    def total_produced_kg(self) -> Decimal:
        return sum((e.total_produced_kg for e in self.entries), Decimal("0.00"))

    @property
    def total_dispatched_kg(self) -> Decimal:
        return sum((e.total_dispatched_kg for e in self.entries), Decimal("0.00"))

    @property
    def total_consumed_kg(self) -> Decimal:
        return sum((e.total_consumed_kg for e in self.entries), Decimal("0.00"))

    @property
    def total_handling_loss_kg(self) -> Decimal:
        return sum((e.handling_loss_kg for e in self.entries), Decimal("0.00"))

    @property
    def flagged_entries(self) -> list[DailyRegisterEntry]:
        return [e for e in self.entries if e.requires_human_review]

    def entries_for_month(self, year: int, month: int) -> list[DailyRegisterEntry]:
        return [e for e in self.entries if e.date.year == year and e.date.month == month]


@dataclass
class ParseError:
    source_file: str
    row_number: int
    field: str
    raw_value: str
    reason: str


@dataclass
class ParseResult:
    transactions: list[NCBTransaction]
    errors: list[ParseError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)
