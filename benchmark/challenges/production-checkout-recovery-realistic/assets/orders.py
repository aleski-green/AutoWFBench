"""Order construction and validation helpers for checkout."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4


SUPPORTED_CURRENCIES = {"USD", "EUR"}


class OrderError(ValueError):
    """Raised when an order cannot be processed."""


def calculate_total(items):
    """Return the two-decimal total for a sequence of order line items."""
    if not isinstance(items, list) or not items:
        raise OrderError("an order must include at least one item")

    total = Decimal("0")
    for item in items:
        if not isinstance(item, dict) or "unit_price" not in item or "quantity" not in item:
            raise OrderError("each item requires unit_price and quantity")
        try:
            price = Decimal(str(item["unit_price"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise OrderError("item price must be numeric") from exc
        quantity = item["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise OrderError("item quantity must be a positive integer")
        if price <= 0:
            raise OrderError("item price must be positive")
        total += price * quantity
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def create_order(customer_id, items, currency, order_id=None):
    """Validate order inputs and create a checkout-ready order dictionary."""
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise OrderError("customer_id is required")
    if currency not in SUPPORTED_CURRENCIES:
        raise OrderError("unsupported order currency")
    return {
        "order_id": order_id or "ord_" + uuid4().hex[:12],
        "customer_id": customer_id,
        "items": items,
        "currency": currency,
        "total": calculate_total(items),
    }
