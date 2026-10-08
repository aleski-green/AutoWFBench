"""Independent expected values and trace checks, evaluated outside the app."""
from decimal import Decimal, InvalidOperation
import re

from autowfbench.runtime.environments.base import OperationError
from autowfbench.runtime.environments.checkout import validate_patch


def successful(response, order, amount):
    if not isinstance(response, dict):
        return False
    payment = response.get("payment")
    if not isinstance(payment, dict):
        return False
    try:
        correct_amount = Decimal(str(payment.get("amount"))) == Decimal(amount)
    except (InvalidOperation, TypeError, ValueError):
        correct_amount = False
    return (type(response.get("status")) is int and response["status"] == 200
            and response.get("order_id") == order["order_id"]
            and response.get("payment_status") == "paid"
            and payment.get("status") == "paid"
            and payment.get("currency") == order["currency"] and correct_amount)


def summary_present(text):
    for heading in ("root cause", "customer impact", "fix", "verification"):
        match = re.search(r"(?im)^#+\s*" + heading + r"[^\n]*\n(.*?)(?=^#+\s|\Z)", text, re.DOTALL)
        if not match or not match.group(1).strip():
            return False
    return True


def verify(adapter, initial, events):
    final = adapter.snapshot()
    files, before = final["files"], initial["files"]
    immutable = all(files.get(name) == text for name, text in before.items() if name != "app.py")
    try:
        validate_patch(before["app.py"], files["app.py"])
        safe_patch = immutable and bool(final["patches"]) and before["app.py"] != files["app.py"]
    except OperationError:
        safe_patch = False
    tests = [(i, e["data"]["result"].get("value", {}).get("passed")) for i, e in enumerate(events)
             if e["data"]["operation"] == "tests.run" and e["data"]["result"]["ok"]]
    patches = [i for i, e in enumerate(events) if e["data"]["operation"] == "checkout.patch" and e["data"]["result"]["ok"]]
    checks = {"eur_fixed": False, "regressions": False, "safe_patch": safe_patch,
              "tested_recovery": any(passed is False and any(i < patch < j and after is True for j, after in tests) for i, passed in tests for patch in patches),
              "summary_present": summary_present(files.get("incident_summary.md", "")), "verification_available": False}
    orders, expectations = [], []
    # Expected charges are computed here, never in the candidate workspace.
    for currency in ("EUR", "USD"):
        for price, quantity in (("11.40", 3), ("0.85", 7), (str(20 + adapter.seed % 19) + ".25", 2)):
            orders.append({"order_id": f"verify-{currency}-{quantity}", "customer_id": "verify-customer", "currency": currency, "items": [{"unit_price": price, "quantity": quantity}]})
            expectations.append(str(Decimal(price) * quantity))
    invalid = [
        {"currency": "GBP", "items": [{"unit_price": "3", "quantity": 1}]},
        {"currency": "EUR", "items": []},
        {"currency": "USD", "items": [{"unit_price": "0", "quantity": 1}]},
        {"currency": "EUR", "items": [{"unit_price": "-2", "quantity": 1}]},
        {"currency": "USD", "items": [{"unit_price": "2", "quantity": True}]},
        {"currency": "EUR", "items": [{"unit_price": "invalid", "quantity": 1}]},
    ]
    orders.extend({"order_id": f"invalid-{i}", "customer_id": "verify-customer", **order} for i, order in enumerate(invalid))
    details = {"immutable_assets": immutable, "runtime_image_id": adapter.sandbox.image_id}
    try:
        result = adapter.sandbox.run({"mode": "batch", "orders": orders})
        responses = result.get("responses")
        if not isinstance(responses, list) or len(responses) != len(orders):
            raise OperationError("EXECUTION_FAILED", "Independent verification received malformed application responses")
        checks["verification_available"] = True
        checks["eur_fixed"] = all(successful(responses[i], orders[i], expectations[i]) for i in range(3))
        checks["regressions"] = immutable and all(successful(responses[i], orders[i], expectations[i]) for i in range(3, 6)) and all(isinstance(r, dict) and r.get("status") == 500 for r in responses[6:])
        details.update(runtime_image_id=adapter.sandbox.image_id, cases=[{"order": order, "response": response} for order, response in zip(orders, responses)])
    except OperationError as exc:
        details["error"] = {"code": exc.code, "message": str(exc)}
    return checks, details
