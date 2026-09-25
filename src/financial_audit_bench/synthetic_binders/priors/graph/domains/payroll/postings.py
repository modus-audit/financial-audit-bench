"""Payroll accrual, cash, and expense-reconciliation postings."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.calculations import (
    _active_fraction,
    _period_salary,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    BENEFITS_GL,
    CENT,
    WITHHOLDING_GL,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.remittances import (
    _deposit_date,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.subsequent_events import (
    opening_stub_accrual,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    with_book_movement_fks,
    with_movement_fks,
)


def _cash_legs(run: dict[str, Any], year_end: date) -> list[tuple[str, str, str, str]]:
    """(suffix, counter_account, signed_amount, date) cash legs of one run."""
    gross = Decimal(run["gross_wages"])
    withheld = Decimal(run["employee_withholding"])
    employer = Decimal(run["employer_payroll_tax"])
    benefits = Decimal(run.get("employer_benefits_cost") or "0")
    allocations = run.get("wage_allocations") or {}
    if allocations:
        legs = [
            (
                f"WAGES-{index}",
                account,
                str(
                    -(
                        Decimal(values["gross_wages"])
                        - Decimal(values["employee_withholding"])
                    )
                ),
                run["pay_date"],
            )
            for index, (account, values) in enumerate(
                sorted(allocations.items()), start=1
            )
        ]
    else:
        legs = [
            (
                "WAGES",
                run["wage_gl_account_id"],
                str(-(gross - withheld)),
                run["pay_date"],
            )
        ]
    deposit = _deposit_date(run["pay_date"])
    if deposit <= year_end:
        if withheld:
            legs.append(("WH", WITHHOLDING_GL, str(-withheld), deposit.isoformat()))
        if employer:
            legs.append(("TAX", WITHHOLDING_GL, str(-employer), deposit.isoformat()))
        # The run's benefits accrual remits with the deposit (carrier and retirement-
        # plan funding follows the payroll cycle); a December deposit after year end
        # stays in the year-end payroll liability.
        if benefits:
            legs.append(("BEN", WITHHOLDING_GL, str(-benefits), deposit.isoformat()))
    return legs


@REGISTRY.rule(
    "post_payroll_accruals",
    inputs=("employee", "payroll_run", "fiscal_calendar"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=60,
    gate="has_payroll",
)
def post_payroll_accruals(
    employees: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Non-cash payroll accruals: the gross-payroll side of every run."""
    entries, lines = [], []

    def post(entry_id, journal_type, posting_date, debit_account, amount):
        entries.append(
            {
                "journal_entry_id": entry_id,
                "journal_type": journal_type,
                "posting_date": posting_date,
                "voucher_id": entry_id.replace("JOURNAL-", "V"),
            }
        )
        lines.extend(
            (
                {
                    "gl_account_id": debit_account,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(amount),
                },
                {
                    "gl_account_id": WITHHOLDING_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(-amount),
                },
            )
        )

    for run in runs:
        wage_allocations = run.get("wage_allocations") or {}
        if wage_allocations:
            for index, (account, values) in enumerate(
                sorted(wage_allocations.items()), start=1
            ):
                withheld = Decimal(values["employee_withholding"])
                if withheld:
                    post(
                        f"JOURNAL-{run['payroll_run_id']}-WH-{index:02d}-ACCRUAL",
                        "payroll_withholding",
                        run["posting_date"],
                        account,
                        withheld,
                    )
        else:
            withheld = Decimal(run["employee_withholding"])
            if withheld:
                post(
                    f"JOURNAL-{run['payroll_run_id']}-WH-ACCRUAL",
                    "payroll_withholding",
                    run["posting_date"],
                    run["wage_gl_account_id"],
                    withheld,
                )
        tax_allocations = run.get("employer_tax_allocations") or {}
        if not tax_allocations:
            tax_allocations = {
                run["payroll_tax_expense_gl_account_id"]: run["employer_payroll_tax"]
            }
        for index, (account, amount) in enumerate(
            sorted(tax_allocations.items()), start=1
        ):
            employer = Decimal(amount)
            if employer:
                post(
                    f"JOURNAL-{run['payroll_run_id']}-TAX-{index:02d}-ACCRUAL",
                    "payroll_tax_accrual",
                    run["posting_date"],
                    account,
                    employer,
                )
        benefit_allocations = run.get("benefit_allocations") or {}
        if not benefit_allocations:
            benefit_allocations = {
                BENEFITS_GL: run.get("employer_benefits_cost") or "0"
            }
        for index, (account, amount) in enumerate(
            sorted(benefit_allocations.items()), start=1
        ):
            benefits = Decimal(amount)
            if benefits:
                post(
                    f"JOURNAL-{run['payroll_run_id']}-BEN-{index:02d}-ACCRUAL",
                    "benefits_accrual",
                    run["posting_date"],
                    account,
                    benefits,
                )
    # Opening stub relief: the accrual the client posted at the prior 12/31 for days
    # worked after the prior year's final period end (the year-end stub one year
    # earlier; the prior-balance pin carries the same amount into the opening TB). The
    # first run's pay date relieves it — Dr accrued payroll / Cr wages — so current-year
    # wage expense carries the current year's days only.
    if runs:
        opening = opening_stub_accrual(employees, int(calendar["fiscal_year"]))
        if opening > 0:
            post(
                "JOURNAL-PAYROLL-PY-ACCRUAL-REV",
                "payroll_accrual_reversal",
                runs[0]["pay_date"],
                runs[0]["wage_gl_account_id"],
                -opening,
            )
    # Year-end stub accrual: weekly/biweekly calendars leave a few worked days between
    # the last period end and 12/31 (semi-monthly and monthly periods end exactly at
    # year end and skip this). The estimate is the register-supported daily rate — no
    # reclassification JEs beyond it.
    if runs:
        year_end = date.fromisoformat(calendar["end_date"])
        last_period_end = max(
            date.fromisoformat(run["period_end_date"]) for run in runs
        )
        stub_days = (year_end - last_period_end).days
        if stub_days > 0:
            year_days = (year_end - date.fromisoformat(calendar["start_date"])).days + 1
            # Per-employee stub: days each employee actually worked between the last
            # period end and 12/31 at their year-end salary rate — a December hire
            # accrues from the hire date, a December leaver stops at termination, so the
            # roster edges never overstate the year-end payroll liability.
            stub_start = last_period_end + timedelta(days=1)
            accrued = Decimal("0")
            for employee in employees:
                fraction = _active_fraction(employee, (stub_start, year_end))
                if fraction <= 0:
                    continue
                salary = _period_salary(employee, stub_start)
                accrued += salary * fraction * stub_days / year_days
            accrued = accrued.quantize(CENT)
            if accrued > 0:
                post(
                    "JOURNAL-PAYROLL-YE-ACCRUAL",
                    "payroll_accrual",
                    year_end.isoformat(),
                    runs[0]["wage_gl_account_id"],
                    accrued,
                )
    return entries, lines


