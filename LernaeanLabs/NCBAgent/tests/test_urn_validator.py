import pytest
from agent.urn_validator import validate_urn, is_valid, URNStatus


# --- VALID cases ---

def test_valid_gujarat_urn():
    r = validate_urn("NCB-GJ-2021-004521")
    assert r.status == URNStatus.VALID
    assert not r.requires_manual_verification

def test_valid_maharashtra_urn():
    r = validate_urn("NCB-MH-2019-012345")
    assert r.status == URNStatus.VALID

def test_valid_up_urn():
    r = validate_urn("NCB-UP-2013-021456")
    assert r.status == URNStatus.VALID

def test_valid_urn_normalises_lowercase():
    r = validate_urn("ncb-gj-2021-004521")
    assert r.status == URNStatus.VALID

def test_valid_urn_strips_whitespace():
    r = validate_urn("  NCB-GJ-2021-004521  ")
    assert r.status == URNStatus.VALID

def test_is_valid_convenience():
    assert is_valid("NCB-GJ-2021-004521") is True
    assert is_valid("INVALID") is False


# --- MISSING cases ---

def test_none_urn():
    r = validate_urn(None)
    assert r.status == URNStatus.MISSING
    assert r.requires_manual_verification

def test_empty_string_urn():
    r = validate_urn("")
    assert r.status == URNStatus.MISSING

def test_whitespace_only_urn():
    r = validate_urn("   ")
    assert r.status == URNStatus.MISSING


# --- INVALID FORMAT cases ---

def test_wrong_prefix():
    r = validate_urn("NCB/GJ/2021/004521")
    assert r.status == URNStatus.INVALID_FORMAT
    assert r.requires_manual_verification

def test_too_few_digits_in_sequence():
    r = validate_urn("NCB-GJ-2021-04521")   # 5 digits, needs 6
    assert r.status == URNStatus.INVALID_FORMAT

def test_too_many_digits_in_sequence():
    r = validate_urn("NCB-GJ-2021-0045210")  # 7 digits
    assert r.status == URNStatus.INVALID_FORMAT

def test_numeric_state_code():
    r = validate_urn("NCB-12-2021-004521")
    assert r.status == URNStatus.INVALID_FORMAT

def test_three_char_state_code():
    r = validate_urn("NCB-GJR-2021-004521")
    assert r.status == URNStatus.INVALID_FORMAT

def test_three_digit_year():
    r = validate_urn("NCB-GJ-202-004521")
    assert r.status == URNStatus.INVALID_FORMAT

def test_missing_segment():
    r = validate_urn("NCB-GJ-2021")
    assert r.status == URNStatus.INVALID_FORMAT

def test_extra_segment():
    r = validate_urn("NCB-GJ-2021-004521-EXTRA")
    assert r.status == URNStatus.INVALID_FORMAT

def test_arbitrary_string():
    r = validate_urn("SOME_RANDOM_ID_12345")
    assert r.status == URNStatus.INVALID_FORMAT


# --- INVALID STATE CODE cases ---

def test_invalid_state_code_xx():
    r = validate_urn("NCB-XX-2021-004521")
    assert r.status == URNStatus.INVALID_STATE_CODE
    assert r.requires_manual_verification

def test_invalid_state_code_zz():
    r = validate_urn("NCB-ZZ-2020-001234")
    assert r.status == URNStatus.INVALID_STATE_CODE


# --- INVALID YEAR cases ---

def test_year_before_1993():
    r = validate_urn("NCB-GJ-1990-004521")
    assert r.status == URNStatus.INVALID_YEAR
    assert r.requires_manual_verification

def test_year_far_future():
    r = validate_urn("NCB-GJ-2099-004521")
    assert r.status == URNStatus.INVALID_YEAR


# --- Message content checks ---

def test_missing_message_mentions_urn_requirement():
    r = validate_urn(None)
    assert "URN" in r.message

def test_invalid_format_message_shows_example():
    r = validate_urn("BADFORMAT")
    assert "NCB-GJ-2021-004521" in r.message or "NCB-XX-YYYY-NNNNNN" in r.message

def test_valid_message_mentions_portal():
    r = validate_urn("NCB-GJ-2021-004521")
    assert "precursorsncb.gov.in" in r.message
