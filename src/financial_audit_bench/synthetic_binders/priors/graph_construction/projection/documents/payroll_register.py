"""Machine-readable payroll register detail."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    finalize_workbook,
    open_pbc_sheet,
    write_header,
)


def _applicable(world: World) -> bool:
    return bool(world.get("payroll_run"))


def _detail_workbook(
    world: World,
    output_dir: Path,
    year: str,
) -> dict[str, Any]:
    workbook, sheet = open_pbc_sheet("Payroll Register Detail", "PAY-01", world=world)
    write_header(
        sheet,
        [
            "Run ID",
            "Period Start",
            "Period End",
            "Pay Date",
            "Employee ID",
            "Employee",
            "Department",
            "Pay Basis",
            "Authorized Rate",
            "Regular Hours",
            "Overtime Hours",
            "Regular Earnings",
            "Overtime Earnings",
            "Bonus",
            "Gross Wages",
            "Statutory Withholding",
            "Voluntary Deductions",
            "Net Wages",
            "Employer Payroll Tax",
            "Employer Benefits",
            "Authorization Reference",
        ],
    )
    for row in sorted(
        world.get("payroll_register_line") or [],
        key=lambda value: (
            value["pay_date"],
            value["employee_id"],
        ),
    ):
        sheet.append(
            [
                row["payroll_run_id"],
                row["period_start_date"],
                row["period_end_date"],
                row["pay_date"],
                row["employee_id"],
                row["employee_name"],
                row["department"],
                row["pay_basis"],
                Decimal(str(row["authorized_rate"])),
                row["regular_hours"],
                row["overtime_hours"],
                Decimal(str(row["regular_earnings"])),
                Decimal(str(row["overtime_earnings"])),
                Decimal(str(row["bonus"])),
                Decimal(str(row["gross_wages"])),
                Decimal(str(row["statutory_withholding"])),
                Decimal(str(row["voluntary_deductions"])),
                Decimal(str(row["net_wages"])),
                Decimal(str(row["employer_payroll_tax"])),
                Decimal(str(row["employer_benefits"])),
                row["authorization_reference"],
            ]
        )
    sheet.freeze_panes = "A2"
    finalize_workbook(workbook)
    path = output_dir / f"Payroll Register Detail {year}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return {
        "request_id": "PAY-01",
        "family": "payroll",
        "path": str(path),
        "cell_map": [],
    }


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    runs = world.get("payroll_run") or []
    if not runs:
        return []
    year = str(runs[0]["period_end_date"])[:4]
    return [_detail_workbook(world, output_dir, year)]
