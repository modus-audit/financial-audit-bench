"""Straight-line depreciation for the retained opening asset register."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "calculate_fixed_asset_depreciation",
    inputs=("fixed_asset", "fiscal_calendar"),
    outputs="fixed_asset_depreciation",
)
def calculate_fixed_asset_depreciation(
    fixed_assets: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, str]]:
    rows = []
    for asset in fixed_assets:
        cost = Decimal(str(asset["opening_cost"]))
        opening = Decimal(str(asset["opening_accumulated_depreciation"]))
        method = str(asset["depreciation_method"])
        if method == "none":
            current = Decimal("0.00")
            period = "0 days"
            convention = "none"
        else:
            life = Decimal(str(asset["useful_life_years"]))
            if life <= 0:
                raise ValueError(
                    f"depreciable asset {asset['fixed_asset_id']} has no useful life"
                )
            current = min(max(cost - opening, Decimal("0.00")), cost / life).quantize(
                Decimal("0.01")
            )
            period = "full fiscal year"
            convention = "straight_line"
        rows.append(
            {
                "beginning_accumulated_depreciation": str(opening),
                "book_layer": "client_book",
                "cost_basis": str(cost),
                "current_depreciation": str(current),
                "depreciable_period_months": period,
                "depreciation_convention": convention,
                "ending_accumulated_depreciation": str(opening + current),
                "ending_net_book_value": str(cost - opening - current),
                "fiscal_calendar_id": str(calendar["fiscal_calendar_id"]),
                "fixed_asset_id": str(asset["fixed_asset_id"]),
            }
        )
    return rows
