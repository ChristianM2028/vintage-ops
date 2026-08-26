"""Exact money parsing and validation shared by storage and providers."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

MAX_CENTS = 9_223_372_036_854_775_807
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.([0-9]{1,2}))?$")


def decimal_to_cents(value, *, rounding=ROUND_HALF_UP, allow_rounding=False):
    """Convert a nonnegative decimal string to cents without binary floats.

    Stored legacy prices accept at most two fractional digits. Callers which
    deliberately accept additional precision must opt into the stated rule.
    """
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int)):
        raise ValueError("money must be a decimal string")
    text = str(value)
    if allow_rounding:
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal money") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("money must be finite and nonnegative")
        cents = int((amount * 100).quantize(Decimal("1"), rounding=rounding))
    else:
        if not _DECIMAL.fullmatch(text):
            raise ValueError("money must be a nonnegative decimal with at most two places")
        cents = int(Decimal(text) * 100)
    if cents > MAX_CENTS:
        raise OverflowError("money exceeds integer-cent storage")
    return cents


def validate_cents(value, name="amount"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > MAX_CENTS:
        raise ValueError(f"{name} must be a nonnegative integer number of cents")
    return value


def format_cents(value):
    cents = validate_cents(value)
    return f"{cents // 100}.{cents % 100:02d}"


def validate_money_set(currency, **amounts):
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be an uppercase ISO-style code")
    return {name: validate_cents(value, name) for name, value in amounts.items()}
