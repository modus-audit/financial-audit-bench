"""Trial-balance boundary checks and current-period rollforwards."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


_CENT = Decimal("0.01")
_PRIOR_PERIOD_BALANCE_INPUTS = (
    "company_context",
    "company_feature_profile",
    "fiscal_calendar",
    "general_ledger_account",
    "prior_period_bank_balance",
    "fixed_asset",
    "operating_scale",
    "employee",
)


def _cent_amount(value: Any, label: str) -> Decimal:
    """Parse a finite, cent-denominated amount or fail closed."""
    try:
        amount = Decimal(str(value))
        rounded = amount.quantize(_CENT)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not a valid monetary amount") from exc
    if not amount.is_finite() or amount != rounded:
        raise ValueError(f"{label} is not a finite cent-denominated amount")
    return amount


def _schedule_totals(
    rows: list[dict[str, Any]],
    *,
    account_field: str,
    amount_field: str,
    label: str,
    sign: Decimal = Decimal("1"),
) -> dict[str, Decimal]:
    """Aggregate one cleared schedule by its explicit GL mapping."""
    totals: dict[str, Decimal] = {}
    for index, row in enumerate(rows):
        amount = _cent_amount(row.get(amount_field), f"{label}[{index}].{amount_field}")
        account_id = str(row.get(account_field) or "").strip()
        if not account_id:
            # A schedule may legitimately carry a non-depreciable land row (or another
            # zero-balance component) without a contra-account mapping. Missing mappings
            # remain an error as soon as they carry value.
            if amount == 0:
                continue
            raise ValueError(f"{label}[{index}] has no {account_field}")
        totals[account_id] = totals.get(account_id, Decimal("0.00")) + sign * amount
    return totals


def _require_schedule_tie(
    balances: dict[str, Decimal],
    expected: dict[str, Decimal],
    *,
    account_ids: set[str] = frozenset(),
    label: str,
) -> None:
    """Require each scheduled account to equal its derived opening amount."""
    for account_id in sorted(set(expected) | set(account_ids)):
        if account_id not in balances:
            if expected.get(account_id, Decimal("0.00")):
                raise ValueError(f"{label} maps to missing account {account_id}")
            continue
        actual = balances[account_id]
        scheduled = expected.get(account_id, Decimal("0.00"))
        if actual != scheduled:
            raise ValueError(
                f"prior-period {label} does not tie for {account_id}: "
                f"{actual} != {scheduled}"
            )


def _balance_amounts(prior_balances: list[dict[str, Any]]) -> dict[str, Decimal]:
    """Return unique account amounts for one already shape-checked vector."""
    account_ids = [str(row.get("gl_account_id") or "") for row in prior_balances]
    if any(not account_id for account_id in account_ids) or len(
        set(account_ids)
    ) != len(account_ids):
        raise ValueError("prior-period account IDs are missing or not unique")
    return {
        account_id: _cent_amount(
            row.get("closing_balance"), f"prior_period_account_balance[{account_id}]"
        )
        for account_id, row in zip(account_ids, prior_balances, strict=True)
    }


def validate_prior_period_balance_boundary(
    prior_balances: list[dict[str, Any]],
    company: dict[str, Any],
    profile: dict[str, Any],
    calendar: dict[str, Any],
    accounts: list[dict[str, Any]],
    prior_bank_balance: dict[str, Any],
) -> None:
    """Reject an incomplete, unbalanced, or identity-detached prior vector."""
    if not accounts:
        raise ValueError("prior-period balances require a nonempty emitted chart")
    company_id = str(company.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("prior-period balances require a synthetic company ID")

    chart_ids = [str(row.get("gl_account_id") or "").strip() for row in accounts]
    if any(not account_id for account_id in chart_ids) or len(set(chart_ids)) != len(
        chart_ids
    ):
        raise ValueError("emitted chart has missing or duplicate account IDs")
    for row in accounts:
        if row.get("company_id") != company_id:
            raise ValueError("emitted chart contains a foreign company account")

    equity_destinations = {
        str(row["gl_account_id"])
        for row in accounts
        if row.get("fiscal_close_behavior") == "equity_destination"
    }
    if len(equity_destinations) != 1:
        raise ValueError(
            "opening-ledger policy requires exactly one predesignated capital account"
        )
    capital_account_id = next(iter(equity_destinations))

    balance_ids = [
        str(row.get("gl_account_id") or "").strip() for row in prior_balances
    ]
    if len(set(balance_ids)) != len(balance_ids):
        raise ValueError("prior-period account IDs are not unique")
    if set(balance_ids) != set(chart_ids):
        raise ValueError(
            "prior-period population must cover the complete emitted chart; "
            "account-presence templates are not allowed"
        )

    prior_calendar_id = prior_bank_balance.get("prior_fiscal_calendar_id")
    if not prior_calendar_id:
        raise ValueError("prior bank balance has no prior fiscal-calendar identity")
    if prior_bank_balance.get("company_id") != company_id:
        raise ValueError("prior bank balance belongs to a different company")
    try:
        prior_period_end = date.fromisoformat(
            str(prior_bank_balance["statement_period_end"])
        )
        current_period_start = date.fromisoformat(str(calendar["start_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "opening-ledger fiscal boundary is missing or invalid"
        ) from exc
    if prior_period_end + timedelta(days=1) != current_period_start:
        raise ValueError(
            "prior bank statement does not end immediately before the fiscal year"
        )

    balances = _balance_amounts(prior_balances)
    for row in prior_balances:
        account_id = str(row["gl_account_id"])
        if row.get("company_id") != company_id:
            raise ValueError(f"prior-period row has foreign company: {account_id}")
        if row.get("prior_fiscal_calendar_id") != prior_calendar_id:
            raise ValueError(
                f"prior-period row has the wrong fiscal calendar: {account_id}"
            )

    total = sum(balances.values(), Decimal("0.00"))
    if total:
        raise ValueError(f"prior-period account vector does not balance: {total}")
    expected_capital = -sum(
        (
            amount
            for account_id, amount in balances.items()
            if account_id != capital_account_id
        ),
        Decimal("0.00"),
    )
    if balances[capital_account_id] != expected_capital:
        raise ValueError("predesignated capital does not equal derived net assets")

    _require_schedule_tie(
        balances,
        {
            "GL-CASH-001": _cent_amount(
                prior_bank_balance.get("ending_balance"),
                "prior_period_bank_balance.ending_balance",
            )
        },
        label="operating-cash schedule",
    )

    # Accounts without a supporting schedule must open at zero.
    inactive_accounts = {
        "has_fixed_assets": {"GL-FIXED-ASSET-001", "GL-ACCUM-DEPR-001"},
        "has_payroll": {"GL-WH-PAYABLE-001"},
    }
    for feature, account_ids in inactive_accounts.items():
        if profile.get(feature):
            continue
        nonzero = sorted(
            account_id
            for account_id in account_ids
            if balances.get(account_id, Decimal("0.00"))
        )
        if nonzero:
            raise ValueError(
                f"inactive opening family {feature} has nonzero accounts: {nonzero}"
            )


def validate_fixed_asset_opening_ties(
    prior_balances: list[dict[str, Any]],
    fixed_assets: list[dict[str, Any]],
) -> None:
    """Tie owned fixed-asset openings to the register."""
    balances = _balance_amounts(prior_balances)
    _require_schedule_tie(
        balances,
        _schedule_totals(
            fixed_assets,
            account_field="cost_gl_account_id",
            amount_field="opening_cost",
            label="fixed_asset",
        ),
        account_ids={"GL-FIXED-ASSET-001"},
        label="fixed-asset cost schedule",
    )
    _require_schedule_tie(
        balances,
        _schedule_totals(
            fixed_assets,
            account_field="accumulated_depreciation_gl_account_id",
            amount_field="opening_accumulated_depreciation",
            label="fixed_asset",
            sign=Decimal("-1"),
        ),
        account_ids={"GL-ACCUM-DEPR-001"},
        label="fixed-asset accumulated-depreciation schedule",
    )


def validate_payroll_opening_ties(
    prior_balances: list[dict[str, Any]],
    calendar: dict[str, Any],
    employees: list[dict[str, Any]],
) -> None:
    """Tie the opening payroll liability to the canonical stub schedule."""
    from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.subsequent_events import (
        opening_stub_accrual,
    )

    fiscal_year = int(calendar["fiscal_year"])
    _require_schedule_tie(
        _balance_amounts(prior_balances),
        {
            "GL-WH-PAYABLE-001": -_cent_amount(
                opening_stub_accrual(employees, fiscal_year),
                "employee opening payroll stub",
            )
        },
        label="opening-payroll schedule",
    )


def roll_forward_trial_balance_accounts(
    general_ledger_accounts: list[dict[str, Any]],
    opening_balances: list[dict[str, str]],
    journal_entries: list[dict[str, str]],
    journal_entry_lines: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Roll opening balances and non-opening entries into client TB rows."""
    opening_by_account = {
        row["gl_account_id"]: Decimal(row["opening_balance"])
        for row in opening_balances
    }
    opening_entry_ids = {
        row["journal_entry_id"]
        for row in journal_entries
        if row["journal_type"] == "opening_carryforward"
    }
    activity_by_account: dict[str, list[Decimal]] = {}
    for row in journal_entry_lines:
        if row["journal_entry_id"] not in opening_entry_ids:
            activity_by_account.setdefault(row["gl_account_id"], []).append(
                Decimal(row["signed_amount"])
            )
    rows = []
    for account in general_ledger_accounts:
        amounts = activity_by_account.get(account["gl_account_id"], [])
        debit = sum((amount for amount in amounts if amount > 0), Decimal("0.00"))
        credit = -sum((amount for amount in amounts if amount < 0), Decimal("0.00"))
        opening = opening_by_account[account["gl_account_id"]]
        rows.append(
            {
                "closing_balance": str(opening + debit - credit),
                "gl_account_id": account["gl_account_id"],
                "opening_balance": str(opening),
                "period_credit": str(credit),
                "period_debit": str(debit),
            }
        )
    return rows


