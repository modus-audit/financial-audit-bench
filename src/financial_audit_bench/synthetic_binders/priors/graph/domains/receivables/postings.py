"""Receivables revenue, cash-receipt, and NSF postings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    year_end_deposits_in_transit,
    with_book_movement_fks,
    with_movement_fks,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "recognize_ar_revenue",
    inputs=(
        "customer_invoice",
        "customer_invoice_line",
        "customer_credit_adjustment",
        "fiscal_calendar",
    ),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=70,
    gate="has_accounts_receivable",
)
def recognize_ar_revenue(
    invoices: list[dict[str, Any]],
    invoice_lines: list[dict[str, Any]],
    credits: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Balanced non-cash entries: Dr AR / Cr revenue per invoice, reversed by each credit memo (Dr revenue / Cr AR). Invoices posted before the fiscal year are opening-balance lineage (prior-year AR continuity): their revenue closed to capital last year, so only their collections post in the current ledger."""
    start = date.fromisoformat(str(calendar["start_date"]))
    end = date.fromisoformat(str(calendar["end_date"]))
    invoices = [
        row
        for row in invoices
        if start <= date.fromisoformat(row["posting_date"]) <= end
    ]
    credits = [
        row
        for row in credits
        if start <= date.fromisoformat(row["posting_date"]) <= end
    ]
    entries, lines = [], []

    def post(
        entry_id,
        journal_type,
        posting_date,
        ar_account,
        revenue_account,
        amount,
        tax_amount=Decimal("0"),
    ):
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
                    "gl_account_id": ar_account,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(amount),
                },
                {
                    "gl_account_id": revenue_account,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(-(amount - tax_amount)),
                },
            )
        )
        if tax_amount:
            lines.append(
                {
                    "gl_account_id": "GL-SALES-TAX-001",
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(-tax_amount),
                }
            )

    invoices_by_id = {row["customer_invoice_id"]: row for row in invoices}
    tax_by_invoice: dict[str, Decimal] = {}
    for line in invoice_lines:
        invoice_id = line["customer_invoice_id"]
        tax_by_invoice[invoice_id] = tax_by_invoice.get(
            invoice_id, Decimal("0")
        ) + Decimal(str(line.get("tax_amount") or "0"))
    for invoice in invoices:
        post(
            f"JOURNAL-REV-{invoice['customer_invoice_id']}",
            "revenue_recognition",
            invoice["posting_date"],
            invoice["ar_gl_account_id"],
            invoice["revenue_gl_account_id"],
            Decimal(invoice["original_amount"]),
            tax_by_invoice.get(invoice["customer_invoice_id"], Decimal("0")),
        )
    for credit in credits:
        invoice = invoices_by_id[credit["related_invoice_id"]]
        credit_amount = Decimal(credit["amount"])
        invoice_amount = Decimal(invoice["original_amount"])
        invoice_tax = tax_by_invoice.get(invoice["customer_invoice_id"], Decimal("0"))
        credit_tax = (
            (credit_amount * invoice_tax / invoice_amount).quantize(Decimal("0.01"))
            if invoice_tax and invoice_amount
            else Decimal("0")
        )
        post(
            f"JOURNAL-CR-{credit['customer_credit_adjustment_id']}",
            "credit_memo",
            credit["posting_date"],
            invoice["ar_gl_account_id"],
            invoice["revenue_gl_account_id"],
            -credit_amount,
            -credit_tax,
        )
    return entries, lines


def _in_year_receipts(
    receipts: list[dict[str, Any]], calendar: dict[str, Any]
) -> list[dict[str, Any]]:
    end = date.fromisoformat(calendar["end_date"])
    return [row for row in receipts if date.fromisoformat(row["receipt_date"]) <= end]


@REGISTRY.rule(
    "expand_ar_receipt_movements",
    inputs=("bank_account", "customer_cash_receipt", "fiscal_calendar"),
    outputs="cash_movement",
    contribution_priority=70,
    gate="has_accounts_receivable",
)
def movements_from_ar_receipts(
    bank_account: dict[str, str],
    receipts: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, str | None]]:
    """Bank movements for cash collected inside the fiscal year."""
    in_transit = year_end_deposits_in_transit(receipts, calendar)
    return [
        with_movement_fks(
            {
                "bank_account_id": bank_account["bank_account_id"],
                "bank_activity_date": row["receipt_date"],
                "cash_movement_id": f"MOVE-{row['customer_cash_receipt_id']}",
                "signed_amount": row["amount"],
                "transaction_class": (
                    "customer_receipt"
                    if Decimal(row["amount"]) > 0
                    else "returned_item"
                ),
            }
        )
        for row in _in_year_receipts(receipts, calendar)
        if row["customer_cash_receipt_id"] not in in_transit
    ]


@REGISTRY.rule(
    "post_ar_receipt_movements",
    inputs=(
        "bank_account",
        "customer_cash_receipt",
        "fiscal_calendar",
        "customer_invoice",
    ),
    outputs="cash_book_movement",
    contribution_priority=70,
    gate="has_accounts_receivable",
)
def book_movements_from_ar_receipts(
    bank_account: dict[str, str],
    receipts: list[dict[str, Any]],
    calendar: dict[str, Any],
    invoices: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    """Post each in-year receipt to cash and the receivable account."""
    ar_account = invoices[0]["ar_gl_account_id"] if invoices else "GL-AR-001"
    return [
        with_book_movement_fks(
            {
                "bank_account_id": bank_account["bank_account_id"],
                "cash_book_movement_id": f"BOOK-{row['customer_cash_receipt_id']}",
                "counter_gl_account_id": ar_account,
                "journal_id": f"JOURNAL-{row['customer_cash_receipt_id']}",
                "posting_date": row["posting_date"],
                "signed_amount": row["amount"],
                "source_kind": "customer_receipt",
                "voucher_id": f"VCR-{row['customer_cash_receipt_id']}",
            }
        )
        for row in _in_year_receipts(receipts, calendar)
    ]


@REGISTRY.rule(
    "post_ar_nsf_fees",
    inputs=("bank_account", "customer_cash_receipt"),
    outputs=("cash_movement", "cash_book_movement"),
    contribution_priority=70,
    gate="has_accounts_receivable",
)
def post_ar_nsf_fees(
    bank_account: dict[str, str],
    receipts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Post the bank's returned-item fee for each NSF receipt reversal."""
    movements: list[dict[str, Any]] = []
    book_movements: list[dict[str, Any]] = []
    for receipt in receipts:
        receipt_id = receipt["customer_cash_receipt_id"]
        if "-NSF-" not in receipt_id:
            continue
        fee = Decimal(25 + sum(map(ord, receipt_id)) % 21).quantize(Decimal("0.01"))
        when = receipt["receipt_date"]
        movements.append(
            with_movement_fks(
                {
                    "bank_account_id": bank_account["bank_account_id"],
                    "bank_activity_date": when,
                    "cash_movement_id": f"MOVE-FEE-{receipt_id}",
                    "signed_amount": str(-fee),
                    "transaction_class": "bank_fee",
                }
            )
        )
        book_movements.append(
            with_book_movement_fks(
                {
                    "bank_account_id": bank_account["bank_account_id"],
                    "cash_book_movement_id": f"BOOK-FEE-{receipt_id}",
                    "counter_gl_account_id": "GL-EXPENSE-001",
                    "journal_id": f"JOURNAL-FEE-{receipt_id}",
                    "posting_date": when,
                    "signed_amount": str(-fee),
                    "source_kind": "bank_fee",
                    "voucher_id": f"VOUCHER-FEE-{receipt_id}",
                }
            )
        )
    return movements, book_movements
