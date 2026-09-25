"""Accrued-expense journal posting and balance validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    balanced_ledger_entry,
)


@REGISTRY.rule(
    "post_accrued_expenses",
    inputs=("accrued_expense", "fiscal_calendar"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def post_accrued_expenses(
    accrued_expenses: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries, lines = [], []
    for accrual in accrued_expenses:
        # Unbooked accruals (client-error worlds) stay off the client GL;
        # the final adjusting journal records them.
        if accrual.get("book_layer") == "final_adjusted":
            continue
        amount = Decimal(accrual["addition_amount"])
        if not amount:
            continue
        entry, pair = balanced_ledger_entry(
            accrual["journal_entry_id"],
            "accrued_expense",
            accrual["posting_date"],
            f"VACC-{accrual['accrued_expense_id']}",
            accrual["expense_gl_account_id"],
            accrual["accrual_gl_account_id"],
            amount,
        )
        entries.append(entry)
        lines.extend(pair)
    return entries, lines


@REGISTRY.check(
    "validate_accrual_postings",
    inputs=("accrued_expense", "journal_entry", "journal_entry_line"),
)
def validate_accrual_postings(
    accrued_expenses: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
    journal_entry_lines: list[dict[str, Any]],
) -> None:
    entry_ids = {row["journal_entry_id"] for row in journal_entries}
    lines_by_entry: dict[str, Decimal] = {}
    for line in journal_entry_lines:
        lines_by_entry[line["journal_entry_id"]] = lines_by_entry.get(
            line["journal_entry_id"], Decimal("0")
        ) + Decimal(line["signed_amount"])
    for accrual in accrued_expenses:
        if accrual.get("book_layer") == "final_adjusted":
            continue
        if not Decimal(accrual["addition_amount"]):
            continue
        entry_id = accrual["journal_entry_id"]
        if entry_id not in entry_ids:
            raise ValueError(f"accrual {accrual['accrued_expense_id']} has no entry")
        if lines_by_entry.get(entry_id) != Decimal("0"):
            raise ValueError(f"accrual entry {entry_id} does not balance")
