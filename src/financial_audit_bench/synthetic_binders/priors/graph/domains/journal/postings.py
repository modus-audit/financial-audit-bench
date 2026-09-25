"""Opening, cash-book, and vendor-invoice journal postings."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.identities import (
    cash_gl_account_id,
)


def opening_carryforward(
    opening_balances: list[dict[str, str]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Render the fiscal-start entry from nonzero opening balances."""
    opening_date = opening_balances[0]["opening_as_of_date"]
    entry = {
        "journal_entry_id": "JOURNAL-OPENING-001",
        "journal_type": "opening_carryforward",
        "posting_date": opening_date,
        "voucher_id": "VOUCHER-OPENING-001",
    }
    lines = [
        {
            "gl_account_id": row["gl_account_id"],
            "journal_entry_id": entry["journal_entry_id"],
            "posting_type": "ledger",
            "signed_amount": row["opening_balance"],
        }
        for row in opening_balances
        if Decimal(row["opening_balance"])
    ]
    return entry, lines


def journal_entries_from_vendor_invoices(
    vendor_invoices: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Render noncash journal-entry envelopes for vendor invoices."""
    return [
        {
            "journal_entry_id": row["journal_id"],
            "journal_type": "vendor_invoice",
            "posting_date": row["posting_date"],
            "voucher_id": row["voucher_id"],
        }
        for row in vendor_invoices
    ]


def journal_entry_lines_from_vendor_invoices(
    vendor_invoices: list[dict[str, str]],
    journal_entries: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Post each vendor invoice to expense and accounts payable."""
    entry_ids = {row["journal_entry_id"] for row in journal_entries}
    rows = []
    for invoice in vendor_invoices:
        if invoice["journal_id"] not in entry_ids:
            raise ValueError("vendor invoice does not resolve a journal entry")
        rows.extend(
            (
                {
                    "gl_account_id": invoice["expense_gl_account_id"],
                    "journal_entry_id": invoice["journal_id"],
                    "posting_type": "ledger",
                    "signed_amount": invoice["amount"],
                },
                {
                    "gl_account_id": invoice["payable_gl_account_id"],
                    "journal_entry_id": invoice["journal_id"],
                    "posting_type": "ledger",
                    "signed_amount": str(-Decimal(invoice["amount"])),
                },
            )
        )
    return rows


_UNSOURCED_BOOK_KINDS = frozenset(
    {
        "cash_receipt",
        "debt_payment",
        "vendor_payment",
    }
)


def _validate_book_movement_sources(book_movements: list[dict[str, str]]) -> None:
    """Reject book rows that do not identify an admitted causal source."""
    unsupported = [
        str(row.get("cash_book_movement_id") or "<missing-id>")
        for row in book_movements
        if not str(row.get("source_kind") or "").strip()
        or row.get("source_kind") in _UNSOURCED_BOOK_KINDS
    ]
    if unsupported:
        raise ValueError(
            "cash book movements lack an admitted causal source: "
            + ", ".join(unsupported)
        )


def journal_entries_from_book_movements(
    book_movements: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Create one journal-entry envelope per source-linked cash posting."""
    _validate_book_movement_sources(book_movements)
    return [
        {
            "journal_entry_id": row["journal_id"],
            "journal_type": "bank",
            "posting_date": row["posting_date"],
            "voucher_id": row["voucher_id"],
        }
        for row in book_movements
    ]


def journal_entry_lines_from_book_movements(
    book_movements: list[dict[str, str]],
    journal_entries: list[dict[str, str]],
    cash_account: str,
) -> list[dict[str, str]]:
    """Post each source-linked cash movement to cash and its counterpart."""
    _validate_book_movement_sources(book_movements)
    entry_ids = {row["journal_entry_id"] for row in journal_entries}
    rows = []
    for book_row in book_movements:
        entry_id = book_row["journal_id"]
        if entry_id not in entry_ids:
            raise ValueError("cash posting rule does not resolve")
        rows.extend(
            (
                {
                    "gl_account_id": cash_account,
                    "journal_entry_id": entry_id,
                    "posting_type": "bank",
                    "signed_amount": book_row["signed_amount"],
                },
                {
                    "gl_account_id": book_row["counter_gl_account_id"],
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(-Decimal(book_row["signed_amount"])),
                },
            )
        )
    return rows


@REGISTRY.rule(
    "render_opening_carryforward",
    inputs=("opening_account_balance",),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def render_opening_carryforward(
    opening_balances: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entry, lines = opening_carryforward(opening_balances)
    return [entry], lines


@REGISTRY.rule(
    "post_cash_book_movement",
    inputs=("cash_book_movement", "general_ledger_account"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def post_cash_book_movement(
    book_movements: list[dict[str, Any]], accounts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries = journal_entries_from_book_movements(book_movements)
    lines = journal_entry_lines_from_book_movements(
        book_movements, entries, cash_gl_account_id(accounts)
    )
    return entries, lines


@REGISTRY.rule(
    "post_vendor_invoice",
    inputs=("vendor_invoice", "fiscal_calendar"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def post_vendor_invoice(
    vendor_invoices: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Opening-AP bills post in the prior ledger: their expense and payable credit belong
    # to the prior year and reach this year only through the opening carryforward;
    # posting them again would double-count.
    current = [
        row
        for row in vendor_invoices
        if row["posting_date"] >= str(calendar["start_date"])
    ]
    entries = journal_entries_from_vendor_invoices(current)
    lines = journal_entry_lines_from_vendor_invoices(current, entries)
    return entries, lines
