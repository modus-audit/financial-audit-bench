"""Shared accounting-identity assertions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable


def dsum(rows: Iterable[dict[str, Any]], field: str) -> Decimal:
    """Sum a Decimal-encoded field over rows (missing/None counts as zero)."""
    return sum((Decimal(row[field] or "0") for row in rows), Decimal("0.00"))


def cash_gl_account_id(accounts: Iterable[dict[str, Any]]) -> str:
    """Resolve the chart's single operating-cash account."""
    cash_ids = [str(row["gl_account_id"]) for row in accounts if row["is_cash_account"]]
    if len(cash_ids) != 1:
        raise ValueError("chart must contain exactly one operating-cash account")
    return cash_ids[0]


def statement_ties(opening: Any, activity_total: Any, ending: Any) -> bool:
    """Return whether opening + activity_total == ending."""
    return _decimal(opening) + _decimal(activity_total) == _decimal(ending)


def chain_is_continuous(pairs: Iterable[tuple[Any, Any]]) -> bool:
    """Return whether every consecutive (ending, next_beginning) pair ties."""
    return all(
        _decimal(ending) == _decimal(next_beginning) for ending, next_beginning in pairs
    )


def _decimal(value: Any) -> Decimal:
    """Normalize a Decimal-convertible value."""
    return value if isinstance(value, Decimal) else Decimal(str(value))
