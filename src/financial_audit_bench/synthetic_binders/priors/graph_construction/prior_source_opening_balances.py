"""Revenue anchors and opening-balance samplers."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.prior_source_common import (
    _fixed_decimal_context,
)

_OPERATING_POLICY = load_authored_policy("authored.global.operating-policy.v1").values


def _authored_band(source_id: str, business_type: str) -> tuple[float, float]:
    values = _OPERATING_POLICY["policies"][source_id]["bands_by_business_type"][
        business_type
    ]
    low, high = map(float, values)
    if low > high:
        raise ValueError(f"reversed authored band for {source_id}/{business_type}")
    return low, high


@_fixed_decimal_context
def _sample_prior_period_bank_balance(self, world: World) -> dict[str, str]:
    """Set opening cash from a sampled number of months of operating outflows."""
    profile = world.get("company_feature_profile") or {}
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
        subledger_scale,
    )

    source_id = "policy.opening_operating_cash"
    scale = subledger_scale(
        world["company_context"],
        profile,
        world["fiscal_calendar"],
        world.get("operating_scale"),
    )
    annual_outflows = Decimal(str(scale.get("cogs") or "0"))
    if annual_outflows <= 0:
        revenue_source = "policy.operating_revenue_scale"
        revenue_low, revenue_high = _authored_band(revenue_source, self.business_type)
        annual_outflows = Decimal(
            str(self._rng(revenue_source).uniform(revenue_low, revenue_high))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    month_low, month_high = _authored_band(source_id, self.business_type)
    cover = Decimal(str(self._rng(source_id).uniform(month_low, month_high)))
    opening = (annual_outflows / 12 * cover).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )

    bank = world["bank_account"]
    company = world["company_context"]
    year = int(world["fiscal_calendar"]["fiscal_year"])
    period_end = _OPERATING_POLICY["prior_bank_statement_period_end"]
    row = {
        "bank_account_id": str(bank["bank_account_id"]),
        "company_id": str(company["company_id"]),
        "ending_balance": f"{opening:.2f}",
        "prior_fiscal_calendar_id": "CALENDAR-CASE-000",
        "statement_period_end": date(
            year + int(period_end["year_offset"]),
            int(period_end["month"]),
            int(period_end["day"]),
        ).isoformat(),
    }
    return row


@_fixed_decimal_context
def _sample_prior_period_account_balance(self, world: World) -> list[dict[str, str]]:
    """Compose a complete opening ledger from cleared world schedules."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.authored.core_company_cash import (
        prior_period_account_balances,
    )

    rows = [dict(row) for row in prior_period_account_balances(world)]
    chart = world.get("general_ledger_account") or []
    chart_ids = [str(row.get("gl_account_id") or "") for row in chart]
    if not chart_ids or any(not account_id for account_id in chart_ids):
        raise ValueError("opening ledger requires a complete emitted chart")
    if len(set(chart_ids)) != len(chart_ids):
        raise ValueError("opening ledger chart contains duplicate account IDs")
    by_account = {str(row["gl_account_id"]): row for row in rows}
    if set(by_account) != set(chart_ids):
        raise ValueError("opening ledger constructor did not cover the full chart")
    equity_ids = [
        str(row["gl_account_id"])
        for row in chart
        if row.get("fiscal_close_behavior") == "equity_destination"
    ]
    if len(equity_ids) != 1:
        raise ValueError("opening ledger requires one chart-designated equity account")
    equity_id = equity_ids[0]

    def cent(value: Any, label: str) -> Decimal:
        try:
            amount = Decimal(str(value))
            rounded = amount.quantize(Decimal("0.01"))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise ValueError(f"{label} is not a monetary amount") from exc
        if not amount.is_finite() or amount != rounded:
            raise ValueError(f"{label} is not finite cent money")
        return rounded

    # Normalize every amount to an explicit cents representation, then
    # recompute only the predesignated equity identity after schedule ties.
    non_equity_total = Decimal("0.00")
    for account_id in chart_ids:
        if account_id == equity_id:
            continue
        amount = cent(
            by_account[account_id]["closing_balance"],
            f"opening ledger {account_id}",
        )
        by_account[account_id]["closing_balance"] = f"{amount:.2f}"
        non_equity_total += amount
    by_account[equity_id]["closing_balance"] = f"{-non_equity_total:.2f}"

    expected_company = str(world["company_context"]["company_id"] or "")
    expected_calendar = str(
        world["prior_period_bank_balance"]["prior_fiscal_calendar_id"] or ""
    )
    for row in rows:
        if row.get("company_id") != expected_company:
            raise ValueError("opening ledger row has a foreign company")
        if row.get("prior_fiscal_calendar_id") != expected_calendar:
            raise ValueError("opening ledger row has a foreign fiscal calendar")
    if sum((Decimal(row["closing_balance"]) for row in rows), Decimal("0.00")):
        raise ValueError("opening ledger does not balance")

    return rows
