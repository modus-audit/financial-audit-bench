"""Accounts-payable-specific workbook sections."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    finalize_workbook,
    open_pbc_sheet,
    write_header,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.public_presentation import (
    present_public_text,
)


def _outstanding_check_ap_relief_rows(
    world: dict[str, Any],
) -> list[dict[str, Any]]:
    """Classify year-end outstanding checks from their book and AP links."""
    year_end = str(world["fiscal_calendar"]["end_date"])
    books = {
        str(row["cash_book_movement_id"]): row
        for row in world.get("cash_book_movement", ()) or ()
    }
    payments = {
        str(row["ap_payment_id"]): row for row in world.get("ap_payment", ()) or ()
    }
    invoices = {
        str(row["vendor_invoice_id"]): row
        for row in world.get("vendor_invoice", ()) or ()
    }
    vendors = {str(row["vendor_id"]): row for row in world.get("vendor", ()) or ()}
    items = sorted(
        (
            row
            for row in world.get("bank_reconciling_item", ()) or ()
            if row.get("item_kind") == "outstanding_check"
            and str(row.get("original_statement_period_end") or "") == year_end
        ),
        key=lambda row: (
            str(row.get("book_date") or ""),
            str(row.get("bank_reconciling_item_id") or ""),
        ),
    )
    result: list[dict[str, Any]] = []
    for item in items:
        book_id = str(item.get("book_movement_id") or "")
        book = books.get(book_id)
        amount = abs(Decimal(str(item.get("amount") or 0)))
        cash_status = (
            "Cleared subsequently"
            if str(item.get("status") or "") == "cleared_subsequently"
            and bool(item.get("subsequent_clearing_date"))
            and str(item["subsequent_clearing_date"]) > year_end
            else "Open — not cleared"
        )
        row = {
            "reconciling_item_id": str(item["bank_reconciling_item_id"]),
            "book_movement_id": book_id,
            "check_date": item.get("book_date"),
            "clearing_date": item.get("subsequent_clearing_date"),
            "amount": str(amount),
            "cash_clearing_status": cash_status,
        }
        if book is None:
            row.update(
                {
                    "ap_classification": "Unresolved source link",
                    "ap_payment_id": None,
                    "invoice_id": None,
                    "vendor_name": None,
                    "payable_gl_account_id": None,
                    "book_entry_direction": None,
                    "ap_relief_status": "Exception — book movement is missing",
                }
            )
            result.append(row)
            continue

        payment_id = str(book.get("ap_payment_id") or "")
        payment = payments.get(payment_id) if payment_id else None
        if payment_id and payment is None:
            row.update(
                {
                    "ap_classification": "Unresolved source link",
                    "ap_payment_id": payment_id,
                    "invoice_id": None,
                    "vendor_name": None,
                    "payable_gl_account_id": book.get("counter_gl_account_id"),
                    "book_entry_direction": None,
                    "ap_relief_status": "Exception — AP payment link is missing",
                }
            )
            result.append(row)
            continue
        if payment is None:
            row.update(
                {
                    "ap_classification": "Non-AP",
                    "ap_payment_id": None,
                    "invoice_id": None,
                    "vendor_name": None,
                    "payable_gl_account_id": None,
                    "book_entry_direction": (
                        f"Dr {book.get('counter_gl_account_id') or 'non-AP account'} / Cr Cash"
                    ),
                    "ap_relief_status": (
                        "Not AP — cash clearing does not evidence AP relief"
                    ),
                }
            )
            result.append(row)
            continue

        invoice = invoices.get(str(payment.get("vendor_invoice_id") or ""))
        vendor = vendors.get(str((invoice or {}).get("vendor_id") or ""), {})
        relief_passes = all(
            (
                book.get("counter_gl_account_id")
                == payment.get("payable_gl_account_id"),
                abs(Decimal(str(book.get("signed_amount") or 0)))
                == abs(Decimal(str(payment.get("amount") or 0)))
                == amount,
                Decimal(str(book.get("signed_amount") or 0)) < 0,
                str(payment.get("posting_date") or "") <= year_end,
                bool(invoice),
            )
        )
        row.update(
            {
                "ap_classification": "AP",
                "ap_payment_id": payment_id,
                "invoice_id": payment.get("vendor_invoice_id"),
                "vendor_name": vendor.get("name"),
                "payable_gl_account_id": payment.get("payable_gl_account_id"),
                "book_entry_direction": (
                    f"Dr {payment.get('payable_gl_account_id')} / Cr Cash"
                ),
                "ap_relief_status": (
                    "AP relief linked and directionally consistent"
                    if relief_passes
                    else "Exception — AP relief link or direction does not agree"
                ),
            }
        )
        result.append(row)
    return result


def _schedule_sheet(workbook: Any, title: str, world: dict[str, Any]) -> Any:
    sheet = workbook.create_sheet(title)
    sheet.append([world["company_context"]["legal_name"]])
    sheet.append([title])
    year_end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    sheet.append([f"As of {year_end.strftime('%B %d, %Y')}"])
    sheet.append([])
    return sheet


def append_outstanding_check_ap_relief(
    workbook: Any, world: dict[str, Any]
) -> list[dict[str, Any]]:
    """Append the AP-direction test population beside the bank-clearing facts."""

    rows = _outstanding_check_ap_relief_rows(world)
    sheet = _schedule_sheet(workbook, "Outstanding Check AP Relief", world)
    write_header(sheet, ["Population control", "Count", "Amount"])
    ap_rows = [row for row in rows if row["ap_classification"] == "AP"]
    non_ap_rows = [row for row in rows if row["ap_classification"] == "Non-AP"]
    unresolved_rows = [
        row for row in rows if row["ap_classification"] == "Unresolved source link"
    ]

    def amount(values: list[dict[str, Any]]) -> Decimal:
        return sum((Decimal(str(row["amount"])) for row in values), Decimal("0"))

    population_amount = amount(rows)
    for label, values in (
        ("Year-end outstanding-check population", rows),
        ("Checks linked to AP relief", ap_rows),
        ("Checks factually outside AP", non_ap_rows),
        ("Unresolved source links", unresolved_rows),
    ):
        sheet.append([label, len(values), amount(values)])
    sheet.append(
        [
            "Classification difference",
            len(rows) - len(ap_rows) - len(non_ap_rows) - len(unresolved_rows),
            population_amount
            - amount(ap_rows)
            - amount(non_ap_rows)
            - amount(unresolved_rows),
        ]
    )
    ties = [
        {
            "field": "Outstanding-check count",
            "sheet": sheet.title,
            "cell": "B6",
            "value": str(len(rows)),
        },
        {
            "field": "Outstanding-check amount",
            "sheet": sheet.title,
            "cell": "C6",
            "value": str(population_amount),
        },
    ]

    sheet.append([])
    write_header(
        sheet,
        [
            "Check #",
            "Reconciliation Item",
            "Book Date",
            "Amount",
            "Subsequent Clearing Date",
            "Cash-Clearing Fact",
            "AP Classification",
            "AP Payment / Book Reference",
            "Vendor",
            "Invoice",
            "Payable / Counter GL",
            "Book-Entry Direction",
            "AP Source-Link Fact",
        ],
    )
    identity = package_identity(world)
    client_ids = client_id_map(world)
    for row in rows:
        payment_id = str(row.get("ap_payment_id") or "")
        movement_id = str(row.get("book_movement_id") or "")
        check_key = payment_id or movement_id.replace("BOOK", "MOVE", 1)
        check_number = identity.check_numbers.get(check_key, "")
        invoice_id = str(row.get("invoice_id") or "")
        invoice_number = identity.invoice_numbers.get(
            invoice_id, client_ids.get(invoice_id, invoice_id)
        )
        reconciling_reference = client_ids.get(
            str(row["reconciling_item_id"]), str(row["reconciling_item_id"])
        )
        if reconciling_reference == str(row["reconciling_item_id"]):
            raise ValueError(
                "outstanding-check source lacks a client-facing reconciliation "
                f"reference: {row['reconciling_item_id']}"
            )
        source_reference = (
            f"Check {check_number}" if check_number else reconciling_reference
        )
        sheet.append(
            [
                check_number,
                reconciling_reference,
                row["check_date"],
                row["amount"],
                row["clearing_date"],
                row["cash_clearing_status"],
                row["ap_classification"],
                source_reference,
                row.get("vendor_name") or "",
                invoice_number,
                row.get("payable_gl_account_id") or "",
                row.get("book_entry_direction") or "",
                row["ap_relief_status"],
            ]
        )
    sheet.append(["Total", len(rows), "", population_amount])
    sheet.append([])
    sheet.append(
        [
            "Control note",
            (
                "Cash clearing tests whether a book item reached the bank. AP relief "
                "separately tests whether the book entry debited a payable and links "
                "to the underlying invoice; a non-AP check cannot be treated as AP "
                "support merely because it cleared."
            ),
        ]
    )
    return ties


def render_vendor_invoice_support(
    path: Any, world: dict[str, Any], title: str
) -> list[dict[str, Any]]:
    """Render AP-03 as one invoice-keyed table with typed PO applicability."""

    invoices = list(world.get("vendor_invoice") or [])
    invoice_ids = [str(row.get("vendor_invoice_id") or "") for row in invoices]
    if not all(invoice_ids) or len(invoice_ids) != len(set(invoice_ids)):
        raise ValueError("AP-03 source requires unique nonblank vendor-invoice IDs")
    vendors = {row["vendor_id"]: row for row in world.get("vendor") or []}
    orders = {
        row["purchase_order_id"]: row for row in world.get("purchase_order") or []
    }
    receipts = {
        row["goods_receipt_id"]: row for row in world.get("goods_receipt") or []
    }
    identity = package_identity(world)
    client_ids = client_id_map(world)
    workbook, detail = open_pbc_sheet(title, "AP-03", world)
    detail.title = "Vendor Invoice Support"
    write_header(
        detail,
        [
            "AP Record ID",
            "Vendor",
            "Vendor Invoice",
            "Invoice Date",
            "Goods / Services",
            "Receipt / Service Date",
            "Evidence Basis",
            "PO Applicability",
            "PO Number",
            "Quantity",
            "Unit Price",
            "GL Coding",
            "PO Approval",
            "Invoice Amount",
            "Contractual Terms",
        ],
    )
    cell_map: list[dict[str, Any]] = []
    population_amount = Decimal("0")
    po_count = 0
    for invoice in invoices:
        vendor = vendors.get(invoice.get("vendor_id"))
        if vendor is None:
            raise ValueError(
                f"AP-03 invoice {invoice['vendor_invoice_id']} has no vendor"
            )
        purchase_order_id = invoice.get("purchase_order_id")
        goods_receipt_id = invoice.get("goods_receipt_id")
        order = orders.get(purchase_order_id)
        receipt = receipts.get(goods_receipt_id)
        if purchase_order_id and order is None:
            raise ValueError(
                f"AP-03 invoice {invoice['vendor_invoice_id']} has an unresolved PO"
            )
        if goods_receipt_id and receipt is None:
            raise ValueError(
                f"AP-03 invoice {invoice['vendor_invoice_id']} has an unresolved receipt"
            )
        if receipt is not None and (
            order is None
            or receipt.get("purchase_order_id") != order.get("purchase_order_id")
            or receipt.get("vendor_id") != invoice.get("vendor_id")
        ):
            raise ValueError(
                f"AP-03 invoice {invoice['vendor_invoice_id']} has an inconsistent "
                "PO/receipt/vendor chain"
            )
        po_applicable = order is not None
        if po_applicable:
            po_count += 1
        amount = Decimal(str(invoice["amount"]))
        population_amount += amount
        vendor_invoice = identity.invoice_numbers.get(
            invoice["vendor_invoice_id"],
            invoice.get("vendor_invoice_number"),
        )
        if not vendor_invoice:
            raise ValueError(
                "AP-03 invoice lacks a client-facing vendor reference: "
                f"{invoice['vendor_invoice_id']}"
            )
        record_id = vendor_invoice
        description = invoice.get("description")
        public_description = (
            present_public_text(str(description)) if description is not None else None
        )
        detail.append(
            [
                record_id,
                vendor["name"],
                vendor_invoice,
                date.fromisoformat(str(invoice["document_date"])),
                public_description,
                date.fromisoformat(
                    str(
                        receipt["receipt_date"]
                        if receipt is not None
                        else invoice["service_period_end"]
                    )
                ),
                "Goods receipt" if receipt is not None else "Service period",
                (
                    "Applicable — PO-backed purchase"
                    if po_applicable
                    else "Not applicable — service/non-PO invoice"
                ),
                (
                    client_ids.get(
                        order["purchase_order_id"], order["purchase_order_id"]
                    )
                    if order is not None
                    else None
                ),
                float(Decimal(str(order["quantity"]))) if order is not None else None,
                float(Decimal(str(order["unit_price"]))) if order is not None else None,
                client_ids.get(
                    invoice["expense_gl_account_id"], invoice["expense_gl_account_id"]
                ),
                order.get("approval_status") if order is not None else None,
                float(amount),
                invoice.get("payment_terms"),
            ]
        )
        row = detail.max_row
        detail.cell(row, 4).number_format = "m/d/yyyy"
        detail.cell(row, 6).number_format = "m/d/yyyy"
        detail.cell(row, 10).number_format = "0.00"
        detail.cell(row, 11).number_format = "#,##0.00;[Red](#,##0.00);-"
        detail.cell(row, 14).number_format = "#,##0.00;[Red](#,##0.00);-"
        cell_map.extend(
            (
                {
                    "field": "AP Record ID",
                    "cell": f"A{row}",
                    "value": record_id,
                },
                {
                    "field": "Invoice Amount",
                    "cell": f"N{row}",
                    "value": str(amount),
                },
            )
        )
    detail.append(
        [
            "Total",
            len(invoices),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            float(population_amount),
        ]
    )

    control = _schedule_sheet(workbook, "Population Controls", world)
    write_header(control, ["Control", "Count", "Amount", "Status"])
    unique_ids = {str(row.get("invoice_reference") or "") for row in invoices}
    controls = (
        ("Source vendor invoices", len(invoices), population_amount, "Source"),
        ("Rendered invoice rows", len(invoices), population_amount, "Tied"),
        (
            "Unique AP record IDs",
            len(unique_ids),
            "",
            "Tied" if len(unique_ids) == len(invoices) else "Exception",
        ),
        ("PO-backed invoices", po_count, "", "Quantity/price/approval applicable"),
        (
            "Non-PO invoices",
            len(invoices) - po_count,
            "",
            "Typed null; service/non-PO applicability retained",
        ),
    )
    for row in controls:
        control.append(list(row))
    if len(unique_ids) != len(invoices) or "" in unique_ids:
        raise ValueError("AP-03 invoice support requires unique nonblank AP record IDs")
    finalize_workbook(workbook)
    workbook.save(path)
    return cell_map
