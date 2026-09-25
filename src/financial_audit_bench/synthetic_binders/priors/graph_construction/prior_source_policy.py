"""Capitalization policies and operating-scale ratios."""

from __future__ import annotations

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)


def _sample_capitalization_policy(self, world: World) -> list[dict[str, str]]:
    """Create one immutable-policy row for every generated asset class."""
    policy = load_authored_policy("authored.global.operating-policy.v1").values[
        "capitalization"
    ]
    assets = world.get("fixed_asset") or []
    calendar = world["fiscal_calendar"]
    company = world["company_context"]
    rows: list[dict[str, str]] = []
    for asset_class in sorted({str(row["asset_class"]) for row in assets}):
        class_assets = [row for row in assets if str(row["asset_class"]) == asset_class]
        life = max(int(str(row["useful_life_years"])) for row in class_assets)
        methods = {str(row["depreciation_method"]) for row in class_assets}
        if len(methods) != 1:
            raise ValueError(
                f"fixed-asset class {asset_class} has mixed depreciation methods"
            )
        rows.append(
            {
                "capitalization_policy_id": (
                    "CAP-POLICY-" + asset_class.replace("_", "-").upper()
                ),
                "company_id": str(company["company_id"]),
                "effective_date": str(calendar["start_date"]),
                "threshold_amount": str(policy["threshold_amount"]),
                "currency_code": str(company["currency_code"]),
                "asset_class": asset_class,
                "depreciation_method": methods.pop(),
                "useful_life_years": str(life),
                "depreciation_convention": str(policy["depreciation_convention"]),
                "repairs_policy": str(policy["repairs_policy"]),
            }
        )
    return rows


def _sample_operating_scale(self, world: World) -> dict[str, str]:
    """Draw in-segment operating-scale ratios; empty sentinel when none."""
    drawn = self._draw_ratio_dict(
        "operating_scale",
        empty="",
        segment_only=("inventory_to_cogs",),
    )
    # Cost of goods sold is determined by the generated subledgers.
    drawn["cogs_to_revenue"] = ""
    return drawn


def _sample_bank_reconciliation_scale(self, world: World) -> dict[str, str]:
    """Draw the retained in-segment outstanding-check ratio."""
    return self._draw_ratio_dict("bank_reconciliation_scale", empty="")
