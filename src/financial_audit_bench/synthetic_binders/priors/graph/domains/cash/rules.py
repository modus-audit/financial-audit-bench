"""Bank activity, book cash, openings, reconciliations, and statements."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    add_business_days,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    month_end,
)

REGISTRY.sample(
    "bank_account",
    inputs=("company_context",),
    rule_name="bank_account_population",
)


REGISTRY.sample(
    "bank_account_signer",
    inputs=("bank_account",),
    rule_name="sample_bank_account_signers",
)
REGISTRY.sample(
    "prior_period_bank_balance",
    inputs=(
        "bank_account",
        "fixed_asset",
    ),
)
# Reconcile movements emitted by operational domains; do not invent standalone entries.


@REGISTRY.rule(
    "fiscal_year_window",
    inputs=("company_context",),
    outputs="fiscal_calendar",
)
def derive_fiscal_calendar(company: dict[str, Any]) -> dict[str, str | int]:
    """Build one fiscal-year window from the provided year and month-day rule."""
    target_year = int(company["target_fiscal_year"])
    month, day = (int(part) for part in company["fiscal_year_end"].split("-"))
    end_date = date(target_year, month, day)
    start_date = date(target_year - 1, month, day) + timedelta(days=1)
    return {
        "end_date": end_date.isoformat(),
        "fiscal_calendar_id": f"CALENDAR-{company['case_id']}",
        "fiscal_year": target_year,
        "start_date": start_date.isoformat(),
    }


def fiscal_close_adjustments(
    prior_balances: list[dict[str, str]],
    general_ledger_accounts: list[dict[str, Any]],
) -> dict[str, Decimal]:
    """Return the balanced vector that closes temporary accounts to equity."""
    accounts = {row["gl_account_id"]: row for row in general_ledger_accounts}
    adjustments: dict[str, Decimal] = {}
    for prior in prior_balances:
        account = accounts[prior["gl_account_id"]]
        if account["fiscal_close_behavior"] != "close_to_equity":
            continue
        destination_id = account["close_to_gl_account_id"]
        if not destination_id or destination_id not in accounts:
            raise ValueError("fiscal-close destination does not resolve")
        source_adjustment = -Decimal(prior["closing_balance"])
        adjustments[prior["gl_account_id"]] = source_adjustment
        adjustments[destination_id] = (
            adjustments.get(destination_id, Decimal("0.00")) - source_adjustment
        )
    return {account_id: amount for account_id, amount in adjustments.items() if amount}


@REGISTRY.rule(
    "derive_account_opening_balances",
    inputs=(
        "prior_period_account_balance",
        "general_ledger_account",
        "fiscal_calendar",
    ),
    outputs="opening_account_balance",
)
def derive_opening_account_balances(
    prior_balances: list[dict[str, str]],
    general_ledger_accounts: list[dict[str, Any]],
    calendar: dict[str, str | int],
) -> list[dict[str, str]]:
    """Carry the prior account closings into the current fiscal-year opening."""
    account_ids = {row["gl_account_id"] for row in general_ledger_accounts}
    if {row["gl_account_id"] for row in prior_balances} != account_ids:
        raise ValueError("prior-period account population does not resolve")
    adjustment_by_account = fiscal_close_adjustments(
        prior_balances, general_ledger_accounts
    )
    return [
        {
            "gl_account_id": row["gl_account_id"],
            "opening_as_of_date": str(calendar["start_date"]),
            "opening_balance": str(
                Decimal(row["closing_balance"])
                + adjustment_by_account.get(row["gl_account_id"], Decimal("0.00"))
            ),
        }
        for row in prior_balances
    ]


@REGISTRY.rule(
    "reconcile_bank_book_movements",
    inputs=("cash_movement", "cash_book_movement"),
    outputs="bank_book_reconciliation",
)
def reconcile_bank_book(
    movements: list[dict[str, Any]],
    book_movements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair bank and book movements by amount, in date order."""
    # AP rows carry an exact causal key. Match them by payment id before the amount/date
    # fallback so an equal-dollar unrelated bank movement cannot consume a year-end
    # outstanding check's book row.
    banks_by_payment = {
        row["ap_payment_id"]: row for row in movements if row.get("ap_payment_id")
    }
    books_by_payment = {
        row["ap_payment_id"]: row for row in book_movements if row.get("ap_payment_id")
    }
    rows = []
    for payment_id in sorted(set(banks_by_payment) | set(books_by_payment)):
        bank = banks_by_payment.get(payment_id)
        book = books_by_payment.get(payment_id)
        rows.append(
            {
                "bank_movement_id": bank["cash_movement_id"] if bank else None,
                "cash_book_movement_id": (
                    book["cash_book_movement_id"] if book else None
                ),
                "reconciliation_id": f"RECON-{payment_id}",
                "status": "matched"
                if bank and book
                else "bank_only"
                if bank
                else "book_only",
            }
        )
    movements = [row for row in movements if not row.get("ap_payment_id")]
    book_movements = [row for row in book_movements if not row.get("ap_payment_id")]

    banks_by_key: dict[tuple[int, int, str], list[dict[str, str]]] = {}
    books_by_key: dict[tuple[int, int, str], list[dict[str, str]]] = {}
    for row in movements:
        activity_date = date.fromisoformat(row["bank_activity_date"])
        key = activity_date.year, activity_date.month, row["signed_amount"]
        banks_by_key.setdefault(key, []).append(row)
    for row in book_movements:
        posting_date = date.fromisoformat(row["posting_date"])
        key = posting_date.year, posting_date.month, row["signed_amount"]
        books_by_key.setdefault(key, []).append(row)

    leftover_banks: dict[str, list[dict[str, str]]] = {}
    leftover_books: dict[str, list[dict[str, str]]] = {}
    for key in sorted(set(banks_by_key) | set(books_by_key)):
        banks = sorted(
            banks_by_key.get(key, []), key=lambda row: row["bank_activity_date"]
        )
        books = sorted(books_by_key.get(key, []), key=lambda row: row["posting_date"])
        # Disbursements honor the same causality guard as the cross-month pass: a check
        # clears AFTER it is booked. The positional zip used to pair an early bank
        # clearing with a later recurring check booked days before the cutoff, consuming
        # the book row whose absence from the bank IS the outstanding check (latent
        # defect surfaced by the two-check December float).
        if Decimal(key[2]) < 0:
            remaining_books = list(books)
            unmatched_banks = []
            for bank in banks:
                book = next(
                    (
                        row
                        for row in remaining_books
                        if row["posting_date"] <= bank["bank_activity_date"]
                    ),
                    None,
                )
                if book is None:
                    unmatched_banks.append(bank)
                    continue
                remaining_books.remove(book)
                rows.append(
                    {
                        "bank_movement_id": bank["cash_movement_id"],
                        "cash_book_movement_id": book["cash_book_movement_id"],
                        "reconciliation_id": f"RECON-{bank['cash_movement_id']}",
                        "status": "matched",
                    }
                )
            leftover_banks.setdefault(key[2], []).extend(unmatched_banks)
            leftover_books.setdefault(key[2], []).extend(remaining_books)
            continue
        paired = min(len(banks), len(books))
        rows.extend(
            {
                "bank_movement_id": bank["cash_movement_id"],
                "cash_book_movement_id": book["cash_book_movement_id"],
                "reconciliation_id": f"RECON-{bank['cash_movement_id']}",
                "status": "matched",
            }
            for bank, book in zip(banks[:paired], books[:paired])
        )
        leftover_banks.setdefault(key[2], []).extend(banks[paired:])
        leftover_books.setdefault(key[2], []).extend(books[paired:])
    for amount in sorted(set(leftover_banks) | set(leftover_books)):
        banks = sorted(
            leftover_banks.get(amount, []), key=lambda row: row["bank_activity_date"]
        )
        books = sorted(
            leftover_books.get(amount, []), key=lambda row: row["posting_date"]
        )
        for bank in banks:
            book = next(
                (
                    row
                    for row in books
                    if row["posting_date"] <= bank["bank_activity_date"]
                ),
                None,
            )
            if book is None:
                rows.append(
                    {
                        "bank_movement_id": bank["cash_movement_id"],
                        "cash_book_movement_id": None,
                        "reconciliation_id": f"RECON-{bank['cash_movement_id']}",
                        "status": "bank_only",
                    }
                )
                continue
            books.remove(book)
            rows.append(
                {
                    "bank_movement_id": bank["cash_movement_id"],
                    "cash_book_movement_id": book["cash_book_movement_id"],
                    "reconciliation_id": f"RECON-{bank['cash_movement_id']}",
                    "status": "matched",
                }
            )
        rows.extend(
            {
                "bank_movement_id": None,
                "cash_book_movement_id": row["cash_book_movement_id"],
                "reconciliation_id": f"RECON-{row['cash_book_movement_id']}",
                "status": "book_only",
            }
            for row in books
        )
    return rows


