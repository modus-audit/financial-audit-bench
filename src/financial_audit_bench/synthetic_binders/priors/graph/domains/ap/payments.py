"""AP settlement projections into bank and cash-book movements."""

from __future__ import annotations

from decimal import Decimal

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    with_book_movement_fks,
    with_movement_fks,
)


@REGISTRY.rule(
    "expand_ap_payments",
    inputs=(
        "bank_account",
        "ap_payment",
        "vendor_invoice",
        "vendor",
        "fiscal_calendar",
    ),
    outputs="cash_movement",
    contribution_priority=30,
)
def movements_from_ap_payments(
    bank_account: dict[str, str],
    ap_payments: list[dict[str, str]],
    vendor_invoices: list[dict[str, str]],
    vendors: list[dict[str, str]],
    calendar: dict[str, str | int],
) -> list[dict[str, str | None]]:
    """Render bank movements that settle accounts-payable obligations."""
    year_end = str(calendar["end_date"])
    invoices_by_id = {str(row["vendor_invoice_id"]): row for row in vendor_invoices}
    vendor_names = {str(row["vendor_id"]): str(row["name"]) for row in vendors}
    rows: list[dict[str, str | None]] = []
    for payment in ap_payments:
        if payment["bank_activity_date"] > year_end:
            continue
        invoice = invoices_by_id.get(str(payment["vendor_invoice_id"]))
        movement_id = payment["ap_payment_id"].replace("AP-PAYMENT", "MOVE-AP", 1)
        rows.append(
            with_movement_fks(
                {
                    "ap_payment_id": payment["ap_payment_id"],
                    "bank_account_id": bank_account["bank_account_id"],
                    "bank_activity_date": payment["bank_activity_date"],
                    "cash_movement_id": movement_id,
                    "signed_amount": str(-Decimal(payment["amount"])),
                    "transaction_class": "ap_payment",
                    "counterparty": (
                        vendor_names.get(str(invoice["vendor_id"]))
                        if invoice is not None
                        else None
                    ),
                }
            )
        )
    return rows


@REGISTRY.rule(
    "post_ap_payments",
    inputs=("bank_account", "ap_payment"),
    outputs="cash_book_movement",
    contribution_priority=30,
)
def book_movements_from_ap_payments(
    bank_account: dict[str, str], ap_payments: list[dict[str, str]]
) -> list[dict[str, str | None]]:
    """Post each payable settlement to cash and accounts payable."""
    return [
        with_book_movement_fks(
            {
                "ap_payment_id": row["ap_payment_id"],
                "bank_account_id": bank_account["bank_account_id"],
                "cash_book_movement_id": row["ap_payment_id"].replace(
                    "AP-PAYMENT", "BOOK-AP", 1
                ),
                "counter_gl_account_id": row["payable_gl_account_id"],
                "journal_id": row["journal_id"],
                "posting_date": row["posting_date"],
                "signed_amount": str(-Decimal(row["amount"])),
                "source_kind": "ap_payment",
                "voucher_id": row["voucher_id"],
            }
        )
        for row in ap_payments
    ]
