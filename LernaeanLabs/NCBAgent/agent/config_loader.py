from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

WEEKDAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


@dataclass
class WorkingDaysConfig:
    days: list[int]                 # weekday ints (0=Mon … 6=Sun)
    national_holidays: list[date]

    def is_working_day(self, d: date) -> bool:
        return d.weekday() in self.days and d not in self.national_holidays


@dataclass
class ClientProfile:
    client_id: str
    client_name: str
    entity_type: str                # "trader" | "manufacturer" | "both"
    urn: str
    gstin: str
    address: str
    zonal_unit: str
    compliance_officer_name: str
    compliance_officer_phone: str
    tier: str
    substances: list[str]
    opening_stock_kg: dict[str, Decimal]
    opening_stock_as_of: date
    working_days: WorkingDaysConfig
    input_method: str


@dataclass
class SheetConfig:
    sheet_name: str
    txn_type_mode: str              # "fixed" | "column"
    txn_type_fixed: Optional[str]
    txn_type_column: Optional[str]
    txn_type_map: dict[str, str]
    header_row: int
    data_starts_row: int
    skip_rows: list[int]
    columns: dict[str, str]         # canonical_field → actual_header_name


@dataclass
class ExcelConfig:
    client_id: str
    input_type: str                 # "excel" | "google_sheets"
    google_sheet_id: Optional[str]
    service_account_key_path: Optional[str]
    sheets: list[SheetConfig]
    date_format: str
    substance_alias_map: dict[str, str]  # lower-cased keys for case-insensitive lookup
    substance_extraction_enabled: bool
    substance_extraction_source_column: Optional[str]
    substance_extraction_pattern: Optional[str]
    qty_unit_normalisation_enabled: bool
    qty_unit_column: Optional[str]
    qty_unit_map: dict[str, Decimal]

    def resolve_substance(self, raw: str) -> Optional[str]:
        return self.substance_alias_map.get(raw.strip().lower())


@dataclass
class TallyConfig:
    client_id: str
    stock_item_alias_map: dict[str, str]        # lower-cased keys
    urn_udf_field: str
    form_g_udf_field: str
    quantity_unit: str
    # Fallback: company name (lower-cased) → NCB URN when UDF is not set in Tally
    counterparty_urn_lookup: dict[str, str] = field(default_factory=dict)
    # Voucher type names treated as manufacturing journal (case-insensitive)
    manufacturing_voucher_types: list[str] = field(default_factory=lambda: [
        "Manufacturing Journal", "Stock Journal", "Production"
    ])

    def resolve_substance(self, raw: str) -> Optional[str]:
        return self.stock_item_alias_map.get(raw.strip().lower())

    def resolve_counterparty_urn(self, name: str, udf_value: str) -> str:
        """Return URN from UDF if present, else lookup table, else empty string."""
        if udf_value and udf_value.strip():
            return udf_value.strip()
        return self.counterparty_urn_lookup.get(name.strip().lower(), "")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _clients_dir(base: Path, client_id: str) -> Path:
    p = base / client_id
    if not p.is_dir():
        raise FileNotFoundError(f"No client directory found at {p}. Create clients/{client_id}/")
    return p


