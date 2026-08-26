from decimal import ROUND_HALF_UP
import pytest

from money import decimal_to_cents, format_cents, validate_money_set


@pytest.mark.parametrize(("text","expected"),[("0",0),("0.01",1),("10.10",1010),("90071992547409.91",9007199254740991)])
def test_exact_decimal_cents(text,expected):
    assert decimal_to_cents(text)==expected
    assert format_cents(expected)==f"{expected // 100}.{expected % 100:02d}"


@pytest.mark.parametrize("value",["-1","1.001","1e2","NaN","inf"," 1.00",True,1.2])
def test_invalid_or_excessively_precise_money_fails(value):
    with pytest.raises((ValueError,OverflowError)): decimal_to_cents(value)


def test_explicit_rounding_rule_is_opt_in():
    assert decimal_to_cents("1.005",allow_rounding=True,rounding=ROUND_HALF_UP)==101


def test_integer_amount_and_currency_contract():
    assert validate_money_set("USD",subtotal=0,total=0)=={"subtotal":0,"total":0}
    with pytest.raises(ValueError): validate_money_set("USD",total=True)
    with pytest.raises(ValueError): validate_money_set("usd",total=1)
