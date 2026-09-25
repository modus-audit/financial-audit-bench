"""Core company, chart-of-accounts, and opening-cash source assembly."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
    to_mutable_json,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)

_BASE_CHART = load_authored_policy("authored.chart-of-accounts.base-rows.v1").values
GENERAL_LEDGER_ACCOUNTS = [
    {**to_mutable_json(row), "company_id": "COMPANY-001"}
    for row in _BASE_CHART["base_rows"]
]
_CAPTIONS_BY_BUSINESS_TYPE = to_mutable_json(_BASE_CHART["captions_by_business_type"])


def general_ledger_accounts_for(business_type: str) -> list[dict]:
    """Return the target chart with business-specific captions."""
    captions = dict(_CAPTIONS_BY_BUSINESS_TYPE.get(business_type, {}))
    return [
        {**row, "name": captions.get(row["gl_account_id"], row["name"])}
        for row in GENERAL_LEDGER_ACCOUNTS
    ]


_IDENTITIES = load_authored_policy("authored.identities.document-identities.v1").values
COMPANY_NAME_STEMS = _IDENTITIES["company_name_stems"]
COMPANY_STATE_POOL = _IDENTITIES["company_state_pool"]
EMPLOYEE_GIVEN_NAMES = _IDENTITIES["employee_given_names"]
COMPANY_SURNAMES = _IDENTITIES["company_surnames"]
COMPANY_NAME_TEMPLATES = _IDENTITIES["company_name_templates"]
del _BASE_CHART, _IDENTITIES


def prior_period_account_balances(world: World) -> list[dict[str, str]]:
    """Build the prior-year closing vector from admitted world schedules."""
    company = world["company_context"]
    chart = world.get("general_ledger_account") or []
    if not chart:
        raise ValueError("cannot derive prior balances without a chart of accounts")
    # Inventory pre-exists only when the company carries inventory, and a trade company
    # opens with last year's sales/COGS on the prior P&L — both from the same
    # deterministic scale plan the subledger engine continues, so the prior TB and the
    # current-year subledgers agree by construction. The declared net-assets identity
    # closes the vector.
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_planning import (
        opening_inventory_cost,
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
        subledger_scale,
        trade_opening_ar,
    )

    feature = world["company_feature_profile"]
    calendar = world["fiscal_calendar"]
    operating_scale = world.get("operating_scale") or {}
    scale = subledger_scale(company, feature, calendar, operating_scale)
    inventory_opening = opening_inventory_cost(
        company, feature, calendar, operating_scale
    )
    opening_receivable = trade_opening_ar(
        company,
        feature,
        calendar,
        operating_scale,
    )
    # The fixed-asset openings derive from the register (whichever master the archetype
    # selected); the gated-off case reads an empty register and opens at zero.
    register = world.get("fixed_asset") or []
    cash_opening = Decimal(str(world["prior_period_bank_balance"]["ending_balance"]))
    # Unsupported chart accounts open at zero.
    nonzero: dict[str, Decimal] = {
        "GL-INVENTORY-001": inventory_opening,
        "GL-AR-001": opening_receivable,
        "GL-SERVICE-REVENUE-001": -Decimal(str(scale["prior_sales"])),
        "GL-COGS-001": Decimal(str(scale["prior_cogs"])),
        "GL-CASH-001": cash_opening,
    }

    def add_balance(account_id: str, amount: Any) -> None:
        if not account_id:
            raise ValueError("opening schedule has no GL account mapping")
        nonzero[account_id] = nonzero.get(account_id, Decimal("0.00")) + Decimal(
            str(amount)
        )

    for asset in register:
        add_balance(
            str(asset.get("cost_gl_account_id") or ""),
            asset["opening_cost"],
        )
        accumulated = Decimal(str(asset["opening_accumulated_depreciation"]))
        if accumulated:
            add_balance(
                str(asset.get("accumulated_depreciation_gl_account_id") or ""),
                -accumulated,
            )
        if asset["depreciation_method"] != "none":
            annual_depreciation = (
                Decimal(str(asset["opening_cost"]))
                / Decimal(str(asset["useful_life_years"]))
            ).quantize(Decimal("0.01"))
            add_balance(
                str(asset.get("depreciation_expense_gl_account_id") or ""),
                annual_depreciation,
            )

    employees = world.get("employee") or []
    if employees:
        from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.subsequent_events import (
            opening_stub_accrual,
        )

        add_balance(
            "GL-WH-PAYABLE-001",
            -Decimal(
                str(
                    opening_stub_accrual(
                        employees,
                        int(calendar["fiscal_year"]),
                    )
                )
            ),
        )

    chart_account_ids = {str(row["gl_account_id"]) for row in chart}
    required_accounts = {
        account_id for account_id, amount in nonzero.items() if amount != 0
    }
    missing_accounts = required_accounts - chart_account_ids
    if missing_accounts:
        raise ValueError(
            "cannot derive prior balances for accounts absent from the chart: "
            f"{sorted(missing_accounts)}"
        )
    capital_account_ids = [
        str(row["gl_account_id"])
        for row in chart
        if row.get("fiscal_close_behavior") == "equity_destination"
    ]
    if len(capital_account_ids) != 1:
        raise ValueError(
            "prior balances require exactly one chart-designated equity destination"
        )
    capital_account_id = capital_account_ids[0]
    if nonzero.get(capital_account_id, Decimal("0.00")):
        raise ValueError("an opening schedule cannot post directly to net assets")
    values = {
        row["gl_account_id"]: str(nonzero.get(row["gl_account_id"], Decimal("0.00")))
        for row in chart
        if row["gl_account_id"] != capital_account_id
    }
    # The chart designates the equity destination before any amounts are known. Its
    # value is the deterministic net-assets identity, never a dynamically selected
    # suspense/counteraccount.
    values[capital_account_id] = str(
        -sum((Decimal(amount) for amount in values.values()), Decimal("0.00"))
    )
    return [
        {
            "closing_balance": amount,
            "company_id": company["company_id"],
            "gl_account_id": account_id,
            "prior_fiscal_calendar_id": world["prior_period_bank_balance"][
                "prior_fiscal_calendar_id"
            ],
        }
        for account_id, amount in values.items()
    ]


def company_feature_profile(world: World) -> dict[str, Any]:
    business_type = str(world["business_type"])
    profile = {"company_feature_profile_id": f"PROFILE-{business_type.upper()}"}
    # The archetype rides on the profile so world checks can key on it.
    from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
        archetype_for,
    )

    profile["archetype"] = archetype_for(
        str(world.get("business_type") or "real_estate_development")
    )
    # The fine type rides along too: the subledger engine keys
    # inventory grammar, goods strings, and margin bands on it.
    profile["business_type"] = business_type
    # Profile feature overrides win last: business_type selects the archetype
    # (an operator forces payroll/receivables on, third-party rent off).
    for name, value in world.get("feature_overrides", ()):
        profile[name] = bool(value)
    return profile
