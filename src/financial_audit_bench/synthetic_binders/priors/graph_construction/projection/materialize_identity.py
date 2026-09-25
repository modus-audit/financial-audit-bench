"""Client-facing identifiers used across materialized binder artifacts."""

from __future__ import annotations

from typing import Any


def fixed_asset_client_tags(assets: list[dict[str, Any]]) -> dict[str, str]:
    """Client asset tags (FA-1001 style), numbered in placed-in-service order
    the way a register module mints them — never the generation lineage id."""
    ordered = sorted(
        assets,
        key=lambda row: (
            str(row["placed_in_service_date"]),
            str(row["fixed_asset_id"]),
        ),
    )
    return {
        str(row["fixed_asset_id"]): f"FA-{1000 + number}"
        for number, row in enumerate(ordered, 1)
    }


def client_id_map(world: dict[str, Any]) -> dict[str, str]:
    """Map internal world ids to the client-facing labels a real system prints."""
    client_ids: dict[str, str] = {}
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
        package_identity,
    )

    identity = package_identity(world)
    client_ids.update(identity.journal_numbers)
    # The service account posts under the software's own actor label, not
    # the generator's all-caps marker.
    client_ids["SYSTEM"] = "System Administration"
    client_ids["USER-SYSTEM"] = "System Administration"
    # Vouchers and per-entry support records present as the entry's journal
    # number — no client system prints generation-lineage ids.
    for entry_row in world.get("journal_entry", []):
        number = identity.journal_numbers.get(entry_row["journal_entry_id"])
        voucher = entry_row.get("voucher_id")
        if number and voucher:
            client_ids.setdefault(str(voucher), number)
        if number:
            client_ids[f"SUPPORT-{entry_row['journal_entry_id']}"] = number
    client_ids.update(
        (row["vendor_id"], row["name"]) for row in world.get("vendor", [])
    )
    employee_ids = {
        str(row["employee_id"]): str(10000 + number)
        for number, row in enumerate(
            sorted(world.get("employee", []), key=lambda row: row["employee_id"]),
            1,
        )
    }
    client_ids.update(employee_ids)
    # HR/payroll reports retain canonical joins in the world but print the identifiers
    # their source systems expose. Composite authorization references must be aliased as
    # a whole; mapping only ``EMP-001`` cannot rewrite ``HR-AUTH-EMP-001-HIRE`` in a
    # scalar workbook cell.
    for row in list(world.get("employee", [])) + list(
        world.get("payroll_authorization_event", [])
    ):
        employee_id = str(row.get("employee_id") or "")
        employee_number = employee_ids.get(employee_id)
        reference = str(row.get("authorization_reference") or "")
        if employee_number and reference:
            client_ids[reference] = reference.replace(employee_id, employee_number)
        event_id = str(row.get("payroll_authorization_event_id") or "")
        if employee_number and event_id:
            client_ids[event_id] = event_id.replace(employee_id, employee_number)
    fiscal_year = int(world["fiscal_calendar"]["fiscal_year"])
    client_ids.update(
        (
            str(row["payroll_run_id"]),
            f"PR-{fiscal_year}-{number:03d}",
        )
        for number, row in enumerate(
            sorted(
                world.get("payroll_run", []),
                key=lambda row: (row["period_end_date"], row["payroll_run_id"]),
            ),
            1,
        )
    )
    # Register rows print a client asset tag; mapping the id to the description made
    # Ref, Unique asset ID, and Description triplicates on the master schedule.
    client_ids.update(fixed_asset_client_tags(world.get("fixed_asset", [])))
    # Payments print the client's document numbers: the allocated check number
    # for checks, an ACH trace for electronic settlements.
    electronic_number = 0
    for payment in world.get("ap_payment", []):
        if payment["payment_method"] == "check":
            document = identity.check_numbers.get(payment["ap_payment_id"])
        else:
            electronic_number += 1
            document = f"ACH{fiscal_year}{electronic_number:05d}"
        if document:
            client_ids[payment["ap_payment_id"]] = document
            client_ids[payment["payment_reference"]] = document
    # Invoice listings print the vendor's own invoice numbers.
    for invoice in world.get("vendor_invoice", []):
        number = identity.invoice_numbers.get(invoice["vendor_invoice_id"])
        if number:
            client_ids[invoice["invoice_reference"]] = number
            client_ids[invoice["vendor_invoice_id"]] = number
            client_ids[f"Vendor invoice {invoice['invoice_reference']}"] = (
                f"Vendor invoice {number}"
            )
    # Procurement records use readable document numbers in every client surface. Their
    # graph keys remain stable lineage identifiers, but a PO, receiving log, AP aging,
    # or supplier invoice never prints those keys.
    for number, order in enumerate(
        sorted(
            world.get("purchase_order", []),
            key=lambda row: (row["order_date"], row["purchase_order_id"]),
        ),
        3501,
    ):
        client_ids[order["purchase_order_id"]] = f"PO-{number}"
    for number, receipt in enumerate(
        sorted(
            world.get("goods_receipt", []),
            key=lambda row: (row["receipt_date"], row["goods_receipt_id"]),
        ),
        81001,
    ):
        client_ids[receipt["goods_receipt_id"]] = f"RCV-{number}"
    # RNI/accrual references are client close-control numbers. Their graph keys must
    # never leak into the receiving, cutoff, or subsequent-payment schedules merely
    # because those schedules are specialized renderers.
    fiscal_year = int(world["fiscal_calendar"]["fiscal_year"])
    for number, rni in enumerate(
        sorted(
            world.get("rni_accrual_item", []),
            key=lambda row: (row["receipt_date"], row["rni_accrual_item_id"]),
        ),
        1,
    ):
        client_ids[rni["rni_accrual_item_id"]] = f"RNI-{fiscal_year}-{number:03d}"
        client_ids[rni["accrued_expense_id"]] = f"ACR-{fiscal_year}-{number:03d}"
    # C3): invoice numbers, SKUs, lot/tag numbers, BOL and receiver numbers —
    # never generation-lineage ids.
    client_ids.update(
        (row["customer_id"], row["customer_name"]) for row in world.get("customer", [])
    )
    client_ids.update(
        (row["customer_invoice_id"], row["invoice_number"])
        for row in world.get("customer_invoice", [])
    )
    invoice_numbers = {
        row["customer_invoice_id"]: row["invoice_number"]
        for row in world.get("customer_invoice", [])
    }
    client_ids.update(
        (
            row["ar_cutoff_item_id"],
            invoice_numbers.get(row["customer_invoice_id"], row["support_reference"]),
        )
        for row in world.get("ar_cutoff_item", [])
    )
    client_ids.update(
        (
            row["ar_aging_item_id"],
            invoice_numbers.get(row["customer_invoice_id"], row["invoice_number"]),
        )
        for row in world.get("accounts_receivable_aging", [])
    )
    client_ids.update(
        (row["customer_cash_receipt_id"], row["bank_reference"])
        for row in world.get("customer_cash_receipt", [])
    )
    # Application rows present as their receipt's bank reference: an AR
    # module lists applications under the receipt, never a lineage id.
    receipt_references = {
        row["customer_cash_receipt_id"]: row["bank_reference"]
        for row in world.get("customer_cash_receipt", [])
    }
    client_ids.update(
        (
            row["ar_receipt_application_id"],
            receipt_references.get(row["customer_cash_receipt_id"], ""),
        )
        for row in world.get("ar_receipt_application", [])
    )
    # Credit memos carry their own date-ordered sequence (numbers mirroring the related
    # invoice are inconsistent); lines and allowance estimates present as the invoice
    # they belong to.
    memo_rows = sorted(
        world.get("customer_credit_adjustment", []),
        key=lambda row: (row["adjustment_date"], row["customer_credit_adjustment_id"]),
    )
    for memo_number, row in enumerate(memo_rows, 1201):
        client_ids[row["customer_credit_adjustment_id"]] = f"CM-{memo_number}"
    client_ids.update(
        (
            row["customer_invoice_line_id"],
            invoice_numbers.get(row["customer_invoice_id"], ""),
        )
        for row in world.get("customer_invoice_line", [])
    )
    # Pool-level allowance rows: the client-facing row
    # key is the aging pool label, like the capitalization policy's class.
    client_ids.update(
        (row["ar_allowance_estimate_id"], row["aging_pool"])
        for row in world.get("ar_allowance_estimate", [])
    )
    client_ids.update(
        (row["fulfillment_event_id"], row["support_reference"])
        for row in world.get("fulfillment_event", [])
    )
    client_ids.update(
        (row["inventory_item_id"], row["sku"])
        for row in world.get("inventory_item", [])
    )
    client_ids.update(
        (row["inventory_lot_balance_id"], row["lot_number"])
        for row in world.get("inventory_lot_balance", [])
    )
    client_ids.update(
        (row["inventory_movement_id"], row["source_document"])
        for row in world.get("inventory_movement", [])
    )
    client_ids.update(
        (row["inventory_count_id"], row["final_results_reference"])
        for row in world.get("inventory_count", [])
    )
    client_ids.update(
        (row["inventory_count_line_id"], row["tag_number"])
        for row in world.get("inventory_count_line", [])
    )
    accounts = []
    for node in ("bank_account", "payroll_bank_account"):
        value = world.get(node) or []
        accounts.extend([value] if isinstance(value, dict) else value)
    for account in accounts:
        masked = account.get("last_four_digits") or str(
            account.get("masked_account_number", "")
        ).lstrip("*X")
        client_ids[account["bank_account_id"]] = account["account_name"] + (
            f" ****{masked}" if masked else ""
        )
    # GL account ids print as the client's account code and name (a client schedule
    # shows "6120 - Accounts receivable", never "GL-AR-001"; a bare caption repeated
    # down a GL-account column read as filler text in
    client_ids.update(
        (
            row["gl_account_id"],
            (
                f"{row['account_number']} - {row['name']}"
                if row.get("account_number")
                else row["name"]
            ),
        )
        for row in world.get("general_ledger_account", [])
    )
    # Reconciling items print as their document number (the check the rec and
    # the outstanding list both name), not the internal item id.
    books_by_id = {
        row["cash_book_movement_id"]: row
        for row in world.get("cash_book_movement", [])
        if row.get("ap_payment_id")
    }
    receipts_by_book_id = {
        f"BOOK-{row['customer_cash_receipt_id']}": row
        for row in world.get("customer_cash_receipt", [])
    }
    customer_names = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer", [])
    }
    for item in world.get("bank_reconciling_item", []):
        book = books_by_id.get(item.get("book_movement_id"))
        if book is not None:
            number = identity.check_numbers.get(book["ap_payment_id"])
            if number:
                client_ids[item["bank_reconciling_item_id"]] = f"Check {number}"
                continue
        # Deposits in transit print as the deposit a rec support lists (customer + book
        # date), never the internal item id (the new in-transit population would
        # otherwise leak ITEM-RECON-…).
        receipt = receipts_by_book_id.get(str(item.get("book_movement_id")))
        if receipt is not None:
            booked = str(item["book_date"])  # ISO: slice month/day
            when = f"{booked[5:7]}/{booked[8:10]}"
            who = customer_names.get(receipt["customer_id"], "customer")
            client_ids[item["bank_reconciling_item_id"]] = f"Deposit {when} - {who}"
            continue
        # Label remaining reconciling items by kind and date.
        dated = str(item.get("bank_date") or item.get("book_date") or "")
        when = f" {dated[5:7]}/{dated[8:10]}" if len(dated) >= 10 else ""
        kind = str(item.get("item_kind", "reconciling item")).replace("_", " ")
        if kind == "bank only":
            kind = "bank adjustment"
        client_ids[item["bank_reconciling_item_id"]] = f"{kind.capitalize()}{when}"
    # Map remaining lineage IDs to their client-facing labels.
    from datetime import date as _date

    vendors_by_id = {row["vendor_id"]: row["name"] for row in world.get("vendor", [])}
    for row in world.get("accrued_expense", []):
        month = _date.fromisoformat(str(row["service_period_end"])).strftime("%B %Y")
        if row["vendor_id"] not in vendors_by_id:
            # The accrual naming path resolves through the same vendor map the invoices
            # use; a placeholder "Vendor" label is a construction error.
            raise ValueError(
                f"accrual {row['accrued_expense_id']} names vendor "
                f"{row['vendor_id']!r} absent from the vendor master"
            )
        vendor = vendors_by_id[row["vendor_id"]]
        client_ids[row["accrued_expense_id"]] = f"{vendor} accrual - {month}"
    for row in world.get("capitalization_policy", []):
        client_ids[row["capitalization_policy_id"]] = (
            str(row["asset_class"]).replace("_", " ").capitalize()
        )
    return client_ids
