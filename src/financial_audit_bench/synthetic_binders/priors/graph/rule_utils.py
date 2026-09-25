"""Cross-domain scaffolding shared by the rule modules."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


DEPOSIT_IN_TRANSIT_SHARE = 80
DEPOSIT_IN_TRANSIT_WINDOW_DAYS = 2


def year_end_deposits_in_transit(
    receipts: list[dict[str, Any]], calendar: dict[str, Any]
) -> set[str]:
    """Ids of the last few December receipts whose bank credit lands on the first business day of January — the deposits in transit the 12/31 rec carries ."""
    end = date.fromisoformat(str(calendar["end_date"]))
    window_start = end - timedelta(days=DEPOSIT_IN_TRANSIT_WINDOW_DAYS - 1)
    candidates = sorted(
        (
            row
            for row in receipts
            if Decimal(str(row["amount"])) > 0
            and window_start <= date.fromisoformat(row["receipt_date"]) <= end
        ),
        key=lambda row: (row["receipt_date"], row["customer_cash_receipt_id"]),
    )
    if not candidates:
        return set()
    salt = "|".join(row["customer_cash_receipt_id"] for row in candidates)
    rng = random.Random(f"deposit-in-transit|{salt}")
    if rng.randrange(100) >= DEPOSIT_IN_TRANSIT_SHARE:
        return set()
    count = rng.randint(1, 3)
    return {row["customer_cash_receipt_id"] for row in candidates[-count:]}


def month_end(value: date) -> date:
    """Return the final calendar day of the given date's month."""
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
        days=1
    )


def balanced_ledger_entry(
    entry_id: str,
    journal_type: str,
    posting_date: str,
    voucher_id: str,
    debit_account: str,
    credit_account: str,
    amount: Decimal,
    description: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A journal-entry envelope plus its balanced Dr/Cr ledger line pair."""
    entry = {
        "journal_entry_id": entry_id,
        "journal_type": journal_type,
        "posting_date": posting_date,
        "voucher_id": voucher_id,
    }

    def line(gl_account_id: str, signed: Decimal) -> dict[str, Any]:
        row = {
            "gl_account_id": gl_account_id,
            "journal_entry_id": entry_id,
            "posting_type": "ledger",
            "signed_amount": str(signed),
        }
        if description is not None:
            row["description"] = description
        return row

    return entry, [line(debit_account, amount), line(credit_account, -amount)]


# ``counterparty`` is not an FK, but is defaulted with movement FKs: only residual
# checks carry a drawn payee; module-sourced movements resolve their counterparties
# through their own populations.
MOVEMENT_FK_COLUMNS = ("ap_payment_id", "counterparty")
BOOK_MOVEMENT_FK_COLUMNS = ("ap_payment_id",)


def with_movement_fks(row: dict) -> dict:
    """Every producer emits the full declared FK set; absent means None."""
    return {**{column: None for column in MOVEMENT_FK_COLUMNS}, **row}


def with_book_movement_fks(row: dict) -> dict:
    return {**{column: None for column in BOOK_MOVEMENT_FK_COLUMNS}, **row}
