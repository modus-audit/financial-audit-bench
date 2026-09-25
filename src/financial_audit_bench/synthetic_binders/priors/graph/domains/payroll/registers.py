"""Employee-level payroll register construction."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.calculations import (
    _employment_basis,
    authorized_compensation_rate,
    employee_withholding_breakdown,
    period_breakdown,
    period_windows,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    CENT,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "derive_payroll_register_lines",
    inputs=(
        "employee",
        "payroll_run",
        "fiscal_calendar",
    ),
    outputs="payroll_register_line",
    gate="has_payroll",
)
def derive_payroll_register_lines(
    employees: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    if not employees or not runs:
        return []
    year = int(calendar["fiscal_year"])
    windows = period_windows(len(runs), year)
    benefit_rate = Decimal("0.00")
    lines: list[dict[str, Any]] = []
    for run_index, run in enumerate(
        sorted(runs, key=lambda row: str(row["period_end_date"]))
    ):
        period_start, period_end = windows[run_index]
        details = period_breakdown(employees, run_index, len(runs), year)
        deductions = employee_withholding_breakdown(details)
        for detail in details:
            employee = detail["employee"]
            employee_id = str(employee["employee_id"])
            statutory, voluntary = deductions[employee_id]
            overtime_earnings = Decimal("0.00")
            if detail["hourly_rate"] is not None:
                overtime_earnings = min(
                    detail["gross"] - detail["bonus"],
                    (
                        Decimal(str(detail["overtime_hours"] or "0"))
                        * Decimal(str(detail["hourly_rate"]))
                        * Decimal("1.5")
                    ).quantize(CENT),
                )
            regular_earnings = detail["gross"] - detail["bonus"] - overtime_earnings
            authorization_reference = (
                f"HR-AUTH-{employee_id}-RATE-{employee['raise_effective_date']}"
                if employee.get("raise_effective_date")
                and str(employee["raise_effective_date"]) <= period_start.isoformat()
                else f"HR-AUTH-{employee_id}-HIRE"
            )
            lines.append(
                {
                    "payroll_register_line_id": (
                        f"PAYLINE-{run['payroll_run_id']}-{employee_id}"
                    ),
                    "payroll_run_id": run["payroll_run_id"],
                    "employee_id": employee_id,
                    "period_start_date": period_start.isoformat(),
                    "period_end_date": run["period_end_date"],
                    "pay_date": run["pay_date"],
                    "posting_date": run["posting_date"],
                    "employee_name": employee["full_name"],
                    "role": employee["role"],
                    "department": employee.get("department", ""),
                    "pay_basis": _employment_basis(employee),
                    "authorized_rate": str(
                        authorized_compensation_rate(employee, period_start)
                    ),
                    "regular_hours": (
                        str(detail["regular_hours"])
                        if detail["regular_hours"] is not None
                        else None
                    ),
                    "overtime_hours": (
                        str(detail["overtime_hours"])
                        if detail["overtime_hours"] is not None
                        else None
                    ),
                    "regular_earnings": str(regular_earnings),
                    "overtime_earnings": str(overtime_earnings),
                    "bonus": str(detail["bonus"]),
                    "reported_tips": "0.00",
                    "gross_wages": str(detail["gross"]),
                    "statutory_withholding": str(statutory),
                    "voluntary_deductions": str(voluntary),
                    "net_wages": str(detail["gross"] - detail["withheld"]),
                    "employer_payroll_tax": str(detail["employer"]),
                    "employer_benefits": str(
                        (Decimal(str(detail["gross"])) * benefit_rate).quantize(CENT)
                    ),
                    "wage_gl_account_id": employee["wage_gl_account_id"],
                    "payroll_tax_expense_gl_account_id": employee[
                        "payroll_tax_expense_gl_account_id"
                    ],
                    "authorization_reference": authorization_reference,
                }
            )
    return lines
