"""Payroll-run construction, HR authorization, and benefit admission."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    prior_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.calculations import (
    _employment_basis,
    _parse_date,
    authorized_compensation_rate,
    pay_schedule,
    period_breakdown,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    BENEFITS_GL,
    CENT,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    person_name,
)


@REGISTRY.rule(
    "derive_payroll_runs",
    inputs=("employee", "fiscal_calendar"),
    outputs="payroll_run",
    gate="has_payroll",
)
def derive_payroll_runs(
    employees: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    """One run per pay period, totalled from the per-employee breakdown."""
    if not employees:
        return []
    periods = int(employees[0]["pay_periods_per_year"])
    year = int(calendar["fiscal_year"])
    benefit_rate = Decimal("0.00")
    runs = []
    for index, (period_end, pay_date) in enumerate(pay_schedule(periods, year)):
        rows = period_breakdown(employees, index, periods, year)
        gross = sum(r["gross"] for r in rows)
        wage_allocations: dict[str, dict[str, Decimal]] = {}
        employer_tax_allocations: dict[str, Decimal] = {}
        benefit_allocations: dict[str, Decimal] = {}
        for detail in rows:
            employee = detail["employee"]
            wage_account = employee["wage_gl_account_id"]
            wage_bucket = wage_allocations.setdefault(
                wage_account,
                {
                    "gross_wages": Decimal("0.00"),
                    "employee_withholding": Decimal("0.00"),
                },
            )
            wage_bucket["gross_wages"] += detail["gross"]
            wage_bucket["employee_withholding"] += detail["withheld"]
            tax_account = employee["payroll_tax_expense_gl_account_id"]
            employer_tax_allocations[tax_account] = (
                employer_tax_allocations.get(tax_account, Decimal("0.00"))
                + detail["employer"]
            )
            benefit_account = (
                "GL-COGS-001"
                if employee.get("employee_class") == "assigned_worker"
                else BENEFITS_GL
            )
            benefit_allocations[benefit_account] = benefit_allocations.get(
                benefit_account, Decimal("0.00")
            ) + (detail["gross"] * benefit_rate).quantize(CENT)
        runs.append(
            {
                "payroll_run_id": f"PAYROLL-{index + 1:02d}",
                "period_end_date": period_end.isoformat(),
                "pay_date": pay_date.isoformat(),
                "posting_date": pay_date.isoformat(),
                # Active-in-period headcount: hires appear, leavers drop.
                "employee_count": len(rows),
                "gross_wages": str(gross),
                "employee_withholding": str(sum(r["withheld"] for r in rows)),
                "employer_payroll_tax": str(sum(r["employer"] for r in rows)),
                # The posted benefit amount is the sum of employee-level allocations.
                # Calculating it again from aggregate gross can differ by a few cents
                # because each employee line is rounded first, leaving a visible
                # residual between the register and departmental posting.
                "employer_benefits_cost": str(
                    sum(benefit_allocations.values(), Decimal("0.00"))
                ),
                "wage_gl_account_id": employees[0]["wage_gl_account_id"],
                "payroll_tax_expense_gl_account_id": employees[0][
                    "payroll_tax_expense_gl_account_id"
                ],
                "wage_allocations": {
                    account: {field: str(amount) for field, amount in values.items()}
                    for account, values in wage_allocations.items()
                },
                "employer_tax_allocations": {
                    account: str(amount)
                    for account, amount in employer_tax_allocations.items()
                },
                "benefit_allocations": {
                    account: str(amount)
                    for account, amount in benefit_allocations.items()
                },
            }
        )
    return runs


@REGISTRY.rule(
    "derive_payroll_authorization_events",
    inputs=("employee", "fiscal_calendar"),
    outputs="payroll_authorization_event",
    gate="has_payroll",
)
def derive_payroll_authorization_events(
    employees: list[dict[str, Any]], calendar: dict[str, Any]
) -> list[dict[str, Any]]:
    """One auditable HR authorization trail, independent of JE approval."""
    fiscal_start = date.fromisoformat(str(calendar["start_date"]))
    rows: list[dict[str, Any]] = []
    identity_key = "|".join(
        str(employee["full_name"])
        for employee in sorted(employees, key=lambda row: row["employee_id"])
    )
    approver, preparer, reviewer = (
        person_name(f"{identity_key}|payroll-workflow", index) for index in range(3)
    )

    def add(
        employee: dict[str, Any],
        suffix: str,
        event_type: str,
        effective: date,
        prior_rate: Decimal | None,
        new_rate: Decimal | None,
    ) -> None:
        reference = f"HR-AUTH-{employee['employee_id']}-{suffix}"
        rows.append(
            {
                "payroll_authorization_event_id": reference,
                "employee_id": employee["employee_id"],
                "employee_name": employee["full_name"],
                "event_type": event_type,
                "effective_date": effective.isoformat(),
                "prior_authorized_rate": (
                    str(prior_rate) if prior_rate is not None else None
                ),
                "new_authorized_rate": (
                    str(new_rate) if new_rate is not None else None
                ),
                "pay_basis": _employment_basis(employee),
                "approved_by": approver,
                "entered_by": preparer,
                "reviewed_by": reviewer,
                "authorization_reference": reference,
                "evidence_date": prior_business_day(effective).isoformat(),
            }
        )

    for employee in sorted(employees, key=lambda row: row["employee_id"]):
        hire = _parse_date(employee.get("hire_date")) or fiscal_start
        raise_date = _parse_date(employee.get("raise_effective_date"))
        base_as_of = raise_date - timedelta(days=1) if raise_date else hire
        base_rate = authorized_compensation_rate(employee, base_as_of)
        add(employee, "HIRE", "hire_or_base_authorization", hire, None, base_rate)
        if raise_date:
            add(
                employee,
                f"RATE-{raise_date.isoformat()}",
                "compensation_change",
                raise_date,
                base_rate,
                authorized_compensation_rate(employee, raise_date),
            )
        termination = _parse_date(employee.get("termination_date"))
        if termination:
            add(
                employee,
                f"TERM-{termination.isoformat()}",
                "termination",
                termination,
                authorized_compensation_rate(employee, termination),
                None,
            )
    return rows
