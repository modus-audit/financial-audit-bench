"""Receivables aging, credit, and sales-register projection operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


@op("credit_memo_customer_name")
def credit_memo_customer_name(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    names = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer") or []
    }
    return [
        names.get(row["customer_id"], row["customer_id"])
        for row in world.get("customer_credit_adjustment") or []
    ] or None


@op("credit_memo_post_year_end")
def credit_memo_post_year_end(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    year_end = str(world["fiscal_calendar"]["end_date"])
    return [
        "Yes" if str(row["posting_date"]) > year_end else "No"
        for row in world.get("customer_credit_adjustment") or []
    ] or None


@op("render_customer_invoice_reference")
def render_customer_invoice_reference(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """The invoice number per invoice LINE, joined into the line-level register: AR-03's table is one row per invoice line, so every column must align to lines, not stack per-node blocks."""
    numbers = {
        row["customer_invoice_id"]: row["invoice_number"]
        for row in world.get("customer_invoice") or []
    }
    return [
        numbers.get(row["customer_invoice_id"], "")
        for row in world.get("customer_invoice_line") or []
    ] or None


@op("join_invoice_line_customer")
def join_invoice_line_customer(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Customer name per invoice line (the AR-03 line-register join)."""
    customers = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer") or []
    }
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    return [
        customers.get(
            invoices.get(row["customer_invoice_id"], {}).get("customer_id"), ""
        )
        for row in world.get("customer_invoice_line") or []
    ] or None


@op("join_invoice_line_date")
def join_invoice_line_date(world: World, node_id: str, field: str, value: Any) -> Any:
    """Invoice date per invoice line: the cutoff
    column every real sales register carries."""
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    return [
        invoices.get(row["customer_invoice_id"], {}).get("invoice_date", "")
        for row in world.get("customer_invoice_line") or []
    ] or None


@op("goods_line_column")
def goods_line_column(world: World, node_id: str, field: str, value: Any) -> Any:
    """Add aligned, presentation-ready goods-only register columns."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
        sells_goods,
    )

    if not sells_goods(world.get("company_feature_profile") or {}):
        return None

    # Re-read the rows instead of trusting ``source_value``. It intentionally
    # omits nulls, which would shift optional columns onto the wrong invoices.
    def present(raw: Any) -> Any:
        if raw is None:
            return "—"
        if field == "sales_tax_status":
            return str(raw).replace("_", " ").title()
        if field == "sales_tax_rate":
            return f"{Decimal(str(raw)):.2%}"
        return raw

    return [present(row.get(field)) for row in world.get(node_id) or []] or None


@op("discount_or_adjustment_column")
def discount_or_adjustment_column(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Return the discounts column only for the manufacturing goods book."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
        sells_goods,
    )

    return value if sells_goods(world.get("company_feature_profile") or {}) else None


@op("register_quantity_column")
def register_quantity_column(world: World, node_id: str, field: str, value: Any) -> Any:
    """Return the retained invoice-line quantity column."""
    return value


@op("register_price_column")
def register_price_column(world: World, node_id: str, field: str, value: Any) -> Any:
    """Return the retained invoice-line price column."""
    return value


@op("join_invoice_line_terms")
def join_invoice_line_terms(world: World, node_id: str, field: str, value: Any) -> Any:
    """Return customer payment terms for every retained invoice line."""
    customers = {
        row["customer_id"]: row["payment_terms"] for row in world.get("customer") or []
    }
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    values = [
        customers.get(
            invoices.get(row["customer_invoice_id"], {}).get("customer_id"),
            "",
        )
        for row in world.get("customer_invoice_line") or []
    ]
    return values if any(values) else None
