"""The VIN check digit: known VINs, a wrong digit, a wrong length, forbidden letters and lower case."""

import pytest
from helpers import with_check_digit

from mcp_quoting.vin import check_digit, is_valid

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("vin", ["1M8GDM9AXKP042788", "11111111111111111", "2T3W1RFV7MC123456"])
def test_known_vins_pass_the_check_digit(vin):
    assert is_valid(vin)


def test_a_wrong_check_digit_fails():
    vin = "1M8GDM9AXKP042788"
    assert not is_valid(vin[:8] + "0" + vin[9:])
    assert check_digit(vin) == "X"


@pytest.mark.parametrize(
    "vin", ["", "2T3W1RFV7MC12345", "2T3W1RFV7MC1234567", "2T3W1RFV7MCI23456", "2t3w1rfv7mc123456"]
)
def test_wrong_length_forbidden_letters_and_lowercase_are_invalid(vin):
    assert not is_valid(vin)


def test_with_check_digit_fixes_the_ninth_character():
    fixed = with_check_digit("2T3W1RFV0MC123456")
    assert fixed == "2T3W1RFV7MC123456" and is_valid(fixed)