def roll_forward_final_adjusted_trial_balance(
    trial_balance: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Expose the clean client trial balance as the final audit balance."""
    rows = [
        {
            "adjustment_credit": "0.00",
            "adjustment_debit": "0.00",
            "client_closing_balance": row["closing_balance"],
            "final_adjusted_closing_balance": row["closing_balance"],
            "gl_account_id": row["gl_account_id"],
        }
        for row in trial_balance
    ]
    if sum(
        (Decimal(row["final_adjusted_closing_balance"]) for row in rows),
        Decimal("0.00"),
    ):
        raise ValueError("final-adjusted trial balance does not balance")
    return rows


# --- graph registration -----------------------------------------------------

REGISTRY.sample(
    "prior_period_account_balance",
    # fixed_asset supplies the register opening balances that the prior TB balances are
    # pinned to. operating_scale feeds the shared trade-scale plan (subledger_scale)
    # that opens inventory and prior sales/COGS.
    inputs=_PRIOR_PERIOD_BALANCE_INPUTS,
)

REGISTRY.check(
    "check_prior_period_balance_boundary",
    inputs=(
        "prior_period_account_balance",
        "company_context",
        "company_feature_profile",
        "fiscal_calendar",
        "general_ledger_account",
        "prior_period_bank_balance",
    ),
)(validate_prior_period_balance_boundary)

REGISTRY.check(
    "check_prior_period_fixed_asset_openings",
    inputs=(
        "prior_period_account_balance",
        "fixed_asset",
    ),
)(validate_fixed_asset_opening_ties)

REGISTRY.check(
    "check_prior_period_payroll_openings",
    inputs=("prior_period_account_balance", "fiscal_calendar", "employee"),
)(validate_payroll_opening_ties)

REGISTRY.rule(
    "aggregate_trial_balance_rollforward",
    inputs=(
        "general_ledger_account",
        "opening_account_balance",
        "journal_entry",
        "journal_entry_line",
    ),
    outputs="trial_balance_account",
)(roll_forward_trial_balance_accounts)

REGISTRY.rule(
    "roll_forward_final_adjusted_trial_balance",
    inputs=("trial_balance_account",),
    outputs="final_adjusted_trial_balance_account",
)(roll_forward_final_adjusted_trial_balance)
