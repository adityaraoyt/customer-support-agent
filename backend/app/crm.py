from __future__ import annotations

from typing import Any


def find_customer(customers: list[dict[str, Any]], extraction: dict[str, str | None]) -> dict[str, Any] | None:
    name = extraction.get("customer_name")
    email = extraction.get("email")
    for customer in customers:
        if email and customer["email"].lower() == email.lower():
            return customer
        if name and customer["name"].lower() == name.lower():
            return customer
    return None


def find_order(customer: dict[str, Any], extraction: dict[str, str | None]) -> dict[str, Any] | None:
    order_id = extraction.get("order_id")
    for order in customer["orders"]:
        if order["order_id"].lower() == (order_id or "").lower():
            return order
    return None
