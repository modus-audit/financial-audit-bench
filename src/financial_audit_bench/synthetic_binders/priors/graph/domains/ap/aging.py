"""Accounts-payable rollforward, open-item, and aging views."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "derive_accounts_payable_open_items",
    inputs=("vendor_invoice", "ap_payment", "fiscal_calendar", "accrued_expense"),
    outputs="accounts_payable_open_item",
)
def derive_accounts_payable_open_items(
    invoices: list[dict[str, str]],
    payments: list[dict[str, str]],
    calendar: dict[str, str | int],
    accrued_expenses: list[dict[str, str]] = (),
) -> list[dict[str, str]]:
    """Derive invoice-level open amounts and aging from payment applications."""
    as_of = date.fromisoformat(str(calendar["end_date"]))
    # Accruals reconcile on their own rollforward, not this AP population.
    del accrued_expenses
    accrual_rows: list[dict[str, str]] = []
    paid_by_invoice: dict[str, Decimal] = {}
    for payment in payments:
        invoice_id = payment["vendor_invoice_id"]
        paid_by_invoice[invoice_id] = paid_by_invoice.get(
            invoice_id, Decimal("0.00")
        ) + Decimal(payment["amount"])
    rows = list(accrual_rows)
    for invoice in invoices:
        original = Decimal(invoice["amount"])
        paid = paid_by_invoice.get(invoice["vendor_invoice_id"], Decimal("0.00"))
        open_amount = original - paid
        if open_amount < 0:
            raise ValueError("accounts-payable invoice was overpaid")
        due_date = date.fromisoformat(invoice["due_date"])
        days_past_due = 0 if not open_amount else max(0, (as_of - due_date).days)
        if not open_amount:
            aging_bucket = "paid"
        elif not days_past_due:
            aging_bucket = "current"
        else:
            aging_bucket = next(
                bucket
                for limit, bucket in (
                    (30, "1-30"),
                    (60, "31-60"),
                    (90, "61-90"),
                    (None, "over-90"),
                )
                if limit is None or days_past_due <= limit
            )
        rows.append(
            {
                "aging_bucket": aging_bucket,
                "ap_open_item_id": f"OPEN-{invoice['vendor_invoice_id']}",
                "as_of_date": as_of.isoformat(),
                "currency_code": invoice["currency_code"],
                "days_past_due": str(days_past_due),
                "document_date": invoice["document_date"],
                "due_date": invoice["due_date"],
                "open_amount": str(open_amount),
                "original_amount": str(original),
                "payable_gl_account_id": invoice["payable_gl_account_id"],
                "payment_amount": str(paid),
                "status": "open" if open_amount else "paid",
                "vendor_id": invoice["vendor_id"],
                "vendor_invoice_id": invoice["vendor_invoice_id"],
            }
        )
    return rows


@REGISTRY.rule(
    "aggregate_accounts_payable_aging",
    inputs=(
        "accounts_payable_open_item",
        "trial_balance_account",
        "fiscal_calendar",
    ),
    outputs="accounts_payable_aging",
)
def aggregate_accounts_payable_aging(
    open_items: list[dict[str, str]],
    trial_balance: list[dict[str, str]],
    calendar: dict[str, str | int],
) -> list[dict[str, str | None]]:
    """Group open invoices by age and reconcile the total to A/P and the TB."""
    tb_by_account = {row["gl_account_id"]: row for row in trial_balance}
    rows = []
    account_ids = sorted({row["payable_gl_account_id"] for row in open_items})
    for account_id in account_ids:
        account_items = [
            row
            for row in open_items
            if row["payable_gl_account_id"] == account_id
            and Decimal(row["open_amount"])
        ]
        for bucket in sorted({row["aging_bucket"] for row in account_items}):
            bucket_items = [
                row for row in account_items if row["aging_bucket"] == bucket
            ]
            rows.append(
                {
                    "aging_bucket": bucket,
                    "difference": None,
                    "fiscal_calendar_id": str(calendar["fiscal_calendar_id"]),
                    "gl_balance": None,
                    "item_count": str(len(bucket_items)),
                    "open_amount": str(
                        sum(
                            (Decimal(row["open_amount"]) for row in bucket_items),
                            Decimal("0.00"),
                        )
                    ),
                    "original_amount": str(
                        sum(
                            (Decimal(row["original_amount"]) for row in bucket_items),
                            Decimal("0.00"),
                        )
                    ),
                    "payable_gl_account_id": account_id,
                    "reconciliation_explanation": None,
                    "reconciliation_resolution": None,
                    "reconciliation_status": "detail",
                }
            )
        open_total = sum(
            (Decimal(row["open_amount"]) for row in account_items),
            Decimal("0.00"),
        )
        gl_balance = Decimal(tb_by_account[account_id]["closing_balance"])
        difference = open_total + gl_balance
        rows.append(
            {
                "aging_bucket": "total",
                "difference": str(difference),
                "fiscal_calendar_id": str(calendar["fiscal_calendar_id"]),
                "gl_balance": str(gl_balance),
                "item_count": str(len(account_items)),
                "open_amount": str(open_total),
                "original_amount": str(
                    sum(
                        (Decimal(row["original_amount"]) for row in account_items),
                        Decimal("0.00"),
                    )
                ),
                "payable_gl_account_id": account_id,
                "reconciliation_explanation": (
                    None
                    if difference == 0
                    else "subledger and GL require investigation"
                ),
                "reconciliation_resolution": (
                    "not required" if difference == 0 else "open"
                ),
                "reconciliation_status": "tied" if difference == 0 else "difference",
            }
        )
    return rows
