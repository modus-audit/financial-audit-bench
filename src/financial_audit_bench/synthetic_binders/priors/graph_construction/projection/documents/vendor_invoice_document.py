"""Readable vendor invoices for sampled AP and inventory purchases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
    vendor_names,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
    fmt_money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    REMIT_LOCALITY_POOL,
)


def _filename(vendor: str, printed_number: str) -> str:
    clean = "".join(
        character
        for character in f"{vendor.lower()} {printed_number}"
        if character.isalnum() or character in " -"
    )
    return f"{' '.join(clean.split())}.txt"


def _remit_to(vendor: str) -> str:
    selector = sum(map(ord, vendor))
    return (
        f"PO Box {selector % 8900 + 100}, "
        f"{REMIT_LOCALITY_POOL[selector % len(REMIT_LOCALITY_POOL)]}"
    )


def _printed_po_number(invoice: dict[str, Any], world: World) -> str:
    purchase_order_id = invoice.get("purchase_order_id")
    if not purchase_order_id:
        return ""
    row = next(
        (
            item
            for item in world.get("purchase_order") or []
            if item["purchase_order_id"] == purchase_order_id
        ),
        None,
    )
    if row is None:
        return ""
    raw = str(row["purchase_order_id"])
    sequence = int(raw.rsplit("-", 1)[-1])
    return f"PO-{3500 + sequence}"


def _line_description(invoice: dict[str, Any]) -> str:
    return str(invoice["description"]).replace("_", " ").strip().capitalize()


def _render_tax_notice(
    doc: TextDoc,
    invoice: dict[str, Any],
    vendor: str,
    printed_number: str,
    world: World,
) -> None:
    doc.center(vendor)
    doc.center(_remit_to(vendor))
    doc.center("STATEMENT OF TAXES AND FEES DUE")
    doc.rule("=")
    doc.pair("Taxpayer", company_name(world))
    doc.pair("Statement number", printed_number)
    doc.pair("Statement date", fmt_date(invoice["document_date"]))
    doc.pair("Payment due", fmt_date(invoice["due_date"]))
    doc.rule()
    doc.pair("Description", f"Taxes and licenses - {invoice['document_date'][:4]}")
    doc.line()
    doc.tie("amount", "TOTAL DUE", fmt_money(invoice["amount"]))
    doc.line()
    doc.line(f"Detach and return this statement with payment to {_remit_to(vendor)}.")


def _render_invoice(
    doc: TextDoc,
    invoice: dict[str, Any],
    vendor: str,
    printed_number: str,
    world: World,
) -> None:
    doc.center(vendor)
    doc.center(_remit_to(vendor))
    doc.center("INVOICE")
    doc.rule("=")
    doc.pair("Bill to", company_name(world))
    doc.pair("Invoice number", printed_number)
    doc.pair("Invoice date", fmt_date(invoice["document_date"]))
    doc.pair("Due date", fmt_date(invoice["due_date"]))
    doc.pair("Terms", str(invoice["payment_terms"]).capitalize())
    if po_number := _printed_po_number(invoice, world):
        doc.pair("PO reference", po_number)
    start = invoice.get("service_period_start")
    end = invoice.get("service_period_end")
    if start and end:
        doc.pair("Service period", f"{fmt_date(start)} to {fmt_date(end)}")
    doc.rule()
    doc.table(
        ["Description", "Amount"],
        [[_line_description(invoice), fmt_money(invoice["amount"])]],
        [62, 14],
    )
    doc.line()
    doc.tie("amount", "AMOUNT DUE", fmt_money(invoice["amount"]))
    doc.line()
    doc.line(f"Please remit payment to {_remit_to(vendor)}.")


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    vendors = vendor_names(world)
    identity = package_identity(world)
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.inventory_procurement_documents import (
        selected_inventory_procurement_invoice_ids,
    )

    inventory_selection = selected_inventory_procurement_invoice_ids(world)
    entries = []
    for invoice in world["vendor_invoice"]:
        if (
            invoice["vendor_invoice_id"] in inventory_selection
            or invoice["vendor_invoice_id"] not in identity.invoice_sample
        ):
            continue
        vendor = vendors[invoice["vendor_id"]]
        printed_number = identity.invoice_numbers[invoice["vendor_invoice_id"]]
        doc = TextDoc()
        if invoice["description"] == "taxes_and_licenses":
            _render_tax_notice(doc, invoice, vendor, printed_number, world)
        else:
            _render_invoice(doc, invoice, vendor, printed_number, world)
        path = output_dir / "invoices" / _filename(vendor, printed_number)
        entries.append(
            entry(
                f"DOC-{invoice['vendor_invoice_id']}",
                "ap_and_accruals",
                path,
                doc.write(path),
            )
        )
    return entries
