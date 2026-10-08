import unittest
from decimal import Decimal

from app import checkout
from orders import OrderError, calculate_total
from payments import PaymentError, charge_card


class CheckoutTests(unittest.TestCase):
    def test_usd_checkout_succeeds(self):
        response = checkout({
            "order_id": "ord_usd_1001",
            "customer_id": "cus_ana",
            "currency": "USD",
            "items": [{"unit_price": "12.50", "quantity": 2}],
        })
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["payment"]["amount"], Decimal("25.00"))
        self.assertEqual(response["payment"]["currency"], "USD")

    def test_eur_checkout_succeeds(self):
        response = checkout({
            "order_id": "ord_eur_2001",
            "customer_id": "cus_martin",
            "currency": "EUR",
            "items": [{"unit_price": "19.99", "quantity": 1}],
        })
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["payment"]["amount"], Decimal("19.99"))
        self.assertEqual(response["payment"]["currency"], "EUR")

    def test_order_total_rounds_to_two_decimals(self):
        self.assertEqual(
            calculate_total([{"unit_price": "4.335", "quantity": 2}]),
            Decimal("8.67"),
        )

    def test_payment_rejects_invalid_inputs(self):
        with self.assertRaises(PaymentError):
            charge_card("ten", "USD")
        with self.assertRaises(PaymentError):
            charge_card(10, "GBP")
        with self.assertRaises(PaymentError):
            charge_card(0, "EUR")

    def test_order_rejects_empty_item_list(self):
        with self.assertRaises(OrderError):
            calculate_total([])


if __name__ == "__main__":
    unittest.main()
