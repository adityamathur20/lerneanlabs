"""
URN (Unique Registration Number) validation for NCB Pre-Register portal.

Tier 1 validation only — regex format check.
Tier 2 (portal scrape cache) and Tier 3 (manual) are out of scope here.

URN format: NCB-{STATE_CODE}-{YEAR}-{6-DIGIT-SEQUENCE}
Example:    NCB-GJ-2021-004521
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

URN_PATTERN = re.compile(r'^NCB-[A-Z]{2}-\d{4}-\d{6}$')

# ISO 3166-2:IN state/UT codes used by NCB
VALID_STATE_CODES = {
    'AN', 'AP', 'AR', 'AS', 'BR', 'CH', 'CG', 'DD', 'DL', 'DN',
    'GA', 'GJ', 'HP', 'HR', 'JH', 'JK', 'KA', 'KL', 'LA', 'LD',
    'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'PB', 'PY', 'RJ',
    'SK', 'TG', 'TN', 'TR', 'UK', 'UP', 'WB',
}

REGISTRATION_YEAR_MIN = 1993  # NDPS Controlled Substances Order first issued
REGISTRATION_YEAR_MAX = 2030  # practical ceiling


class URNStatus(Enum):
    VALID = "VALID"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_STATE_CODE = "INVALID_STATE_CODE"
    INVALID_YEAR = "INVALID_YEAR"
    MISSING = "MISSING"


@dataclass
class URNValidationResult:
    urn: str
    status: URNStatus
    message: str
    requires_manual_verification: bool


def validate_urn(urn: Optional[str]) -> URNValidationResult:
    """
    Tier 1 URN validation: format, state code, and year plausibility.
    Always set requires_manual_verification=True for anything that is not VALID
    so the daily register module knows to flag it before accepting the transaction.
    """
    if not urn or not urn.strip():
        return URNValidationResult(
            urn=urn or "",
            status=URNStatus.MISSING,
            message="URN is missing. No Schedule A transaction can be recorded without a counterparty URN.",
            requires_manual_verification=True,
        )

    urn = urn.strip().upper()

    if not URN_PATTERN.match(urn):
        return URNValidationResult(
            urn=urn,
            status=URNStatus.INVALID_FORMAT,
            message=(
                f"'{urn}' does not match required format NCB-XX-YYYY-NNNNNN "
                f"(e.g. NCB-GJ-2021-004521). Flag for manual verification on precursorsncb.gov.in."
            ),
            requires_manual_verification=True,
        )

    parts = urn.split('-')
    state_code = parts[1]
    year = int(parts[2])

    if state_code not in VALID_STATE_CODES:
        return URNValidationResult(
            urn=urn,
            status=URNStatus.INVALID_STATE_CODE,
            message=(
                f"'{urn}' contains unrecognised state code '{state_code}'. "
                f"Valid codes: {', '.join(sorted(VALID_STATE_CODES))}."
            ),
            requires_manual_verification=True,
        )

    if not (REGISTRATION_YEAR_MIN <= year <= REGISTRATION_YEAR_MAX):
        return URNValidationResult(
            urn=urn,
            status=URNStatus.INVALID_YEAR,
            message=(
                f"'{urn}' has implausible registration year {year}. "
                f"Expected {REGISTRATION_YEAR_MIN}–{REGISTRATION_YEAR_MAX}."
            ),
            requires_manual_verification=True,
        )

    return URNValidationResult(
        urn=urn,
        status=URNStatus.VALID,
        message=(
            f"'{urn}' passes Tier 1 format validation. "
            f"Confirm active status on precursorsncb.gov.in before first transaction."
        ),
        requires_manual_verification=False,
    )


def is_valid(urn: Optional[str]) -> bool:
    """Convenience wrapper — returns True only for VALID status."""
    return validate_urn(urn).status == URNStatus.VALID
