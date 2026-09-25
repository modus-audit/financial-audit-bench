"""Payroll projection operation retained by the employee-roster workbook."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


@op("derive_pay_periods_worked")
def derive_pay_periods_worked(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """Return the number of payroll windows overlapping each employee."""
    from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.calculations import (
        _active_fraction,
        period_windows,
    )

    employees = world.get("employee") or []
    if not employees:
        return None
    year = int(world["fiscal_calendar"]["fiscal_year"])
    return [
        str(
            sum(
                1
                for window in period_windows(
                    int(employee["pay_periods_per_year"]), year
                )
                if _active_fraction(employee, window) > 0
            )
        )
        for employee in employees
    ]