def load_client_profile(client_id: str, clients_base: Path = Path("clients")) -> ClientProfile:
    path = _clients_dir(clients_base, client_id) / "client_profile.yaml"
    if not path.exists():
        raise FileNotFoundError(f"client_profile.yaml not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    wd_raw = raw.get("working_days", {})
    day_names = wd_raw.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
    holidays_raw = wd_raw.get("national_holidays", [])

    working_days = WorkingDaysConfig(
        days=[WEEKDAY_MAP[d] for d in day_names],
        national_holidays=[
            date.fromisoformat(str(h)) for h in holidays_raw if h
        ],
    )

    opening_raw = raw.get("opening_stock_kg", {})
    opening_stock = {k: Decimal(str(v)) for k, v in opening_raw.items()}

    return ClientProfile(
        client_id=raw["client_id"],
        client_name=raw["client_name"],
        entity_type=raw.get("entity_type", "trader"),
        urn=raw["urn"],
        gstin=raw.get("gstin", ""),
        address=raw.get("address", ""),
        zonal_unit=raw.get("zonal_unit", ""),
        compliance_officer_name=raw.get("compliance_officer_name", ""),
        compliance_officer_phone=raw.get("compliance_officer_phone", ""),
        tier=raw.get("tier", "standard"),
        substances=raw.get("substances", []),
        opening_stock_kg=opening_stock,
        opening_stock_as_of=date.fromisoformat(str(raw["opening_stock_as_of"])),
        working_days=working_days,
        input_method=raw.get("input_method", "excel"),
    )


def load_excel_config(client_id: str, clients_base: Path = Path("clients")) -> ExcelConfig:
    path = _clients_dir(clients_base, client_id) / "excel_config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"excel_config.yaml not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    sheets = []
    for s in raw.get("sheets", []):
        sheets.append(SheetConfig(
            sheet_name=s["sheet_name"],
            txn_type_mode=s.get("txn_type_mode", "column"),
            txn_type_fixed=s.get("txn_type_fixed"),
            txn_type_column=s.get("txn_type_column"),
            txn_type_map={str(k): str(v) for k, v in s.get("txn_type_map", {}).items()},
            header_row=s.get("header_row", 1),
            data_starts_row=s.get("data_starts_row", 2),
            skip_rows=[int(r) for r in s.get("skip_rows", [])],
            columns=s.get("columns", {}),
        ))

    # Lowercase all alias map keys for case-insensitive lookup
    raw_alias = raw.get("substance_alias_map", {})
    alias_map = {k.strip().lower(): v for k, v in raw_alias.items()}

    se = raw.get("substance_extraction", {})
    qu = raw.get("qty_unit_normalisation", {})
    unit_map = {k: Decimal(str(v)) for k, v in qu.get("unit_map", {}).items()}

    inp = raw.get("input", {})

    return ExcelConfig(
        client_id=raw["client_id"],
        input_type=inp.get("type", "excel"),
        google_sheet_id=inp.get("google_sheet_id"),
        service_account_key_path=inp.get("service_account_key_path"),
        sheets=sheets,
        date_format=raw.get("date_format", "%d/%m/%Y"),
        substance_alias_map=alias_map,
        substance_extraction_enabled=bool(se.get("enabled", False)),
        substance_extraction_source_column=se.get("source_column"),
        substance_extraction_pattern=se.get("pattern"),
        qty_unit_normalisation_enabled=bool(qu.get("enabled", False)),
        qty_unit_column=qu.get("unit_column"),
        qty_unit_map=unit_map,
    )


def load_tally_config(client_id: str, clients_base: Path = Path("clients")) -> TallyConfig:
    path = _clients_dir(clients_base, client_id) / "tally_config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"tally_config.yaml not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    raw_alias = raw.get("stock_item_alias_map", {})
    alias_map = {k.strip().lower(): v for k, v in raw_alias.items()}

    raw_urn_lookup = raw.get("counterparty_urn_lookup", {})
    urn_lookup = {k.strip().lower(): v for k, v in raw_urn_lookup.items()}

    mfg_types = raw.get("manufacturing_voucher_types",
                         ["Manufacturing Journal", "Stock Journal", "Production"])

    return TallyConfig(
        client_id=raw["client_id"],
        stock_item_alias_map=alias_map,
        urn_udf_field=raw.get("urn_udf_field", "COUNTERPARTYURN"),
        form_g_udf_field=raw.get("form_g_udf_field", "FORMGNO"),
        quantity_unit=raw.get("quantity_unit", "Kg"),
        counterparty_urn_lookup=urn_lookup,
        manufacturing_voucher_types=mfg_types,
    )
