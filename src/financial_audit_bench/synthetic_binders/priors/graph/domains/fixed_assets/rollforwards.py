"""Fixed-asset and amortizable-asset rollforwards and GL reconciliation."""

from __future__ import annotations

from decimal import Decimal

from financial_audit_bench.synthetic_binders.priors.graph.domains.fixed_assets import (
    policy as _policy,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.identities import (
    dsum,
)


@REGISTRY.rule(
    "roll_forward_fixed_assets",
    inputs=(
        "fixed_asset",
        "fixed_asset_depreciation",
    ),
    outputs="fixed_asset_rollforward",
)
def roll_forward_fixed_assets(
    fixed_assets: list[dict[str, str | None]],
    depreciation: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Summarize the retained opening register by asset class."""
    if not fixed_assets:
        return []
    depreciation_by_asset = {row["fixed_asset_id"]: row for row in depreciation}
    assets_by_class: dict[str, list[dict[str, str | None]]] = {}
    for asset in fixed_assets:
        assets_by_class.setdefault(str(asset["asset_class"]), []).append(asset)

    rows = []
    company_id = str(fixed_assets[0]["company_id"])
    fiscal_calendar_id = depreciation[0]["fiscal_calendar_id"]
    for asset_class, class_assets in sorted(assets_by_class.items()):
        class_depreciation = [
            depreciation_by_asset[str(asset["fixed_asset_id"])]
            for asset in class_assets
        ]
        beginning_cost = sum(
            (Decimal(str(asset["opening_cost"])) for asset in class_assets),
            Decimal("0.00"),
        )
        beginning_depreciation = dsum(
            class_depreciation, "beginning_accumulated_depreciation"
        )
        current_depreciation = dsum(class_depreciation, "current_depreciation")
        representative = class_assets[0]
        rows.append(
            {
                # There is no current-year acquisition or disposal producer. Keep the
                # zero columns because FA-02 is still a familiar client rollforward,
                # without carrying an event-processing branch.
                "acquisitions": "0.00",
                "asset_class": asset_class,
                "beginning_accumulated_depreciation": str(beginning_depreciation),
                "beginning_cost": str(beginning_cost),
                "company_id": company_id,
                "depreciation_additions": str(current_depreciation),
                "disposals": "0.00",
                "ending_accumulated_depreciation": str(
                    beginning_depreciation + current_depreciation
                ),
                "ending_cost": str(beginning_cost),
                "ending_net_book_value": str(
                    beginning_cost - beginning_depreciation - current_depreciation
                ),
                "cost_subaccount_id": _policy._class_subaccounts(asset_class)[0].split(
                    " - ", 1
                )[0],
                "depreciation_method": str(representative["depreciation_method"]),
                "useful_life_years": str(representative["useful_life_years"]),
                "earliest_placed_in_service_date": min(
                    str(row["placed_in_service_date"]) for row in class_assets
                ),
                "latest_placed_in_service_date": max(
                    str(row["placed_in_service_date"]) for row in class_assets
                ),
                "depreciable_period_basis": "; ".join(
                    sorted(
                        {
                            str(row["depreciable_period_months"])
                            for row in class_depreciation
                        }
                    )
                ),
                "fiscal_calendar_id": fiscal_calendar_id,
            }
        )
    return rows


@REGISTRY.check(
    "validate_fixed_asset_rollforwards",
    inputs=("fixed_asset", "fixed_asset_depreciation", "fixed_asset_rollforward"),
)
def validate_fixed_asset_rollforwards(
    fixed_assets: list[dict[str, str | None]],
    depreciation: list[dict[str, str]],
    rollforwards: list[dict[str, str]],
) -> None:
    """Check the retained opening-register rollforward identity."""
    if len(depreciation) != len(fixed_assets):
        raise ValueError("fixed-asset depreciation population does not resolve")
    for row in rollforwards:
        if row["acquisitions"] != "0.00" or row["disposals"] != "0.00":
            raise ValueError("opening-only fixed-asset rollforward contains activity")
        if Decimal(row["ending_net_book_value"]) != Decimal(
            row["ending_cost"]
        ) - Decimal(row["ending_accumulated_depreciation"]):
            raise ValueError("fixed-asset net book value does not tie")


@REGISTRY.rule(
    "reconcile_fixed_assets_to_gl",
    inputs=(
        "fixed_asset",
        "fixed_asset_rollforward",
        "trial_balance_account",
        "final_adjusted_trial_balance_account",
    ),
    outputs="fixed_asset_gl_reconciliation",
)
def reconcile_fixed_assets_to_gl(
    fixed_assets: list[dict[str, str | None]],
    rollforwards: list[dict[str, str]],
    trial_balance: list[dict[str, str]],
    final_adjusted_trial_balance: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Compare the register with both client and final-adjusted TB balances."""
    if not fixed_assets:
        return []
    tb = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    final_tb = {
        row["gl_account_id"]: Decimal(row["final_adjusted_closing_balance"])
        for row in final_adjusted_trial_balance
    }
    cost_accounts = {str(row["cost_gl_account_id"]) for row in fixed_assets}
    accumulated_accounts = {
        str(row["accumulated_depreciation_gl_account_id"])
        for row in fixed_assets
        if row["accumulated_depreciation_gl_account_id"]
    }
    register_cost = dsum(rollforwards, "ending_cost")
    register_accumulated = dsum(rollforwards, "ending_accumulated_depreciation")
    gl_cost = sum((tb[account] for account in cost_accounts), Decimal("0.00"))
    gl_accumulated = sum(
        (tb[account] for account in accumulated_accounts), Decimal("0.00")
    )
    final_gl_accumulated = sum(
        (final_tb[account] for account in accumulated_accounts), Decimal("0.00")
    )
    final_gl_cost = sum(
        (final_tb[account] for account in cost_accounts), Decimal("0.00")
    )
    register_net = register_cost - register_accumulated
    difference = register_net - (gl_cost + gl_accumulated)
    final_difference = register_net - (final_gl_cost + final_gl_accumulated)
    return [
        {
            "accumulated_depreciation_gl_account_id": " / ".join(
                sorted(accumulated_accounts)
            ),
            "asset_class": "all",
            "cost_gl_account_id": " / ".join(sorted(cost_accounts)),
            "difference": str(difference),
            "final_adjusted_difference": str(final_difference),
            "final_adjusted_gl_accumulated_depreciation": str(final_gl_accumulated),
            "fiscal_calendar_id": rollforwards[0]["fiscal_calendar_id"],
            "gl_accumulated_depreciation": str(gl_accumulated),
            "gl_cost": str(gl_cost),
            "reconciliation_status": (
                "tied_final_adjusted" if final_difference == 0 else "difference"
            ),
            "register_accumulated_depreciation": str(register_accumulated),
            "register_cost": str(register_cost),
            "register_net_book_value": str(register_net),
        }
    ]
