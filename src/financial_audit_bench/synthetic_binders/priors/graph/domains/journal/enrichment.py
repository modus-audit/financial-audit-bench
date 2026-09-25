"""Journal memo provenance, deterministic texture, and line enrichment."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.presentation import (
    DESCRIPTION_BY_TYPE,
    MANUAL_JOURNAL_TYPES,
    _entry_time,
)


def _memo_map(
    world: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Entry id -> (memo, document number, counterparty name) maps, all built from the same world-owned event so no field is sampled apart from its siblings."""
    vendors = {row["vendor_id"]: row["name"] for row in world.get("vendor", [])}
    invoices = {
        row["vendor_invoice_id"]: row for row in world.get("vendor_invoice", [])
    }
    memos: dict[str, str] = {}
    numbers: dict[str, str] = {}
    names: dict[str, str] = {}
    for invoice in world.get("vendor_invoice", []):
        vendor = vendors.get(invoice["vendor_id"], "")
        # Memos embed the vendor's own printed invoice number (assigned by the
        # vendor_invoice finalizer, which runs before journal lines finalize): a
        # composed memo escapes the whole-cell client-id relabel map, so an internal
        # REF-###### here leaked into the GL export.
        printed = invoice.get("vendor_invoice_number") or ""
        memos[invoice["journal_id"]] = (
            f"{vendor} - {invoice['description'].replace('_', ' ')}"
            + (f" inv {printed}" if printed else "")
        ).strip()
        numbers[invoice["journal_id"]] = str(
            invoice.get("vendor_invoice_number") or invoice["invoice_reference"]
        )
        names[invoice["journal_id"]] = vendor
    for payment in world.get("ap_payment", []):
        invoice = invoices.get(payment["vendor_invoice_id"], {})
        vendor = vendors.get(invoice.get("vendor_id", ""), "")
        printed = invoice.get("vendor_invoice_number") or ""
        memos[payment["journal_id"]] = (
            f"AP pymt - {vendor}" + (f" inv {printed}" if printed else "")
        ).strip()
        numbers[payment["journal_id"]] = str(payment["payment_reference"])
        names[payment["journal_id"]] = vendor
    for accrual in world.get("accrued_expense", []):
        # Year-end accrual memos name the vendor and service accrued. Unbooked
        # accruals never post, so no memo exists.
        if accrual.get("book_layer") == "final_adjusted":
            continue
        vendor = vendors.get(accrual["vendor_id"], "")
        service = accrual["category"].replace("_", " ")
        memos[accrual["journal_entry_id"]] = (
            f"Accrue {vendor} - {service} for December".strip()
        )
        names[accrual["journal_entry_id"]] = vendor
    # Revenue side: billing, credit-memo, and collection memos name the world's customer
    # or job the way the vendor side names payees. Journal ids are deterministic per
    # posting rule, so the memo resolves through each subledger population directly.
    customers = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer", [])
    }
    for invoice in world.get("customer_invoice", []):
        customer = customers.get(invoice["customer_id"], "")
        entry_id = f"JOURNAL-REV-{invoice['customer_invoice_id']}"
        memos[entry_id] = f"{customer} inv {invoice['invoice_number']}".strip()
        numbers[entry_id] = str(invoice["invoice_number"])
        names[entry_id] = customer
    for public_number, credit in enumerate(
        sorted(
            world.get("customer_credit_adjustment", []),
            key=lambda row: (
                row["adjustment_date"],
                row["customer_credit_adjustment_id"],
            ),
        ),
        start=5001,
    ):
        customer = customers.get(credit["customer_id"], "")
        entry_id = f"JOURNAL-CR-{credit['customer_credit_adjustment_id']}"
        memos[entry_id] = f"Credit memo - {customer}".strip(" -")
        numbers[entry_id] = f"Credit Memo {public_number}"
        names[entry_id] = customer
    for receipt in world.get("customer_cash_receipt", []):
        customer = customers.get(receipt["customer_id"], "")
        if customer:
            # Line descriptions append the posting month; the memo carries
            # only the payer so the month never prints twice.
            memos[f"JOURNAL-{receipt['customer_cash_receipt_id']}"] = (
                f"Deposit - {customer}"
            )
            names[f"JOURNAL-{receipt['customer_cash_receipt_id']}"] = customer
    for movement in world.get("cash_movement", []):
        entry_id = movement["cash_movement_id"].replace("MOVE", "JOURNAL", 1)
        counterparty = movement.get("counterparty")
        if counterparty:
            # Month-free for the same reason as the deposit memos above.
            memos[entry_id] = str(counterparty)
            names.setdefault(entry_id, str(counterparty))
        if movement.get("transaction_class") == "check" and not movement.get(
            "ap_payment_id"
        ):
            # Resolves to the allocated check number at render time.
            numbers[entry_id] = movement["cash_movement_id"]
    return memos, numbers, names


