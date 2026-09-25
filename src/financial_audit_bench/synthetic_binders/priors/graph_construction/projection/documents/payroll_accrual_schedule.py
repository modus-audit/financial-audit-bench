"""Year-end accrued payroll and payroll-liability reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
    titled_doc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
    fmt_money,
)


def _applicable(world: World) -> bool:
    return bool(world.get("payroll_run"))


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    runs = sorted(world["payroll_run"], key=lambda row: row["period_end_date"])
    last = runs[-1]
    year_end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    last_end = date.fromisoformat(last["period_end_date"])
    accrual_lines = [
        row
        for row in world.get("journal_entry_line") or []
        if row["journal_entry_id"] == "JOURNAL-PAYROLL-YE-ACCRUAL"
    ]
    gross_accrual = sum(
        (
            Decimal(str(row["signed_amount"]))
            for row in accrual_lines
            if Decimal(str(row["signed_amount"])) > 0
        ),
        Decimal("0"),
    )
    subsequent_remittances = [
        row
        for row in world.get("payroll_remittance") or []
        if date.fromisoformat(str(row["settlement_date"])) > year_end
    ]
    employee_withholding = sum(
        (
            Decimal(str(row["amount"]))
            for row in subsequent_remittances
            if row["remittance_type"] == "employee_tax_withholding"
        ),
        Decimal("0.00"),
    )
    employee_deductions = sum(
        (
            Decimal(str(row["amount"]))
            for row in subsequent_remittances
            if row["remittance_type"] == "employee_voluntary_deduction"
        ),
        Decimal("0.00"),
    )
    employer_taxes = sum(
        (
            Decimal(str(row["amount"]))
            for row in subsequent_remittances
            if row["remittance_type"] == "employer_payroll_tax"
        ),
        Decimal("0.00"),
    )
    benefits = sum(
        (
            Decimal(str(row["amount"]))
            for row in subsequent_remittances
            if row["remittance_type"] == "employer_benefit"
        ),
        Decimal("0.00"),
    )
    liability = next(
        (
            Decimal(str(row["closing_balance"]))
            for row in world["trial_balance_account"]
            if row["gl_account_id"] == "GL-WH-PAYABLE-001"
        ),
        Decimal("0"),
    )
    doc = titled_doc("YEAR-END PAYROLL ACCRUAL RECONCILIATION", world)
    doc.pair("Last regular payroll period end", fmt_date(last["period_end_date"]))
    doc.pair("Last regular payroll pay date", fmt_date(last["pay_date"]))
    doc.pair("Year end", fmt_date(year_end.isoformat()))
    doc.pair("Earned but unpaid calendar days", str((year_end - last_end).days))
    doc.line()
    doc.tie("gross_wages_accrued", "Gross wages accrued", fmt_money(gross_accrual))
    doc.tie(
        "employee_withholdings_outstanding",
        "Employee withholdings awaiting remittance",
        fmt_money(employee_withholding),
    )
    doc.tie(
        "employer_taxes_outstanding",
        "Employer payroll taxes awaiting remittance",
        fmt_money(employer_taxes),
    )
    doc.tie(
        "employee_deductions_outstanding",
        "Employee voluntary deductions awaiting remittance",
        fmt_money(employee_deductions),
    )
    doc.tie(
        "benefits_outstanding",
        "Employer benefits awaiting provider remittance",
        fmt_money(benefits),
    )
    doc.tie(
        "payroll_liability",
        "Payroll liabilities per general ledger",
        fmt_money(-liability),
    )
    doc.tie(
        "payroll_liability_reconciliation_difference",
        "Reconciliation difference",
        fmt_money(
            -liability
            - gross_accrual
            - employee_withholding
            - employee_deductions
            - employer_taxes
            - benefits
        ),
    )
    subsequent = next(
        (
            row
            for row in world.get("payroll_subsequent_event") or []
            if row["event_type"] == "subsequent_regular_payroll_and_accrual_reversal"
        ),
        None,
    )
    if subsequent:
        doc.line()
        doc.pair(
            "Subsequent payroll period end", fmt_date(subsequent["period_end_date"])
        )
        doc.pair(
            "Subsequent payroll settlement", fmt_date(subsequent["settlement_date"])
        )
        doc.pair("Accrual reversal reference", subsequent["reversal_reference"])
        doc.pair("Payroll bank/EFT reference", subsequent["bank_eft_reference"])
        doc.pair("Subsequent payroll support", "PAY-05")
    if subsequent_remittances:
        doc.pair(
            "Tax and benefit settlement support",
            "PAY-04 settlement and remittance report",
        )
    doc.line()
    doc.wrapped(
        "The gross-wage stub accrual covers service after the last regular "
        "payroll period through year end. Payroll tax deposits and other "
        "withholdings outstanding at year end remain in the payroll-liability "
        "account until the next scheduled remittance."
    )
    doc.line("Prepared by: Payroll Manager")
    doc.line("Reviewed by: Controller")
    path = (
        output_dir / "payroll" / f"Payroll Accrual Reconciliation {year_end.year}.txt"
    )
    return [entry("DOC-PAYROLL-ACCRUAL", "payroll", path, doc.write(path))]
