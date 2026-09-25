"""Shared, source-item-driven bank reconciliation calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping


BOOK_SIDE_KINDS = frozenset({"outstanding_check", "deposit_in_transit"})
OPEN_AT_PERIOD_END = frozenset({"unresolved", "cleared_subsequently"})


def require_year_end_bank_statement(
    statements: Iterable[Mapping[str, Any]],
    *,
    bank_account_id: str,
    statement_period_end: str,
) -> Mapping[str, Any]:
    """Return the retained account's year-end statement."""
    return next(
        row
        for row in statements
        if str(row.get("bank_account_id") or "") == bank_account_id
        and str(row.get("statement_period_end") or "") == statement_period_end
    )


def statement_period_reconciling_items(
    reconciling_items: Iterable[Mapping[str, Any]],
    *,
    statement_period_end: str,
    bank_account_id: str,
) -> tuple[dict[str, Any], ...]:
    """Select open reconciling items for one account and statement period."""
    return tuple(
        sorted(
            (
                dict(row)
                for row in reconciling_items
                if str(row.get("original_statement_period_end") or "")
                == statement_period_end
                and str(row.get("bank_account_id") or "") == bank_account_id
                and row.get("status") in OPEN_AT_PERIOD_END
            ),
            key=lambda row: str(row["bank_reconciling_item_id"]),
        )
    )


@dataclass(frozen=True)
class BankReconciliationControls:
    """A bank-to-book reconciliation calculated only from source records."""

    book_side_items: tuple[dict[str, Any], ...]
    bank_side_items: tuple[dict[str, Any], ...]
    statement_period_end: str
    statement_balance: Decimal
    book_balance: Decimal
    deposits_in_transit: Decimal
    outstanding_checks: Decimal
    bank_items_not_in_books: Decimal
    adjusted_bank_balance: Decimal
    adjusted_book_balance: Decimal
    difference: Decimal

    @property
    def all_items(self) -> tuple[dict[str, Any], ...]:
        return self.book_side_items + self.bank_side_items

    @property
    def source_item_count(self) -> int:
        return len(self.all_items)

    @property
    def source_absolute_amount(self) -> Decimal:
        return sum(
            (abs(Decimal(str(item["amount"]))) for item in self.all_items),
            Decimal("0.00"),
        )


def calculate_bank_reconciliation(
    *,
    bank_account_id: str,
    statement_period_end: str,
    statement_balance: Decimal | str,
    book_balance: Decimal | str,
    reconciling_items: Iterable[dict[str, Any]],
) -> BankReconciliationControls:
    """Calculate a reconciliation without constructing a balancing plug."""

    open_items = statement_period_reconciling_items(
        reconciling_items,
        statement_period_end=statement_period_end,
        bank_account_id=bank_account_id,
    )
    book_side = tuple(
        item for item in open_items if item.get("item_kind") in BOOK_SIDE_KINDS
    )
    bank_side = tuple(
        item for item in open_items if item.get("item_kind") not in BOOK_SIDE_KINDS
    )

    statement = Decimal(str(statement_balance))
    books = Decimal(str(book_balance))
    deposits = sum(
        (
            Decimal(str(item["amount"]))
            for item in book_side
            if item.get("item_kind") == "deposit_in_transit"
        ),
        Decimal("0.00"),
    )
    checks = sum(
        (
            Decimal(str(item["amount"]))
            for item in book_side
            if item.get("item_kind") == "outstanding_check"
        ),
        Decimal("0.00"),
    )
    bank_items = sum(
        (Decimal(str(item["amount"])) for item in bank_side),
        Decimal("0.00"),
    )
    adjusted_bank = statement + deposits + checks
    adjusted_book = books + bank_items

    return BankReconciliationControls(
        book_side_items=book_side,
        bank_side_items=bank_side,
        statement_period_end=statement_period_end,
        statement_balance=statement,
        book_balance=books,
        deposits_in_transit=deposits,
        outstanding_checks=checks,
        bank_items_not_in_books=bank_items,
        adjusted_bank_balance=adjusted_bank,
        adjusted_book_balance=adjusted_book,
        difference=adjusted_bank - adjusted_book,
    )


def require_resolved_reconciliation(
    controls: BankReconciliationControls,
    *,
    bank_account_id: str,
) -> None:
    """Reject a package only after every generated source item is applied."""

    if controls.difference != 0:
        item_ids = (
            ", ".join(
                str(item.get("bank_reconciling_item_id")) for item in controls.all_items
            )
            or "none"
        )
        raise ValueError(
            "bank reconciliation remains unexplained after applying all source "
            f"items for {bank_account_id}: difference {controls.difference}; "
            f"reconciling items: {item_ids}"
        )
