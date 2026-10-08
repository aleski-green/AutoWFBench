"""Payment gateway boundary used by checkout."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4


SUPPORTED_CARD_CURRENCIES = {"USD", "EUR"}


class PaymentError(ValueError):
    """Raised when a card charge request is invalid."""


def charge_card(amount, currency):
    """Create a card charge for a positive monetary amount."""
    if isinstance(amount, bool):
        raise PaymentError("amount must be numeric")
    try:
        normalized_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentError("amount must be numeric") from exc
    if not normalized_amount.is_finite() or normalized_amount <= 0:
        raise PaymentError("amount must be positive")
    if not isinstance(currency, str) or currency not in SUPPORTED_CARD_CURRENCIES:
        raise PaymentError("unsupported card currency")
    return {
        "payment_id": "pay_" + uuid4().hex[:12],
        "status": "paid",
        "amount": normalized_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "currency": currency,
    }
