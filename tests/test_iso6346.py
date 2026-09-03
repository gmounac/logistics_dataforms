"""ISO 6346 container-number parsing and the check-digit algorithm."""

import pytest

from src.models import (
    is_valid_number_format,
    iso6346_check_digit,
    validate_container_number,
)

# Known-good numbers (check digit already correct).
VALID = ["MSKU3630518", "CSQU3054383", "HLXU1234561", "TCLU1234568"]


@pytest.mark.parametrize("number", VALID)
def test_validate_accepts_well_formed_numbers(number):
    assert validate_container_number(number) == number


def test_validate_uppercases_and_strips():
    assert validate_container_number("  csqu3054383  ") == "CSQU3054383"


@pytest.mark.parametrize(
    "number",
    [
        "MSK3630510",  # only 3 letters
        "MSKU363051",  # too short
        "MSKU36305100",  # too long
        "MSKA3630510",  # 4th letter not U/J/Z
        "1SKU3630510",  # leading digit
        "",
    ],
)
def test_validate_rejects_bad_format(number):
    with pytest.raises(ValueError, match="format"):
        validate_container_number(number)


def test_validate_rejects_bad_check_digit():
    # CSQU3054383 is valid; bump the check digit.
    with pytest.raises(ValueError, match="check digit"):
        validate_container_number("CSQU3054384")


def test_check_digit_is_zero_to_ten_mod_ten():
    # The algorithm folds a computed 10 back to 0.
    d = iso6346_check_digit("CSQU305438")
    assert 0 <= d <= 9


def test_is_valid_number_format_is_format_only():
    assert is_valid_number_format("CSQU3054384")  # format ok, check digit wrong
    assert not is_valid_number_format("nope")
