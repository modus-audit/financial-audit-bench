"""Payroll posting-evidence bridges and identity validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.postings import (
    _cash_legs,
    _payroll_run_accrual_journal_ids,
    payroll_expense_posting_total,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.check(
    "payroll_register_and_evidence_identity",
    inputs=(
        "payroll_run",
        "payroll_register_line",
        "payroll_remittance",
        "journal_entry",
        "journal_entry_line",
        "cash_movement",
        "cash_book_movement",
        "fiscal_calendar",
    ),
)
def check_payroll_register_and_evidence_identity(
    runs: list[dict[str, Any]],
    register_lines: list[dict[str, Any]],
    remittances: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
    journal_lines: list[dict[str, Any]],
    cash_movements: list[dict[str, Any]],
    cash_book_movements: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> None:
    if not runs:
        if register_lines or remittances:
            raise ValueError("payroll evidence exists without a payroll run")
        return

    def unique_by(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
        result = {str(row[field]): row for row in rows}
        if len(result) != len(rows):
            raise ValueError(f"duplicate payroll identity in {field}")
        return result

    lines_by_id = unique_by(register_lines, "payroll_register_line_id")
    remittances_by_id = unique_by(remittances, "payroll_remittance_id")
    journal_ids = {str(row["journal_entry_id"]) for row in journal_entries}
    cash_ids = {str(row["cash_movement_id"]) for row in cash_movements}
    book_ids = {str(row["cash_book_movement_id"]) for row in cash_book_movements}
    books_by_id = {
        str(row["cash_book_movement_id"]): row for row in cash_book_movements
    }
    ordered_runs = sorted(runs, key=lambda row: str(row["period_end_date"]))
    run_positions = {
        str(run["payroll_run_id"]): index for index, run in enumerate(ordered_runs)
    }
    year_end = date.fromisoformat(str(calendar["end_date"]))
    all_line_references: list[str] = []
    for run in runs:
        run_id = str(run["payroll_run_id"])
        line_ids = sorted(
            line_id
            for line_id, row in lines_by_id.items()
            if str(row["payroll_run_id"]) == run_id
        )
        all_line_references.extend(line_ids)
        lines = [lines_by_id[line_id] for line_id in line_ids]
        if (
            len(lines) != int(run["employee_count"])
            or any(str(line["payroll_run_id"]) != run_id for line in lines)
            or any(
                str(line["payroll_register_line_id"])
                != f"PAYLINE-{run_id}-{line['employee_id']}"
                for line in lines
            )
        ):
            raise ValueError(f"payroll register identities do not cover {run_id}")
        gross = sum(
            (Decimal(str(line["gross_wages"])) for line in lines),
            Decimal("0.00"),
        )
        withholding = sum(
            (
                Decimal(str(line["statutory_withholding"]))
                + Decimal(str(line["voluntary_deductions"]))
                for line in lines
            ),
            Decimal("0.00"),
        )
        employer_tax = sum(
            (Decimal(str(line["employer_payroll_tax"])) for line in lines),
            Decimal("0.00"),
        )
        benefits = sum(
            (Decimal(str(line["employer_benefits"])) for line in lines),
            Decimal("0.00"),
        )
        if (
            gross != Decimal(str(run["gross_wages"]))
            or withholding != Decimal(str(run["employee_withholding"]))
            or employer_tax != Decimal(str(run["employer_payroll_tax"]))
            or benefits != Decimal(str(run.get("employer_benefits_cost") or "0"))
        ):
            raise ValueError(f"payroll register lines do not foot to {run_id}")
        suffixes = [suffix for suffix, *_rest in _cash_legs(run, year_end)]
        expected_cash_ids = {f"MOVE-{run_id}-{suffix}" for suffix in suffixes}
        expected_book_ids = {f"BOOK-{run_id}-{suffix}" for suffix in suffixes}
        if not expected_cash_ids <= cash_ids or not expected_book_ids <= book_ids:
            raise ValueError(f"payroll bridge {run_id} lacks a required cash posting")
        expected_journal_ids = _payroll_run_accrual_journal_ids(run) | {
            str(books_by_id[book_id]["journal_id"]) for book_id in expected_book_ids
        }
        if run_positions[run_id] == 0:
            expected_journal_ids.add("JOURNAL-PAYROLL-PY-ACCRUAL-REV")
        if run_positions[run_id] == len(ordered_runs) - 1:
            expected_journal_ids.add("JOURNAL-PAYROLL-YE-ACCRUAL")
        expected_journal_ids &= journal_ids
        expected_expense = (
            Decimal(str(run["gross_wages"]))
            + Decimal(str(run["employer_payroll_tax"]))
            + Decimal(str(run.get("employer_benefits_cost") or "0"))
        )
        if (
            payroll_expense_posting_total(
                [run],
                [
                    line
                    for line in journal_lines
                    if str(line["journal_entry_id"]) in expected_journal_ids
                ],
                [
                    row
                    for row in cash_book_movements
                    if str(row["cash_book_movement_id"]) in expected_book_ids
                ],
            )
            != expected_expense
        ):
            raise ValueError(f"payroll bridge {run_id} does not cover GL expense")
        run_remittance_ids = {
            remittance_id
            for remittance_id, row in remittances_by_id.items()
            if str(row["payroll_run_id"]) == run_id
        }
        if any(
            not remittances_by_id[remittance_id].get("bank_eft_reference")
            for remittance_id in run_remittance_ids
        ):
            raise ValueError(
                f"payroll run {run_id} has a remittance without EFT support"
            )
    if len(all_line_references) != len(set(all_line_references)) or set(
        all_line_references
    ) != set(lines_by_id):
        raise ValueError(
            "payroll register lines are omitted or duplicated across bridges"
        )
