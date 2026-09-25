"""Late-position receivables subsequent-cash projection operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


def _subsequent_receipts(world: World) -> list[dict[str, Any]]:
    """Customer receipts dated after the fiscal year end — the January
    vouching population the AR-05 title promises."""
    end = str(world["fiscal_calendar"]["end_date"])
    return [
        row
        for row in world.get("customer_cash_receipt") or []
        if row["receipt_date"] > end and Decimal(str(row["amount"])) > 0
    ]


@op("subsequent_receipt_column")
def subsequent_receipt_column(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """A customer_cash_receipt column restricted to POST-year-end receipts: the subsequent-cash-receipts schedule lists what collected in January, never the whole in-year register relabeled."""
    return [row[field] for row in _subsequent_receipts(world)] or None


@op("subsequent_receipt_zero_column")
def subsequent_receipt_zero_column(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Discount/deduction/unapplied columns render only when some row is
    nonzero: clients export the columns they use, so an all-0.00 column is
    dropped instead of printed."""
    values = [row[field] for row in _subsequent_receipts(world)]
    if not values or all(Decimal(str(item)) == 0 for item in values):
        return None
    return values


@op("subsequent_receipt_invoice")
def subsequent_receipt_invoice(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """The customer invoice number(s) each subsequent receipt paid off, joined per receipt so the schedule renders as one register rather than separate position-joined receipt and application blocks."""
    numbers = {
        row["customer_invoice_id"]: row.get("invoice_number")
        or row["customer_invoice_id"]
        for row in world.get("customer_invoice") or []
    }
    applied: dict[str, list[str]] = {}
    for row in world.get("ar_receipt_application") or []:
        applied.setdefault(row["customer_cash_receipt_id"], []).append(
            str(numbers.get(row["customer_invoice_id"], row["customer_invoice_id"]))
        )
    return [
        ", ".join(applied.get(row["customer_cash_receipt_id"], [])) or "-"
        for row in _subsequent_receipts(world)
    ] or None