def enrich_journal_population(
    entries: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    world: dict[str, Any] | None = None,
) -> None:
    """Normalize complete JE metadata once for every source-specific generator."""
    memos, numbers, names = _memo_map(world) if world else ({}, {}, {})
    for entry in entries:
        posting_date = entry["posting_date"]
        journal_type = entry["journal_type"]
        if journal_type in MANUAL_JOURNAL_TYPES:
            generation_type = "manual"
        elif str(entry.get("voucher_id", "")).startswith("LEASE-"):
            # Lease schedules are explicitly linked recurring templates.
            generation_type = "recurring"
        else:
            generation_type = "system"
        # System batches post overnight under the service account; the recurring
        # template fires early morning; humans post manual entries at varied business-
        # hour times.
        if generation_type == "manual":
            entered_time = _entry_time(entry["journal_entry_id"] + "-ENTERED", 8, 2)
            timestamp = _entry_time(entry["journal_entry_id"] + "-POSTED", 15, 2)
            user_id = "USER-PREPARER"
        elif generation_type == "recurring":
            timestamp = _entry_time(entry["journal_entry_id"], 7, 1)
            entered_time = timestamp
            user_id = "USER-SYSTEM"
        else:
            timestamp = _entry_time(entry["journal_entry_id"], 2, 4)
            entered_time = timestamp
            user_id = "USER-SYSTEM"
        # A posting rule may pre-set the memo; otherwise use the source map or
        # the retained journal-type vocabulary.
        raw_memo = memos.get(entry["journal_entry_id"], entry.get("description"))
        description = raw_memo or DESCRIPTION_BY_TYPE.get(
            journal_type, journal_type.replace("_", " ").title()
        )
        entry.update(
            {
                "accounting_period": posting_date[:7],
                # Num and Name derive from the same event as the memo
                #: blank when the source has no document
                # number or counterparty (general journals).
                "counterparty_name": names.get(
                    entry["journal_entry_id"], "Not applicable - internal posting"
                ),
                "document_number": numbers.get(
                    entry["journal_entry_id"], "Not applicable - internal journal"
                ),
                "approval_status": "approved",
                "description": description,
                "generation_type": generation_type,
                "entered_timestamp": f"{posting_date}T{entered_time}",
                "journal_source": journal_type,
                "posting_status": "posted",
                "posting_timestamp": f"{posting_date}T{timestamp}",
                # Reversal entries reference the journal they reverse;
                # everything else carries no reference.
                "reverses_journal_entry_id": entry.get("reverses_journal_entry_id"),
                "source_reference": entry["voucher_id"],
                "user_id": user_id,
            }
        )
    descriptions = {}
    for row in entries:
        description = f"{row['description'][:1].upper()}{row['description'][1:]}"
        descriptions[row["journal_entry_id"]] = description
    line_number: dict[str, int] = {}
    for line in lines:
        entry_id = line["journal_entry_id"]
        line_number[entry_id] = line_number.get(entry_id, 0) + 1
        amount = Decimal(line["signed_amount"])
        description = (
            line["description"]
            if line.get("description") is not None
            else descriptions.get(entry_id, "Ledger posting")
        )
        line.update(
            {
                "credit_amount": str(-amount if amount < 0 else Decimal("0.00")),
                "debit_amount": str(amount if amount > 0 else Decimal("0.00")),
                # One placeholder repeated 355 times defeats description
                # testing; derive from the entry.
                "description": description,
                "line_number": str(line_number[entry_id]),
            }
        )
