"""Shared deterministic helpers for inventory lifecycle modules."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

INVENTORY_GL = "GL-INVENTORY-001"
COGS_GL = "GL-COGS-001"


def _is_policy_business_day(day: date) -> bool:
    return day.weekday() < 5


def _add_policy_business_days(day: date, count: int) -> date:
    """Apply the policy's explicitly fictional Monday-Friday calendar."""
    if not count:
        return day
    step = timedelta(days=1 if count > 0 else -1)
    current = day
    for _ in range(abs(count)):
        current += step
        while not _is_policy_business_day(current):
            current += step
    return current


def _next_policy_business_day(day: date) -> date:
    current = day
    while not _is_policy_business_day(current):
        current += timedelta(days=1)
    return current


def _next_policy_run(day: date, weekdays: tuple[int, ...]) -> date:
    current = day
    while current.weekday() not in weekdays:
        current += timedelta(days=1)
    return current


def _round_fraction_half_even(value: Fraction) -> int:
    """Round an exact rational to an integer without ambient Decimal state."""
    sign = -1 if value < 0 else 1
    numerator = abs(value.numerator)
    quotient, remainder = divmod(numerator, value.denominator)
    doubled = remainder * 2
    if doubled > value.denominator or (doubled == value.denominator and quotient % 2):
        quotient += 1
    return sign * quotient


def _scaled_decimal(value: int, places: int) -> Decimal:
    """Render an integer at a fixed decimal scale without context rounding."""
    sign = "-" if value < 0 else ""
    digits = str(abs(value)).zfill(places + 1)
    if not places:
        return Decimal(f"{sign}{digits}")
    return Decimal(f"{sign}{digits[:-places]}.{digits[-places:]}")


def _minor_units(value: Any, field: str) -> int:
    scaled = Fraction(str(value)) * 100
    if scaled.denominator != 1:
        raise ValueError(f"{field} is not an exact integer number of minor units")
    return scaled.numerator


def _unit_price_string(amount: Any, quantity: Any) -> str:
    exact_quantity = Fraction(str(quantity))
    if exact_quantity <= 0:
        raise ValueError("inventory procurement quantity must be positive")
    exact_amount = Fraction(str(amount))
    scaled = exact_amount / exact_quantity * 10_000
    rendered = _scaled_decimal(_round_fraction_half_even(scaled), 4)
    # Vendor unit prices are displayed to four decimals while document totals are stated
    # in cents. Reperform the ordinary extension at the document's cent precision; exact
    # rational equality is impossible whenever quantity does not divide a cent-
    # denominated total into a terminating decimal.
    extended_minor = _round_fraction_half_even(
        Fraction(str(rendered)) * exact_quantity * 100
    )
    if extended_minor != _minor_units(exact_amount, "inventory procurement total"):
        raise ValueError(
            "inventory procurement unit-price extension does not round to the "
            "admitted cent total at the reviewed zero-cent match tolerance"
        )
    return str(rendered)


def _procurement_order_lead(policy: Mapping[str, Any], ordinal: int) -> int:
    policy = policy["procurement"]["order_lead_business_days"]
    return int(policy["base"]) + (int(policy["ordinal_multiplier"]) * ordinal) % int(
        policy["modulus"]
    )


def _purchases(movements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in movements if row["movement_kind"] == "purchase_receipt"]


def _rni_movement(movement: dict[str, Any], year_end_iso: str) -> bool:
    """A received-not-invoiced purchase: the movement's own invoice date
    falls after fiscal close, so no in-year AP voucher exists."""
    invoice_date = str(movement.get("invoice_date") or "")
    return bool(invoice_date) and invoice_date > year_end_iso


def _sales(movements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inventory outflows for shipped goods and production consumption."""
    return [
        row
        for row in movements
        if row["movement_kind"] in {"sale_shipment", "kitchen_usage", "kitchen_waste"}
    ]