@REGISTRY.rule(
    "derive_bank_reconciling_items",
    inputs=(
        "bank_book_reconciliation",
        "cash_movement",
        "cash_book_movement",
        "ap_payment",
        "fiscal_calendar",
    ),
    outputs="bank_reconciling_item",
)
def derive_reconciling_items(
    reconciliations: list[dict[str, Any]],
    movements: list[dict[str, Any]],
    book_movements: list[dict[str, Any]],
    ap_payments: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn unmatched reconciliation sides into durable cash-control items."""
    banks = {row["cash_movement_id"]: row for row in movements}
    books = {row["cash_book_movement_id"]: row for row in book_movements}
    rows = []
    for reconciliation in reconciliations:
        if reconciliation["status"] == "matched":
            continue
        bank = banks.get(reconciliation["bank_movement_id"])
        book = books.get(reconciliation["cash_book_movement_id"])
        source = bank or book
        source_date = date.fromisoformat(
            bank["bank_activity_date"] if bank else book["posting_date"]
        )
        statement_end = month_end(source_date)
        amount = source["signed_amount"]
        item_kind = "bank_only"
        if book and not bank:
            item_kind = (
                "outstanding_check" if Decimal(amount) < 0 else "deposit_in_transit"
            )
        rows.append(
            {
                "amount": amount,
                "bank_account_id": source["bank_account_id"],
                "bank_date": bank["bank_activity_date"] if bank else None,
                "bank_movement_id": bank["cash_movement_id"] if bank else None,
                "bank_reconciling_item_id": f"ITEM-{reconciliation['reconciliation_id']}",
                "book_date": book["posting_date"] if book else None,
                "book_movement_id": (book["cash_book_movement_id"] if book else None),
                "item_kind": item_kind,
                "management_explanation": None,
                "original_statement_period_end": statement_end.isoformat(),
                # Reconciliations are prepared and reviewed on business days.
                "prepared_date": add_business_days(statement_end, 1).isoformat(),
                "reviewed_date": add_business_days(statement_end, 2).isoformat(),
                "status": "unresolved",
                "subsequent_clearing_date": None,
                "subsequent_clearing_reference": None,
            }
        )
    for row in rows:
        if row["item_kind"] != "bank_only" or row["status"] != "unresolved":
            continue
        matches = [
            other
            for other in rows
            if other is not row
            and other["item_kind"] == "bank_only"
            and other["bank_date"] == row["bank_date"]
            and Decimal(other["amount"]) == -Decimal(row["amount"])
        ]
        if len(matches) == 1:
            row["management_explanation"] = "Offsetting same-day bank-only pair"
            row["status"] = "resolved_net_zero_pair"
            row["subsequent_clearing_date"] = row["bank_date"]
            row["subsequent_clearing_reference"] = matches[0][
                "bank_reconciling_item_id"
            ]
    # Bank-only debit/credit reversals may post on different days (for example, a
    # rejected ACH and its reversal). Pair unique opposite amounts within the same month
    # so they do not remain open all year or appear on the December 31 reconciliation as
    # two contradictory items.
    for row in rows:
        if row["item_kind"] != "bank_only" or row["status"] != "unresolved":
            continue
        row_date = date.fromisoformat(row["bank_date"])
        matches = [
            other
            for other in rows
            if other is not row
            and other["item_kind"] == "bank_only"
            and other["status"] == "unresolved"
            and Decimal(other["amount"]) == -Decimal(row["amount"])
            and abs((date.fromisoformat(other["bank_date"]) - row_date).days) <= 31
        ]
        if len(matches) != 1:
            continue
        other = matches[0]
        for current, counterpart in ((row, other), (other, row)):
            current["management_explanation"] = "Offsetting bank reversal"
            current["status"] = "resolved_net_zero_pair"
            current["subsequent_clearing_date"] = counterpart["bank_date"]
            current["subsequent_clearing_reference"] = counterpart[
                "bank_reconciling_item_id"
            ]
    # Outstanding checks whose payment cleared after year end carry the
    # known subsequent clearing (the CASH-07 population).
    payments_by_id = {row["ap_payment_id"]: row for row in ap_payments}
    books_by_id = {
        row["cash_book_movement_id"]: row
        for row in book_movements
        if row.get("ap_payment_id")
    }
    for item in rows:
        book = books_by_id.get(item.get("book_movement_id"))
        if book is None:
            continue
        payment = payments_by_id.get(book["ap_payment_id"])
        if payment is None or not payment.get("cleared_date"):
            continue
        if str(payment["cleared_date"]) > str(item["book_date"]):
            item["subsequent_clearing_date"] = payment["cleared_date"]
            item["subsequent_clearing_reference"] = payment["payment_reference"]
            item["status"] = "cleared_subsequently"
    # Any causal cutoff item not already linked to an AP settlement receives
    # its subsequent-period bank reference.
    year_end = date.fromisoformat(str(calendar["end_date"]))
    for item in rows:
        if item["status"] != "unresolved":
            continue
        if item["item_kind"] == "deposit_in_transit":
            movement_id = str(item["book_movement_id"]).replace("BOOK", "MOVE", 1)
            item["subsequent_clearing_date"] = add_business_days(
                year_end, 1
            ).isoformat()
            item["subsequent_clearing_reference"] = movement_id
            item["status"] = "cleared_subsequently"
            continue
        if item["item_kind"] != "outstanding_check":
            continue
        movement_id = str(item["book_movement_id"]).replace("BOOK", "MOVE", 1)
        item["subsequent_clearing_date"] = add_business_days(year_end, 3).isoformat()
        item["subsequent_clearing_reference"] = movement_id
        item["status"] = "cleared_subsequently"
    return rows


@REGISTRY.rule(
    "statement_period_rollforward",
    inputs=(
        "bank_account",
        "fiscal_calendar",
        "prior_period_bank_balance",
        "cash_movement",
    ),
    outputs="bank_statement_month",
)
def roll_forward_monthly_statements(
    bank_account: dict[str, Any],
    calendar: dict[str, Any],
    prior_bank_balance: dict[str, str],
    movements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Roll each calendar month from the opening balance and movement rows."""
    start_date = date.fromisoformat(calendar["start_date"])
    end_date = date.fromisoformat(calendar["end_date"])
    movement_totals: dict[tuple[int, int], Decimal] = {}
    for row in movements:
        movement_date = date.fromisoformat(row["bank_activity_date"])
        key = movement_date.year, movement_date.month
        movement_totals[key] = movement_totals.get(key, Decimal("0.00")) + Decimal(
            row["signed_amount"]
        )

    statements = []
    balance = Decimal(prior_bank_balance["ending_balance"])
    current_date = start_date.replace(day=1)
    while current_date <= end_date:
        period_end = min(month_end(current_date), end_date)
        activity_total = movement_totals.get(
            (current_date.year, current_date.month), Decimal("0.00")
        )
        ending_balance = balance + activity_total
        statement_id = (
            f"BANK-STMT-{bank_account['bank_account_id']}-{period_end.isoformat()}"
        )
        statements.append(
            {
                "bank_statement_id": statement_id,
                "activity_total": str(activity_total),
                "bank_account_id": bank_account["bank_account_id"],
                "ending_balance": str(ending_balance),
                "opening_balance": str(balance),
                "statement_period_end": period_end.isoformat(),
                "statement_period_start": current_date.isoformat(),
            }
        )
        balance = ending_balance
        current_date = month_end(current_date) + timedelta(days=1)
    return statements


@REGISTRY.check(
    "validate_cash_control_records",
    inputs=(
        "bank_account",
        "bank_account_signer",
        "bank_reconciling_item",
        "bank_statement_month",
    ),
)
def validate_cash_control_records(
    bank_account: dict[str, Any],
    signers: list[dict[str, Any]],
    reconciling_items: list[dict[str, str | None]],
    statements: list[dict[str, str]],
) -> None:
    """Validate signer, inactive-transfer, and reconciliation control records."""
    if not signers or any(
        row["bank_account_id"] != bank_account["bank_account_id"]
        or row["authorization_status"] != "active"
        for row in signers
    ):
        raise ValueError("bank-account signer relationship is invalid")
    if any(
        row["bank_account_id"] != bank_account["bank_account_id"]
        or not row["bank_movement_id"]
        and not row["book_movement_id"]
        or Decimal(row["amount"]) == 0
        for row in reconciling_items
    ):
        raise ValueError("bank reconciling item is invalid")
    statement_ids: set[str] = set()
    for row in statements:
        statement_id = str(row["bank_statement_id"])
        if statement_id in statement_ids:
            raise ValueError(f"duplicate bank statement source ID: {statement_id}")
        statement_ids.add(statement_id)
