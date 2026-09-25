"""Deterministic payroll schedules and employee-level calculations."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    prior_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    CENT,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    month_end,
)


def _business_day(when: date) -> date:
    """Roll a weekend or holiday pay date back to the prior banking day."""
    return prior_business_day(when)


def pay_schedule(periods: int, year: int) -> list[tuple[date, date]]:
    """(period_end, pay_date) per period for the cadence, pay dates rolled to banking days. 12 = monthly, 24 = semi-monthly (15th + month end), 26 = biweekly Fridays, 52 = weekly Fridays."""
    if periods == 24:
        ends = []
        for month in range(1, 13):
            ends.append(date(year, month, 15))
            ends.append(month_end(date(year, month, 1)))
    elif periods in (26, 52):
        first = date(year, 1, 1)
        first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
        step = 14 if periods == 26 else 7
        ends = [
            first_friday + timedelta(days=step * index + step - 7)
            for index in range(periods)
        ]
    else:
        ends = [month_end(date(year, month, 1)) for month in range(1, 13)][
            : max(1, periods)
        ]
    return [(end, _business_day(end)) for end in ends]


def period_windows(periods: int, year: int) -> list[tuple[date, date]]:
    """Return service windows; weekly cadences may begin before January 1."""
    ends = [end for end, _ in pay_schedule(periods, year)]
    first_start = (
        ends[0] - timedelta(days=(14 if periods == 26 else 7) - 1)
        if periods in {26, 52}
        else date(year, 1, 1)
    )
    starts = [first_start] + [end + timedelta(days=1) for end in ends[:-1]]
    return list(zip(starts, ends))


def _parse_date(when: Any) -> date | None:
    return date.fromisoformat(when) if when else None


def _active_fraction(employee: dict[str, Any], window: tuple[date, date]) -> Decimal:
    """Share of a pay period the employee was on the roster."""
    start, end = window
    hire = _parse_date(employee.get("hire_date"))
    term = _parse_date(employee.get("termination_date"))
    first = max(start, hire) if hire else start
    last = min(end, term - timedelta(days=1)) if term else end
    if first > last:
        return Decimal("0")
    return Decimal((last - first).days + 1) / Decimal((end - start).days + 1)


def _period_salary(employee: dict[str, Any], period_start: date) -> Decimal:
    """Annual salary rate in effect for a period: base salary, stepped up from the first period starting on/after an explicit raise event."""
    salary = Decimal(str(employee["annual_salary"]))
    effective = _parse_date(employee.get("raise_effective_date"))
    if effective and effective <= period_start:
        salary *= Decimal("1") + Decimal(str(employee["raise_pct"]))
    return salary


def _employment_basis(employee: dict[str, Any]) -> str:
    """Return one explicit, internally consistent payroll basis."""
    pay_basis = employee.get("pay_basis")
    employment_basis = employee.get("employment_basis")
    if pay_basis and employment_basis and str(pay_basis) != str(employment_basis):
        raise ValueError("employee payroll-basis fields disagree")
    basis = str(pay_basis or employment_basis or "")
    if basis not in {"salary", "hourly", "tipped_hourly"}:
        raise ValueError("employee lacks a supported explicit payroll basis")
    return basis


def authorized_compensation_rate(
    employee: dict[str, Any], as_of: date | None = None
) -> Decimal:
    """Return the HR-authorized salary or hourly rate effective on ``as_of``."""
    annual = Decimal(str(employee["annual_salary"]))
    effective = _parse_date(employee.get("raise_effective_date"))
    if effective and (as_of is None or effective <= as_of):
        annual *= Decimal("1") + Decimal(str(employee.get("raise_pct") or "0"))
    basis = _employment_basis(employee)
    if basis in {"hourly", "tipped_hourly"}:
        return (annual / Decimal("2080")).quantize(Decimal("0.0001"))
    return annual.quantize(CENT)


def _employer_tax_rate(employee: dict[str, Any]) -> Decimal:
    """Return the explicit accounted employer-tax ratio carried by the row."""
    try:
        rate = Decimal(str(employee["employer_tax_rate"]))
    except (KeyError, ArithmeticError, ValueError) as error:
        raise ValueError("employee lacks an accounted employer-tax ratio") from error
    if not Decimal("0") <= rate <= Decimal("1"):
        raise ValueError("employee employer-tax ratio must be between zero and one")
    return rate


def period_breakdown(
    employees: list[dict[str, Any]], index: int, periods: int, year: int
) -> list[dict[str, Any]]:
    """Per-employee amounts for one pay period; shared by the run
    derivation and the payroll-register renderer so both always agree."""
    windows = period_windows(periods, year)
    window = windows[index]
    rows = []
    for employee in employees:
        fraction = _active_fraction(employee, window)
        if fraction <= 0:
            # Not yet hired, or already terminated: no register line, no accrual — the
            # roster changes surface as rows appearing and disappearing across pay-date
            # sections.
            continue
        salary = _period_salary(employee, window[0])
        # Boundary periods pro-rate by days active (partial first/final
        # checks); full periods pay the plain per-period salary.
        base = (salary / periods * fraction).quantize(CENT)
        gross = base
        basis = _employment_basis(employee)
        # Pay basis must be explicit.  A generic/full-time label is not hashed
        # into a guessed hourly classification.
        hourly = basis in {"hourly", "tipped_hourly"}
        bonus = Decimal("0.00")
        withheld = Decimal("0.00")
        employer = (gross * _employer_tax_rate(employee)).quantize(CENT)
        regular_hours = overtime_hours = hourly_rate = None
        if hourly:
            hourly_rate = (salary / Decimal("2080")).quantize(Decimal("0.0001"))
            nominal_hours = Decimal("2080") / periods * fraction
            wages = gross - bonus
            if wages >= base:
                regular_hours = nominal_hours.quantize(Decimal("0.01"))
                overtime_hours = (
                    (wages - base) / (hourly_rate * Decimal("1.5"))
                ).quantize(Decimal("0.01"))
            else:
                regular_hours = (wages / hourly_rate).quantize(Decimal("0.01"))
                overtime_hours = Decimal("0.00")
        rows.append(
            {
                "employee": employee,
                "gross": gross,
                "bonus": bonus,
                "withheld": withheld,
                "employer": employer,
                "regular_hours": regular_hours,
                "overtime_hours": overtime_hours,
                "hourly_rate": hourly_rate,
            }
        )
    return rows


def employee_withholding_breakdown(
    details: list[dict[str, Any]], voluntary_total: Decimal = Decimal("0.00")
) -> dict[str, tuple[Decimal, Decimal]]:
    """Allocate a run-level statutory/voluntary split to employee rows."""
    total_withheld = sum(
        (Decimal(str(row.get("withheld") or "0")) for row in details),
        Decimal("0.00"),
    )
    if voluntary_total < 0 or voluntary_total > total_withheld:
        raise ValueError("invalid voluntary-deduction total for employee allocation")
    result: dict[str, tuple[Decimal, Decimal]] = {}
    allocated = Decimal("0.00")
    withholding_by_row = [
        Decimal(str(detail.get("withheld") or "0")) for detail in details
    ]
    for index, detail in enumerate(details):
        employee_id = str(detail["employee"]["employee_id"])
        withheld = withholding_by_row[index]
        remaining = voluntary_total - allocated
        if index == len(details) - 1:
            voluntary = remaining
        else:
            proposed = (
                voluntary_total * withheld / total_withheld
                if total_withheld
                else Decimal("0.00")
            ).quantize(CENT)
            future_capacity = sum(withholding_by_row[index + 1 :], Decimal("0.00"))
            minimum_now = max(Decimal("0.00"), remaining - future_capacity)
            voluntary = max(
                minimum_now,
                min(proposed, withheld, remaining),
            )
        if voluntary > withheld:
            raise ValueError(
                f"employee voluntary deductions exceed withholding for {employee_id}"
            )
        allocated += voluntary
        result[employee_id] = (withheld - voluntary, voluntary)
    if allocated != voluntary_total:
        raise ValueError("employee voluntary-deduction allocation does not foot")
    return result
