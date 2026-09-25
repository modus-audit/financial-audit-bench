"""Render the AP-06 received-not-invoiced population and exceptions."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _add_schedule_sheet,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    save_workbook,
    open_pbc_sheet,
    write_header,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)

World = dict[str, Any]


def render_received_not_invoiced(
    path: Path, world: World, title: str
) -> list[dict[str, Any]] | None:
    """Render AP-06 as a complete receiving population plus RNI exceptions."""

    receipts = sorted(
        world.get("goods_receipt") or [],
        key=lambda row: (row["receipt_date"], row["goods_receipt_id"]),
    )
    if not receipts:
        return None
    if len({row["goods_receipt_id"] for row in receipts}) != len(receipts):
        raise ValueError("AP-06 contains duplicate goods-receipt IDs")

    purchase_orders = {
        row["purchase_order_id"]: row for row in world.get("purchase_order") or []
    }
    vendors = {row["vendor_id"]: row for row in world.get("vendor") or []}
    receipt_invoices = [
        row for row in world.get("vendor_invoice") or [] if row.get("goods_receipt_id")
    ]
    invoices_by_receipt = {row["goods_receipt_id"]: row for row in receipt_invoices}
    if len(invoices_by_receipt) != len(receipt_invoices):
        raise ValueError("AP-06 has more than one current invoice for a receipt")
    rni_rows = list(world.get("rni_accrual_item") or [])
    rni_by_receipt = {row["goods_receipt_id"]: row for row in rni_rows}
    if len(rni_by_receipt) != len(rni_rows):
        raise ValueError("AP-06 has duplicate RNI dispositions for a receipt")
    pending_receipts = {
        row["goods_receipt_id"]
        for row in receipts
        if str(row.get("invoice_received") or "").lower() != "yes"
    }
    if pending_receipts != set(rni_by_receipt):
        missing = sorted(pending_receipts - set(rni_by_receipt))
        orphaned = sorted(set(rni_by_receipt) - pending_receipts)
        raise ValueError(
            "AP-06 RNI population does not resolve: "
            f"missing accrual links={missing}; orphan accrual links={orphaned}"
        )

    client_ids = client_id_map(world)
    wb, detail = open_pbc_sheet(title, "AP-06", world)
    detail.title = "Receiving Population"
    write_header(
        detail,
        [
            "Receipt ID",
            "Receipt Date",
            "Vendor",
            "PO",
            "Item / Service",
            "Quantity Received",
            "PO Amount",
            "Invoice Received",
            "Invoice",
            "Matching Status",
            "RNI Accrual ID",
            "Accrued Amount",
            "Accrual GL",
            "Subsequent Invoice Date",
            "Disposition",
        ],
    )
    cell_map: list[dict[str, Any]] = []
    population_total = Decimal("0.00")
    rni_total = Decimal("0.00")
    for receipt in receipts:
        receipt_id = receipt["goods_receipt_id"]
        po = purchase_orders.get(receipt["purchase_order_id"])
        vendor = vendors.get(receipt["vendor_id"])
        if po is None or vendor is None:
            raise ValueError(f"AP-06 receipt {receipt_id} has unresolved PO/vendor")
        po_amount = Decimal(str(po["total_amount"]))
        population_total += po_amount
        invoice = invoices_by_receipt.get(receipt_id)
        rni = rni_by_receipt.get(receipt_id)
        invoice_received = str(receipt.get("invoice_received") or "").lower() == "yes"
        if invoice_received and invoice is None:
            raise ValueError(
                f"AP-06 receipt {receipt_id} says invoiced but has no invoice"
            )
        if not invoice_received and invoice is not None:
            raise ValueError(
                f"AP-06 receipt {receipt_id} says not invoiced but has a current invoice"
            )
        if rni:
            accrued = Decimal(str(rni["amount"]))
            if accrued != po_amount:
                raise ValueError(
                    f"AP-06 receipt {receipt_id} accrual {accrued} does not tie to PO {po_amount}"
                )
            rni_total += accrued
            accrual = next(
                (
                    row
                    for row in world.get("accrued_expense") or []
                    if row["accrued_expense_id"] == rni["accrued_expense_id"]
                ),
                None,
            )
            if accrual is None or Decimal(str(accrual["ending_balance"])) != accrued:
                raise ValueError(
                    f"AP-06 RNI {rni['rni_accrual_item_id']} lacks a tied accrual"
                )
            disposition = (
                "Accrued at year end; subsequent invoice/payment support retained"
            )
        else:
            accrued = Decimal("0.00")
            accrual = None
            disposition = "Three-way match complete"

        detail.append(
            [
                client_ids.get(receipt_id, receipt_id),
                receipt["receipt_date"],
                vendor["name"],
                client_ids.get(
                    receipt["purchase_order_id"], receipt["purchase_order_id"]
                ),
                po["description"],
                receipt["quantity_received"],
                po_amount,
                "Yes" if invoice_received else "No",
                (
                    client_ids.get(
                        invoice["vendor_invoice_id"],
                        invoice.get("invoice_reference", ""),
                    )
                    if invoice
                    else "Pending at year end"
                ),
                str(receipt["matching_status"]).replace("_", " ").capitalize(),
                client_ids.get(rni["rni_accrual_item_id"], rni["rni_accrual_item_id"])
                if rni
                else "",
                accrued if rni else "",
                accrual["accrual_gl_account_id"] if accrual else "",
                rni["subsequent_invoice_date"] if rni else "",
                disposition,
            ]
        )
        cell_map.append(
            {
                "field": "Receipt ID",
                "cell": f"A{detail.max_row}",
                "value": client_ids.get(receipt_id, receipt_id),
            }
        )
    detail.append(
        [
            "Total",
            len(receipts),
            "",
            "",
            "",
            "",
            population_total,
            "",
            "",
            "",
            "",
            rni_total,
        ]
    )

    controls = _add_schedule_sheet(wb, "Population Controls", world)
    write_header(controls, ["Control", "Count", "Amount"])
    invoiced_count = len(receipts) - len(pending_receipts)
    for label, count, amount in (
        ("Source receiving rows", len(receipts), population_total),
        ("Invoice received — yes", invoiced_count, ""),
        ("Invoice received — no", len(pending_receipts), rni_total),
        ("RNI exceptions linked to accruals", len(rni_by_receipt), rni_total),
        (
            "Unresolved row-count difference",
            len(pending_receipts) - len(rni_by_receipt),
            "",
        ),
        ("Unresolved RNI amount difference", "", Decimal("0.00")),
    ):
        controls.append([label, count, amount])

    exceptions = _add_schedule_sheet(wb, "RNI Exceptions", world)
    write_header(
        exceptions,
        [
            "RNI ID",
            "Receipt ID",
            "Receipt Date",
            "Vendor",
            "PO",
            "Amount",
            "Accrual ID",
            "Subsequent Invoice Date",
            "Subsequent Payment ID",
            "Status",
        ],
    )
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_ap_subsequent import (
        subsequent_open_invoice_settlements,
    )

    payments_by_invoice = {
        row["vendor_invoice_id"]: row
        for row in subsequent_open_invoice_settlements(world)
    }
    for receipt_id in sorted(pending_receipts):
        rni = rni_by_receipt[receipt_id]
        payment = payments_by_invoice.get(rni["rni_accrual_item_id"])
        if payment is None:
            raise ValueError(
                f"AP-06 RNI {rni['rni_accrual_item_id']} lacks subsequent payment evidence"
            )
        exceptions.append(
            [
                client_ids.get(rni["rni_accrual_item_id"], rni["rni_accrual_item_id"]),
                client_ids.get(receipt_id, receipt_id),
                rni["receipt_date"],
                vendors[rni["vendor_id"]]["name"],
                client_ids.get(rni["purchase_order_id"], rni["purchase_order_id"]),
                rni["amount"],
                client_ids.get(rni["accrued_expense_id"], rni["accrued_expense_id"]),
                rni["subsequent_invoice_date"],
                payment["payment_id"],
                "Resolved — accrued at year end and settled after year end",
            ]
        )
    exceptions.append(
        [
            "Total",
            len(pending_receipts),
            "",
            "",
            "",
            rni_total,
            "",
            "",
            "",
            "",
        ]
    )

    save_workbook(wb, path)
    return cell_map
