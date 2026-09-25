"""PO, receipt, and supplier-invoice evidence for selected inventory purchases."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
    fmt_money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)

PURCHASE_SUPPORT_TARGET = 3


def selected_inventory_procurement_movement_ids(world: World) -> tuple[str, ...]:
    """Select the largest receipts plus every year-end RNI receipt."""
    purchases = [
        row
        for row in world.get("inventory_movement") or []
        if row.get("movement_kind") == "purchase_receipt"
    ]
    rni_ids = {
        str(row["inventory_movement_id"]) for row in world.get("rni_accrual_item") or []
    }
    mandatory = sorted(
        (row for row in purchases if row["inventory_movement_id"] in rni_ids),
        key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
    )
    others = sorted(
        (row for row in purchases if row["inventory_movement_id"] not in rni_ids),
        key=lambda row: (
            -Decimal(str(row["extended_cost"])),
            row["inventory_movement_id"],
        ),
    )
    target = min(PURCHASE_SUPPORT_TARGET, len(purchases))
    selected = mandatory + others[: max(0, target - len(mandatory))]
    return tuple(str(row["inventory_movement_id"]) for row in selected)


def selected_inventory_procurement_invoice_ids(world: World) -> frozenset[str]:
    """Invoices rendered here instead of by the generic AP renderer."""
    selected = set(selected_inventory_procurement_movement_ids(world))
    return frozenset(
        str(row["vendor_invoice_id"])
        for row in world.get("vendor_invoice") or []
        if str(row.get("inventory_movement_id") or "") in selected
    )


def _safe(value: str) -> str:
    return " ".join("".join(c for c in value if c.isalnum() or c in " -_").split())


def _write_document(
    world: World,
    folder: Path,
    *,
    title: str,
    filename: str,
    heading: str,
    fields: list[tuple[str, Any]],
    support_type: str,
    movement: dict[str, Any],
    item: dict[str, Any],
    order: dict[str, Any],
    receipt: dict[str, Any],
    evidence_set_id: str,
    source_invoice: tuple[str, str] | None = None,
) -> dict[str, Any]:
    doc = TextDoc()
    doc.center(heading.upper())
    doc.center(title)
    doc.rule("=")
    for index, (label, value) in enumerate(fields):
        if index == 0:
            doc.tie(support_type, label, str(value))
        else:
            doc.pair(label, str(value))
    path = folder / filename
    payload = entry(
        "INV-06",
        "inventory",
        path,
        doc.write(path),
    )
    source_ids = {
        "inventory_movement_id": movement["inventory_movement_id"],
        "inventory_item_id": item["inventory_item_id"],
        "purchase_order_id": order["purchase_order_id"],
        "goods_receipt_id": receipt["goods_receipt_id"],
    }
    if source_invoice:
        source_ids[source_invoice[0]] = source_invoice[1]
    payload.update(
        support_type=support_type,
        evidence_set_id=evidence_set_id,
        source_record_ids=source_ids,
    )
    return payload


def _invoice_facts(
    world: World,
    po_number: str,
    invoice: dict[str, Any] | None,
    rni: dict[str, Any] | None,
) -> tuple[str, str, str, tuple[str, str]]:
    if invoice:
        return (
            str(invoice["vendor_invoice_number"]),
            str(invoice["document_date"]),
            f"Net 30 / {fmt_date(invoice['due_date'])}",
            ("vendor_invoice_id", str(invoice["vendor_invoice_id"])),
        )
    if not rni:
        raise ValueError(f"{po_number} has neither an invoice nor an RNI record")
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_ap_subsequent import (
        subsequent_open_invoice_settlements,
    )

    settlement = next(
        (
            row
            for row in subsequent_open_invoice_settlements(world)
            if row["vendor_invoice_id"] == rni["rni_accrual_item_id"]
        ),
        None,
    )
    if not settlement:
        raise ValueError(f"{po_number} RNI has no subsequent settlement evidence")
    return (
        str(settlement["invoice_number"]),
        str(rni["subsequent_invoice_date"]),
        f"Paid {fmt_date(settlement['payment_date'])}",
        ("rni_accrual_item_id", str(rni["rni_accrual_item_id"])),
    )


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    selected = selected_inventory_procurement_movement_ids(world)
    if not selected:
        return []
    index_specs = {
        "movement": ("inventory_movement", "inventory_movement_id", None),
        "item": ("inventory_item", "inventory_item_id", None),
        "order": ("purchase_order", "inventory_movement_id", "PO-STK-"),
        "receipt": ("goods_receipt", "inventory_movement_id", "GR-STK-"),
        "invoice": ("vendor_invoice", "inventory_movement_id", "VENDOR-INVOICE-STK-"),
        "rni": ("rni_accrual_item", "inventory_movement_id", None),
        "vendor": ("vendor", "vendor_id", None),
    }
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for name, (population, key, prefix) in index_specs.items():
        indexes[name] = {
            str(row[key]): row
            for row in world.get(population) or []
            if not prefix or str(row.get(f"{population}_id") or "").startswith(prefix)
        }
    public_ids = client_id_map(world)
    folder = output_dir / "inventory_purchase_support"
    rendered: list[dict[str, Any]] = []
    for movement_id in selected:
        movement = indexes["movement"][movement_id]
        order = indexes["order"].get(movement_id)
        receipt = indexes["receipt"].get(movement_id)
        item = indexes["item"].get(str(movement["inventory_item_id"]))
        if not order or not receipt or not item:
            raise ValueError(
                f"selected receipt {movement_id} lacks its PO/receipt/item chain"
            )
        vendor = indexes["vendor"].get(str(order["vendor_id"]))
        if not vendor:
            raise ValueError(f"selected receipt {movement_id} lacks its vendor")
        invoice = indexes["invoice"].get(movement_id)
        rni = indexes["rni"].get(movement_id)
        if (invoice is None) == (rni is None):
            raise ValueError(
                f"selected receipt {movement_id} has an invalid invoice state"
            )

        po_number = public_ids[order["purchase_order_id"]]
        receipt_number = public_ids[receipt["goods_receipt_id"]]
        common = dict(
            world=world,
            folder=folder,
            movement=movement,
            item=item,
            order=order,
            receipt=receipt,
            evidence_set_id=po_number,
        )
        rendered.append(
            _write_document(
                **common,
                title="PURCHASE ORDER",
                filename=f"{po_number} Purchase Order.txt",
                heading=company_name(world),
                fields=[
                    ("Purchase order", po_number),
                    ("Order date", fmt_date(order["order_date"])),
                    ("Supplier", vendor["name"]),
                    ("Item", f"{item['sku']} - {item['description']}"),
                    ("Quantity", f"{order['quantity']} {item['uom']}"),
                    ("Unit price", fmt_money(order["unit_price"])),
                    ("Total", fmt_money(order["total_amount"])),
                    ("Approved by", order["approved_by"]),
                ],
                support_type="purchase_order",
            )
        )
        rendered.append(
            _write_document(
                **common,
                title="GOODS RECEIVED NOTE",
                filename=f"{receipt_number} Goods Received Note.txt",
                heading=company_name(world),
                fields=[
                    ("Receiving number", receipt_number),
                    ("Purchase order", po_number),
                    ("Supplier", vendor["name"]),
                    ("Receipt date", fmt_date(receipt["receipt_date"])),
                    ("Item", f"{item['sku']} - {item['description']}"),
                    (
                        "Quantity received",
                        f"{receipt['quantity_received']} {item['uom']}",
                    ),
                    ("Received by", receipt["receiver_name"]),
                ],
                support_type="goods_receipt",
            )
        )
        invoice_number, invoice_date, terms, source_invoice = _invoice_facts(
            world, po_number, invoice, rni
        )
        rendered.append(
            _write_document(
                **common,
                title="SUPPLIER INVOICE",
                filename=f"{po_number} Supplier Invoice {_safe(invoice_number)}.txt",
                heading=vendor["name"],
                fields=[
                    ("Invoice number", invoice_number),
                    ("Invoice date", fmt_date(invoice_date)),
                    ("Customer", company_name(world)),
                    ("Purchase order", po_number),
                    ("Receiving reference", receipt_number),
                    ("Item", f"{item['sku']} - {item['description']}"),
                    ("Quantity", f"{movement['quantity']} {item['uom']}"),
                    ("Unit price", fmt_money(movement["unit_cost"])),
                    ("Amount due", fmt_money(movement["extended_cost"])),
                    ("Terms / settlement", terms),
                ],
                support_type="vendor_invoice",
                source_invoice=source_invoice,
            )
        )
    return rendered
