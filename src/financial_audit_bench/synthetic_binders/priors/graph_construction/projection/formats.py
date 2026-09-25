"""Display conventions for client-facing document surfaces."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def fmt_date(iso: str) -> str:
    """ISO date -> MM/DD/YYYY."""
    when = date.fromisoformat(str(iso)[:10])
    return f"{when.month:02d}/{when.day:02d}/{when.year}"


def fmt_amount(value) -> str:
    """Comma-grouped amount with cents, sign preserved: -1234.5 -> -1,234.50."""
    return f"{Decimal(str(value)):,.2f}"


def fmt_money(value) -> str:
    """Dollar amount, parenthesized when negative: -1234.5 -> $(1,234.50)."""
    amount = Decimal(str(value))
    if amount < 0:
        return f"$({-amount:,.2f})"
    return f"${amount:,.2f}"
