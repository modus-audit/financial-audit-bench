"""Subsequent-period payroll events and opening stub accruals."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    prior_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.calculations import (
    _active_fraction,
    _parse_date,
    _period_salary,
    pay_schedule,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    CENT,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    month_end,
)


def _next_payroll_window(last_period_end: date, periods: int) -> tuple[date, date]:
    start = last_period_end + timedelta(days=1)
    if periods == 24:
        end = date(start.year, start.month, 15)
        if start.day > 15:
            end = month_end(start)
    elif periods == 12:
        end = month_end(start)
    else:
        end = last_period_end + timedelta(days=7 if periods == 52 else 14)
    return start, end


@REGISTRY.rule(
    "derive_payroll_subsequent_events",
    inputs=(
        "employee",
        "payroll_run",
        "payroll_remittance",
        "journal_entry_line",
        "fiscal_calendar",
    ),
    outputs="payroll_subsequent_event",
    gate="has_payroll",
)
def derive_payroll_subsequent_events(
    employees: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    remittances: list[dict[str, Any]],
    journal_lines: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    if not employees or not runs:
        return []
    year_end = date.fromisoformat(str(calendar["end_date"]))
    last = max(runs, key=lambda row: row["period_end_date"])
    periods = int(employees[0]["pay_periods_per_year"])
    period_start, period_end = _next_payroll_window(
        date.fromisoformat(str(last["period_end_date"])), periods
    )
    pay_date = prior_business_day(period_end)
    next_gross = Decimal("0.00")
    next_withholding = Decimal("0.00")
    for employee in employees:
        fraction = _active_fraction(employee, (period_start, period_end))
        if fraction > 0:
            employee_gross = (
                _period_salary(employee, period_start) / periods * fraction
            ).quantize(CENT)
            next_gross += employee_gross
            next_withholding += Decimal("0.00")
    year_end_accrual = sum(
        (
            Decimal(str(line["signed_amount"]))
            for line in journal_lines
            if line.get("journal_entry_id") == "JOURNAL-PAYROLL-YE-ACCRUAL"
            and Decimal(str(line["signed_amount"])) > 0
        ),
        Decimal("0.00"),
    )
    rows = [
        {
            "payroll_subsequent_event_id": "SUBSEQ-PAYROLL-01",
            "source_payroll_run_id": str(last["payroll_run_id"]),
            "event_type": "subsequent_regular_payroll_and_accrual_reversal",
            "period_start_date": period_start.isoformat(),
            "period_end_date": period_end.isoformat(),
            "settlement_date": pay_date.isoformat(),
            "gross_amount": str(next_gross),
            # The payroll-bank account settles employee NET pay. Keeping gross separate
            # prevents the subsequent statement from labeling a gross register control
            # as an ACH payment.
            "settlement_amount": str(next_gross - next_withholding),
            "year_end_accrual_amount": str(year_end_accrual),
            "reversal_reference": (
                "SUBSEQ-GL-PAYROLL-ACCRUAL-REVERSAL"
                if year_end_accrual
                else "N/A-zero-earned-unpaid-days"
            ),
            "bank_eft_reference": "EFT-SUBSEQ-PAYROLL-01-NET",
            "remittance_reference": "SUBSEQ-PAYROLL-REGISTER-01",
            "settlement_status": "settled_subsequent",
        }
    ]
    for remittance in remittances:
        settlement = date.fromisoformat(str(remittance["settlement_date"]))
        if settlement <= year_end:
            continue
        rows.append(
            {
                "payroll_subsequent_event_id": (
                    f"SUBSEQ-{remittance['payroll_remittance_id']}"
                ),
                "source_payroll_run_id": remittance["payroll_run_id"],
                "event_type": remittance["remittance_type"],
                "period_start_date": remittance["liability_period_end"],
                "period_end_date": remittance["liability_period_end"],
                "settlement_date": remittance["settlement_date"],
                "gross_amount": None,
                "settlement_amount": remittance["amount"],
                "year_end_accrual_amount": remittance["amount"],
                "reversal_reference": "SUBSEQ-GL-PAYROLL-LIABILITY-RELIEF",
                "bank_eft_reference": remittance["bank_eft_reference"],
                "remittance_reference": remittance["return_or_provider_reference"],
                "settlement_status": remittance["settlement_status"],
            }
        )
    return rows


def opening_stub_accrual(employees: list[dict[str, Any]], fiscal_year: int) -> Decimal:
    """The wage accrual the client posted at the PRIOR 12/31."""
    if not employees:
        return Decimal("0.00")
    # Only staff already on board at the prior 12/31 accrued prior-year wages: in-year
    # hires (hire_date inside the fiscal year) had no stub, and in-year raises had not
    # happened yet, so base salaries price it.
    fiscal_start = date(fiscal_year, 1, 1)
    employees = [
        e
        for e in employees
        if (hire := _parse_date(e.get("hire_date"))) is None or hire < fiscal_start
    ]
    if not employees:
        return Decimal("0.00")
    periods = int(employees[0]["pay_periods_per_year"])
    prior_year = fiscal_year - 1
    prior_end = date(prior_year, 12, 31)
    last_period_end = max(end for end, _ in pay_schedule(periods, prior_year))
    stub_days = (prior_end - last_period_end).days
    if stub_days <= 0:
        return Decimal("0.00")
    year_days = (prior_end - date(prior_year, 1, 1)).days + 1
    annual = sum(Decimal(str(e["annual_salary"])) for e in employees)
    return max(Decimal("0.00"), (annual * stub_days / year_days).quantize(CENT))
