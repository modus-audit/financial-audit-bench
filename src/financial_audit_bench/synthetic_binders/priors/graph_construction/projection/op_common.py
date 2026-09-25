"""Shared primitives and fixed-shape projection operations."""

from __future__ import annotations

from datetime import date
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


@op("render_count_responsibilities")
def render_count_responsibilities(
    world: World, node_id: str, field: str, value: Any
) -> list[dict[str, Any]]:
    return [
        {"count_team": row["count_team"], "supervisor": row["supervisor"]}
        for row in world["inventory_count"]
    ]


@op("select_staffing_revenue_tb_balance")
def select_staffing_revenue_tb_balance(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Return only TB balances for accounts used by staffing invoices."""
    revenue_accounts = {
        str(row["revenue_gl_account_id"])
        for row in world.get("customer_invoice") or []
        if row.get("revenue_gl_account_id")
    }
    balances = [
        row[field]
        for row in world.get(node_id) or []
        if str(row.get("gl_account_id")) in revenue_accounts
    ]
    if not balances:
        return None
    return balances[0] if len(balances) == 1 else balances


def year_end(world: World) -> date:
    return date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))


def near_year_end(world: World, row: dict[str, Any]) -> bool:
    document_date = date.fromisoformat(row["document_date"])
    return abs((document_date - year_end(world)).days) <= 31
