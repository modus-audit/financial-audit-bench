"""Fixed-asset register, class-summary, and reconciliation projections."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


@op("render_method_and_life")
def render_method_and_life(world: World, node_id: str, field: str, value: Any) -> Any:
    tokens = {"straight_line": "S/L", "none": "Not depreciated"}
    return [
        f"{tokens.get(row['depreciation_method'], row['depreciation_method'])}"
        + (
            f" over {row['useful_life_years']} years"
            if row["depreciation_method"] != "none"
            else ""
        )
        for row in world["capitalization_policy"]
    ]


@op("fixed_asset_register_detail")
def fixed_asset_register_detail(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Present each retained opening asset with its current depreciation."""
    del node_id, value
    assets = world.get("fixed_asset") or []
    depreciation = {
        row["fixed_asset_id"]: row
        for row in world.get("fixed_asset_depreciation") or []
    }
    rows = []
    for asset in assets:
        cost = Decimal(asset["opening_cost"])
        accumulated = Decimal(asset["opening_accumulated_depreciation"])
        current = Decimal(depreciation[asset["fixed_asset_id"]]["current_depreciation"])
        rows.append(
            {
                **asset,
                "register_cost": cost,
                "register_depreciation": current,
                "register_nbv": cost - accumulated - current,
            }
        )
    key = {
        "cost_basis": "register_cost",
        "current_depreciation": "register_depreciation",
        "ending_net_book_value": "register_nbv",
    }.get(field, field)
    if field == "useful_life_support_reference":
        from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
            fixed_asset_support_display,
        )

        return [fixed_asset_support_display(row[key]) for row in rows]
    return [row[key] for row in rows]


@op("render_fixed_asset_differences")
def render_fixed_asset_differences(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    # The client's rec shows where the difference sits, not why: naming the cause
    # ("current-year depreciation not yet recorded") would hand the agent the planted
    # finding pre-diagnosed. A labeled under-review row still satisfies the no-bare-
    # number rule.
    return [
        {
            "reconciling_item": (
                "Register cost and accumulated vs general ledger - under review"
            ),
            "amount": row["difference"],
        }
        for row in world["fixed_asset_gl_reconciliation"]
        if Decimal(row["difference"])
    ] or None
