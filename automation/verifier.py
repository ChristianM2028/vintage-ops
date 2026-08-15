from decimal import Decimal, InvalidOperation
from .models import VerificationResult
from .resolver import normalize_label

def verify_value(expected, observed):
    ok=normalize_label(str(expected)) == normalize_label(str(observed))
    return VerificationResult(ok,expected,observed,None if ok else "normalized values differ")

def verify_number(expected, observed):
    try: ok=Decimal(str(expected)) == Decimal(str(observed))
    except InvalidOperation: ok=False
    return VerificationResult(ok,expected,observed,None if ok else "numeric values differ")
