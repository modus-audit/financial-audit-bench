"""World-level identity checks, one small registered check per domain."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.identities import (
    cash_gl_account_id,
    dsum,
    chain_is_continuous,
    statement_ties,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.cash.rules import (
    fiscal_close_adjustments,
)

BANK_ACCOUNT_FIELDS = {
    "account_name",
    "account_type",
    "bank_account_id",
    "bank_name",
    "closing_date",
    "company_id",
    "currency_code",
    "last_four_digits",
    "masked_account_number",
    "opening_date",
    "restriction_status",
}


@REGISTRY.check(
    "check_bank_account_master",
    inputs=(
        "bank_account",
        "general_ledger_account",
        "prior_period_bank_balance",
    ),
)
def check_bank_account_master(bank_account, accounts, prior_bank):
    missing = BANK_ACCOUNT_FIELDS - bank_account.keys()
    if missing:
        raise ValueError(f"bank-account master fields missing: {sorted(missing)}")
    if (
        not bank_account["masked_account_number"].endswith(
            bank_account["last_four_digits"]
        )
        or bank_account["account_type"] != "checking"
        or bank_account["restriction_status"] not in {"restricted", "unrestricted"}
    ):
        raise ValueError(
            f"bank-account master values invalid: {bank_account['bank_account_id']}"
        )
    cash_gl_account_id(accounts)
    if (
        prior_bank["company_id"] != bank_account["company_id"]
        or prior_bank["bank_account_id"] != bank_account["bank_account_id"]
    ):
        raise ValueError(f"prior-period bank balance does not resolve: {prior_bank}")


@REGISTRY.check(
    "check_ap_integrity",
    inputs=("vendor", "vendor_invoice", "ap_payment", "general_ledger_account"),
)
def check_ap_integrity(vendors, invoices, payments, accounts):
    vendors_by_id = {row["vendor_id"]: row for row in vendors}
    invoices_by_id = {row["vendor_invoice_id"]: row for row in invoices}
    accounts_by_id = {row["gl_account_id"] for row in accounts}
    if len(invoices_by_id) != len(invoices):
        raise ValueError("vendor invoice ids are not unique")
    for row in invoices:
        if row["vendor_id"] not in vendors_by_id:
            raise ValueError(f"invoice vendor missing: {row['vendor_invoice_id']}")
    paid: dict[str, Decimal] = {}
    for row in payments:
        if (
            row["vendor_invoice_id"] not in invoices_by_id
            or row["payable_gl_account_id"] not in accounts_by_id
            or Decimal(row["amount"]) <= 0
        ):
            raise ValueError(f"AP payment does not resolve: {row['ap_payment_id']}")
        paid[row["vendor_invoice_id"]] = paid.get(
            row["vendor_invoice_id"], Decimal("0.00")
        ) + Decimal(row["amount"])
    for invoice_id, invoice in invoices_by_id.items():
        if paid.get(invoice_id, Decimal("0.00")) > Decimal(invoice["amount"]):
            raise ValueError(f"payments exceed invoice {invoice_id}")


@REGISTRY.check(
    "check_movement_window_and_bank_recs",
    inputs=(
        "cash_movement",
        "cash_book_movement",
        "bank_book_reconciliation",
        "fiscal_calendar",
    ),
)
def check_movement_window(movements, book_movements, reconciliations, calendar):
    start_date = date.fromisoformat(calendar["start_date"])
    end_date = date.fromisoformat(calendar["end_date"])
    for row in movements:
        activity_date = date.fromisoformat(row["bank_activity_date"])
        if not start_date <= activity_date <= end_date:
            raise ValueError(
                f"movement outside fiscal calendar: {row['cash_movement_id']}"
            )
    books_by_id = {row["cash_book_movement_id"]: row for row in book_movements}
    banks_by_id = {row["cash_movement_id"]: row for row in movements}
    bank_only_total = Decimal("0.00")
    for row in reconciliations:
        bank_row = banks_by_id.get(row["bank_movement_id"])
        book_row = books_by_id.get(row["cash_book_movement_id"])
        if row["status"] == "matched":
            if bank_row is None or book_row is None:
                raise ValueError(f"matched reconciliation incomplete: {row}")
            if bank_row["signed_amount"] != book_row["signed_amount"]:
                raise ValueError(f"matched amounts differ: {row['bank_movement_id']}")
        elif row["status"] == "bank_only":
            if bank_row is None or book_row is not None:
                raise ValueError(f"invalid bank-only row: {row}")
            bank_only_total += Decimal(bank_row["signed_amount"])
        elif row["status"] == "book_only":
            if book_row is None or bank_row is not None:
                raise ValueError(f"invalid book-only row: {row}")
        else:
            raise ValueError(f"unexpected reconciliation status: {row['status']}")
    if bank_only_total != Decimal("0.00"):
        raise ValueError(f"bank-only activity must net zero, got {bank_only_total}")


@REGISTRY.check(
    "check_opening_balances",
    inputs=(
        "opening_account_balance",
        "prior_period_account_balance",
        "general_ledger_account",
    ),
)
def check_opening_balances(openings, priors, accounts):
    openings_by_id = {row["gl_account_id"]: row for row in openings}
    account_ids = {row["gl_account_id"] for row in accounts}
    if set(openings_by_id) != account_ids:
        raise ValueError("opening balance population does not resolve")
    if dsum(openings, "opening_balance"):
        raise ValueError("opening balances do not sum to zero")
    close_by_id = fiscal_close_adjustments(priors, accounts)
    if sum(close_by_id.values(), Decimal("0.00")):
        raise ValueError("fiscal-close adjustment vector does not balance")
    prior_by_id = {row["gl_account_id"]: row for row in priors}
    for account_id, row in openings_by_id.items():
        expected = Decimal(prior_by_id[account_id]["closing_balance"]) + Decimal(
            close_by_id.get(account_id, Decimal("0.00"))
        )
        if Decimal(row["opening_balance"]) != expected:
            raise ValueError(f"opening balance does not resolve: {account_id}")


@REGISTRY.check(
    "check_journal_population",
    inputs=(
        "journal_entry",
        "journal_entry_line",
        "cash_book_movement",
        "general_ledger_account",
        "vendor_invoice",
        "opening_account_balance",
    ),
)
def check_journal_population(
    entries, lines, book_movements, accounts, invoices, openings
):
    cash_account = cash_gl_account_id(accounts)
    entries_by_id = {row["journal_entry_id"]: row for row in entries}
    lines_by_entry: dict[str, list[dict[str, Any]]] = {}
    for row in lines:
        lines_by_entry.setdefault(row["journal_entry_id"], []).append(row)
    if set(entries_by_id) != set(lines_by_entry):
        raise ValueError("journal entries and lines do not resolve")
    book_by_journal = {row["journal_id"]: row for row in book_movements}
    for entry_id, entry_lines in lines_by_entry.items():
        if dsum(entry_lines, "signed_amount"):
            raise ValueError(f"journal entry not balanced: {entry_id}")
        if entry_id in book_by_journal:
            cash_lines = [
                row for row in entry_lines if row["gl_account_id"] == cash_account
            ]
            if (
                len(cash_lines) != 1
                or cash_lines[0]["signed_amount"]
                != book_by_journal[entry_id]["signed_amount"]
            ):
                raise ValueError(f"cash line does not match booking: {entry_id}")
        elif entries_by_id[entry_id]["journal_type"] == "vendor_invoice":
            invoice = next(row for row in invoices if row["journal_id"] == entry_id)
            expected = {
                invoice["expense_gl_account_id"]: invoice["amount"],
                invoice["payable_gl_account_id"]: str(-Decimal(invoice["amount"])),
            }
            actual = {row["gl_account_id"]: row["signed_amount"] for row in entry_lines}
            if actual != expected:
                raise ValueError(f"invalid vendor-invoice entry: {entry_id}")
    opening_entry_ids = [
        entry_id
        for entry_id, row in entries_by_id.items()
        if row["journal_type"] == "opening_carryforward"
    ]
    if len(opening_entry_ids) != 1:
        raise ValueError("exactly one opening carryforward entry is required")
    opening_lines = lines_by_entry[opening_entry_ids[0]]
    expected_openings = {
        row["gl_account_id"]: row["opening_balance"]
        for row in openings
        if Decimal(row["opening_balance"])
    }
    if {
        row["gl_account_id"]: row["signed_amount"] for row in opening_lines
    } != expected_openings:
        raise ValueError("opening carryforward does not equal opening balances")


@REGISTRY.check(
    "check_trial_balance_ties",
    inputs=(
        "trial_balance_account",
        "general_ledger_account",
    ),
)
def check_trial_balance_ties(trial_balance, accounts):
    trial_balance_by_id = {row["gl_account_id"]: row for row in trial_balance}
    if set(trial_balance_by_id) != {row["gl_account_id"] for row in accounts}:
        raise ValueError("trial balance population does not resolve")
    for row in trial_balance:
        if Decimal(row["closing_balance"]) != Decimal(row["opening_balance"]) + Decimal(
            row["period_debit"]
        ) - Decimal(row["period_credit"]):
            raise ValueError(
                f"TB account does not roll forward: {row['gl_account_id']}"
            )
    if (
        dsum(trial_balance, "opening_balance")
        or dsum(trial_balance, "closing_balance")
        or dsum(trial_balance, "period_debit") != dsum(trial_balance, "period_credit")
    ):
        raise ValueError("trial balance totals do not balance")


@REGISTRY.check(
    "check_statement_months",
    inputs=("bank_statement_month",),
)
def check_statement_months(statements):
    if not chain_is_continuous(
        (previous["ending_balance"], current["opening_balance"])
        for previous, current in zip(statements, statements[1:], strict=False)
    ):
        raise ValueError("monthly statement chain is broken")
    for statement in statements:
        if not statement_ties(
            statement["opening_balance"],
            statement["activity_total"],
            statement["ending_balance"],
        ):
            raise ValueError(
                f"statement does not tie: {statement['statement_period_end']}"
            )


@REGISTRY.check(
    "check_subledger_tb_ties",
    inputs=(
        "trial_balance_account",
        "accounts_receivable_rollforward",
        "inventory_gl_reconciliation",
    ),
)
def check_subledger_tb_ties(trial_balance, ar_rollforwards, inventory_recons):
    closing = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    for rollforward in ar_rollforwards:
        tb = closing.get(rollforward["ar_gl_account_id"], Decimal("0"))
        if Decimal(rollforward["ending_gross_receivable"]) != tb:
            raise ValueError(
                "A/R rollforward does not tie to the trial balance: "
                f"{rollforward['ending_gross_receivable']} vs {tb}"
            )
    for recon in inventory_recons:
        tb = closing.get(recon["inventory_gl_account_id"], Decimal("0"))
        if Decimal(recon["subledger_gross_cost"]) != tb:
            raise ValueError(
                "inventory reconciliation does not tie to the trial balance: "
                f"{recon['subledger_gross_cost']} vs {tb}"
            )


# Scale-coherence checks are shared by graph execution and seed sweeps.
from financial_audit_bench.synthetic_binders.priors.graph.scale_validation import (  # noqa: E402
    check_scale_ar_population,
    check_scale_coherence,
    check_scale_inventory_population,
    check_scale_ppe,
)

REGISTRY.check(
    "check_scale_coherence",
    inputs=(
        "trial_balance_account",
        "general_ledger_account",
        "vendor_invoice",
        "company_feature_profile",
    ),
)(check_scale_coherence)

# Per-family checks are registered with their family so composition drops each
# check exactly when that family is absent.
REGISTRY.check(
    "check_scale_ar_population",
    inputs=(
        "accounts_receivable_aging",
        "trial_balance_account",
        "general_ledger_account",
        "company_feature_profile",
    ),
)(check_scale_ar_population)
REGISTRY.check(
    "check_scale_inventory_population",
    inputs=(
        "inventory_item",
        "trial_balance_account",
        "general_ledger_account",
        "company_feature_profile",
    ),
)(check_scale_inventory_population)
REGISTRY.check(
    "check_scale_ppe",
    inputs=(
        "fixed_asset",
        "trial_balance_account",
        "general_ledger_account",
        "company_feature_profile",
    ),
)(check_scale_ppe)