@REGISTRY.rule(
    "expand_payroll_movements",
    inputs=("bank_account", "payroll_run", "fiscal_calendar"),
    outputs="cash_movement",
    contribution_priority=60,
    gate="has_payroll",
)
def movements_from_payroll_runs(
    bank_account: dict[str, str],
    runs: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, str | None]]:
    """Bank movements: net-pay funding on the pay date, EFTPS tax deposit
    mid-following-month (December's deposit stays accrued)."""
    year_end = date.fromisoformat(calendar["end_date"])
    return [
        with_movement_fks(
            {
                "bank_account_id": bank_account["bank_account_id"],
                "bank_activity_date": leg_date,
                "cash_movement_id": f"MOVE-{run['payroll_run_id']}-{suffix}",
                "signed_amount": amount,
                "transaction_class": "payroll_disbursement",
            }
        )
        for run in runs
        for suffix, _account, amount, leg_date in _cash_legs(run, year_end)
    ]


@REGISTRY.rule(
    "post_payroll_movements",
    inputs=("bank_account", "payroll_run", "fiscal_calendar"),
    outputs="cash_book_movement",
    contribution_priority=60,
    gate="has_payroll",
)
def book_movements_from_payroll_runs(
    bank_account: dict[str, str],
    runs: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, str | None]]:
    """Post each payroll cash leg to cash and its counter account."""
    year_end = date.fromisoformat(calendar["end_date"])
    return [
        with_book_movement_fks(
            {
                "bank_account_id": bank_account["bank_account_id"],
                "cash_book_movement_id": f"BOOK-{run['payroll_run_id']}-{suffix}",
                "counter_gl_account_id": account,
                "journal_id": f"JOURNAL-{run['payroll_run_id']}-{suffix}",
                "posting_date": leg_date,
                "signed_amount": amount,
                "source_kind": "payroll",
                "voucher_id": f"VOUCHER-{run['payroll_run_id']}-{suffix}",
            }
        )
        for run in runs
        for suffix, account, amount, leg_date in _cash_legs(run, year_end)
    ]


