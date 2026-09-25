"""Material cross-document accounting ties for the public graph."""

from __future__ import annotations

from decimal import Decimal

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.identities import (
    cash_gl_account_id,
    dsum,
)

ZERO = Decimal("0.00")


@REGISTRY.check(
    "check_bank_statement_gl_agreement",
    inputs=(
        "cash_movement",
        "cash_book_movement",
        "bank_book_reconciliation",
        "bank_reconciling_item",
        "bank_statement_month",
        "trial_balance_account",
        "general_ledger_account",
    ),
)
def check_bank_statement_gl_agreement(
    movements,
    book_movements,
    reconciliations,
    reconciling_items,
    statements,
    trial_balance,
    accounts,
):
    banks = {row["cash_movement_id"]: row for row in movements}
    books = {row["cash_book_movement_id"]: row for row in book_movements}
    if sorted(
        row["bank_movement_id"] for row in reconciliations if row["bank_movement_id"]
    ) != sorted(banks) or sorted(
        row["cash_book_movement_id"]
        for row in reconciliations
        if row["cash_book_movement_id"]
    ) != sorted(books):
        raise ValueError("bank and book cash populations are not fully reconciled")

    items = {row["bank_reconciling_item_id"]: row for row in reconciling_items}
    book_only = bank_only = ZERO
    for row in reconciliations:
        if row["status"] == "matched":
            continue
        item = items.get(f"ITEM-{row['reconciliation_id']}")
        if item is None:
            raise ValueError("an unmatched cash item lacks reconciliation support")
        amount = Decimal(item["amount"])
        if row["status"] == "book_only":
            book_only += amount
        else:
            bank_only += amount
    cash_balance = next(
        Decimal(row["closing_balance"])
        for row in trial_balance
        if row["gl_account_id"] == cash_gl_account_id(accounts)
    )
    statement_balance = (
        Decimal(statements[-1]["ending_balance"]) if statements else ZERO
    )
    if cash_balance != statement_balance + book_only - bank_only:
        raise ValueError("bank reconciliation does not foot to cash")


@REGISTRY.check(
    "check_ap_control_account_tie",
    inputs=(
        "vendor_invoice",
        "ap_payment",
        "accrued_expense",
        "accounts_payable_open_item",
        "trial_balance_account",
    ),
)
def check_ap_control_account_tie(
    invoices, payments, accrued_expenses, open_items, trial_balance
):
    paid: dict[str, Decimal] = {}
    for row in payments:
        invoice_id = row["vendor_invoice_id"]
        paid[invoice_id] = paid.get(invoice_id, ZERO) + Decimal(row["amount"])
    expected = {
        f"OPEN-{row['vendor_invoice_id']}": (
            row["payable_gl_account_id"],
            Decimal(row["amount"]) - paid.get(row["vendor_invoice_id"], ZERO),
        )
        for row in invoices
        if Decimal(row["amount"]) - paid.get(row["vendor_invoice_id"], ZERO)
    }
    actual = {
        row["ap_open_item_id"]: (
            row["payable_gl_account_id"],
            Decimal(row["open_amount"]),
        )
        for row in open_items
        if Decimal(row["open_amount"])
    }
    if actual != expected:
        raise ValueError("AP open items do not match vendor detail")
    closing = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    by_account: dict[str, Decimal] = {}
    for account, amount in expected.values():
        by_account[account] = by_account.get(account, ZERO) + amount
    if any(
        total != -closing.get(account, ZERO) for account, total in by_account.items()
    ):
        raise ValueError("AP detail does not tie to the GL")

    accruals: dict[str, Decimal] = {}
    for row in accrued_expenses:
        account = row["accrual_gl_account_id"]
        accruals[account] = accruals.get(account, ZERO) + Decimal(row["ending_balance"])
    if any(
        account in by_account or total != -closing.get(account, ZERO)
        for account, total in accruals.items()
    ):
        raise ValueError("accrual detail does not tie to the GL")


@REGISTRY.check(
    "check_fixed_asset_cross_document_tie",
    inputs=(
        "fixed_asset",
        "fixed_asset_depreciation",
        "fixed_asset_rollforward",
        "fixed_asset_gl_reconciliation",
        "trial_balance_account",
    ),
)
def check_fixed_asset_cross_document_tie(
    fixed_assets, depreciation, rollforwards, reconciliations, trial_balance
):
    if not fixed_assets:
        return
    closing = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    cost_accounts = {row["cost_gl_account_id"] for row in fixed_assets}
    accumulated_accounts = {
        row["accumulated_depreciation_gl_account_id"]
        for row in fixed_assets
        if row["accumulated_depreciation_gl_account_id"]
    }
    expense_accounts = {
        row["depreciation_expense_gl_account_id"]
        for row in fixed_assets
        if row["depreciation_expense_gl_account_id"]
    }
    current_depreciation = dsum(depreciation, "current_depreciation")
    if dsum(rollforwards, "depreciation_additions") != current_depreciation:
        raise ValueError("depreciation schedule does not tie to its rollforward")
    if sum((closing.get(account, ZERO) for account in cost_accounts), ZERO) != dsum(
        rollforwards, "ending_cost"
    ):
        raise ValueError("fixed-asset cost does not tie to the GL")
    if -sum(
        (closing.get(account, ZERO) for account in accumulated_accounts), ZERO
    ) != dsum(rollforwards, "ending_accumulated_depreciation"):
        raise ValueError("accumulated depreciation does not tie to the GL")
    if (
        sum((closing.get(account, ZERO) for account in expense_accounts), ZERO)
        != current_depreciation
    ):
        raise ValueError("depreciation expense does not tie to the GL")
    if any(
        Decimal(row["final_adjusted_difference"]) != ZERO
        or row["reconciliation_status"] != "tied_final_adjusted"
        for row in reconciliations
    ):
        raise ValueError("fixed-asset reconciliation does not tie")