def _payroll_run_accrual_journal_ids(run: dict[str, Any]) -> set[str]:
    run_id = str(run["payroll_run_id"])
    expected: set[str] = set()
    wage_allocations = run.get("wage_allocations") or {}
    if wage_allocations:
        for index, (_account, values) in enumerate(
            sorted(wage_allocations.items()), start=1
        ):
            if Decimal(str(values["employee_withholding"])):
                expected.add(f"JOURNAL-{run_id}-WH-{index:02d}-ACCRUAL")
    elif Decimal(str(run.get("employee_withholding") or "0")):
        expected.add(f"JOURNAL-{run_id}-WH-ACCRUAL")
    tax_allocations = run.get("employer_tax_allocations") or {
        run["payroll_tax_expense_gl_account_id"]: run["employer_payroll_tax"]
    }
    for index, (_account, amount) in enumerate(
        sorted(tax_allocations.items()), start=1
    ):
        if Decimal(str(amount)):
            expected.add(f"JOURNAL-{run_id}-TAX-{index:02d}-ACCRUAL")
    benefit_allocations = run.get("benefit_allocations") or {
        BENEFITS_GL: run.get("employer_benefits_cost") or "0"
    }
    for index, (_account, amount) in enumerate(
        sorted(benefit_allocations.items()), start=1
    ):
        if Decimal(str(amount)):
            expected.add(f"JOURNAL-{run_id}-BEN-{index:02d}-ACCRUAL")
    return expected


def payroll_expense_posting_total(
    runs: list[dict[str, Any]],
    journal_lines: list[dict[str, Any]],
    cash_book_movements: list[dict[str, Any]],
) -> Decimal:
    """Reconcile payroll expense through exact source-document identities."""
    expected_book_ids: set[str] = set()
    expected_accrual_ids: set[str] = set()
    for run in runs:
        run_id = str(run["payroll_run_id"])
        wage_allocations = run.get("wage_allocations") or {}
        if wage_allocations:
            expected_book_ids.update(
                f"BOOK-{run_id}-WAGES-{index}"
                for index, _item in enumerate(sorted(wage_allocations.items()), start=1)
            )
        else:
            expected_book_ids.add(f"BOOK-{run_id}-WAGES")
        expected_accrual_ids.update(_payroll_run_accrual_journal_ids(run))
    wage_book_rows = [
        row
        for row in cash_book_movements
        if str(row.get("cash_book_movement_id") or "") in expected_book_ids
    ]
    wage_journal_ids = {str(row["journal_id"]) for row in wage_book_rows}
    expected_journal_ids = expected_accrual_ids | wage_journal_ids
    journal_total = sum(
        (
            Decimal(str(line["signed_amount"]))
            for line in journal_lines
            if str(line.get("journal_entry_id") or "") in expected_journal_ids
            and Decimal(str(line["signed_amount"])) > 0
        ),
        Decimal("0.00"),
    )
    return journal_total
