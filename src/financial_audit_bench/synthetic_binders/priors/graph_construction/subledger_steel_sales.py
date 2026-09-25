"""Shipment-backed steel sales and receivables alignment."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_ar_controls import (
    validate_accounts_receivable,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _allocate_amount,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    person_name,
)


def _align_steel_sales_to_shipments(
    records: dict[str, list[dict[str, Any]]],
    year_end: date,
) -> None:
    """Make billed quantities a view of the finished-goods shipments."""
    items = {row["inventory_item_id"]: row for row in records["inventory_items"]}
    shipments_by_month: dict[str, list[dict[str, Any]]] = {}
    for movement in records["inventory_movements"]:
        if movement["movement_kind"] != "sale_shipment":
            continue
        shipments_by_month.setdefault(movement["posting_date"][:7], []).append(movement)
    invoices = {row["customer_invoice_id"]: row for row in records["customer_invoices"]}
    lines_by_month: dict[str, list[dict[str, Any]]] = {}
    for line in records["customer_invoice_lines"]:
        invoice = invoices[line["customer_invoice_id"]]
        lines_by_month.setdefault(invoice["invoice_date"][:7], []).append(line)
    fulfillment_by_line = {
        row["customer_invoice_line_id"]: row for row in records["fulfillment_events"]
    }
    allocated_by_movement: dict[str, Decimal] = {}
    aligned_invoice_ids: set[str] = set()
    for month, shipments in sorted(shipments_by_month.items()):
        lines = sorted(
            lines_by_month.get(month, []),
            key=lambda row: row["customer_invoice_line_id"],
        )
        if not lines:
            raise ValueError(f"steel shipments in {month} have no sales invoices")
        ordered_shipments = sorted(
            (row for row in shipments if Decimal(str(row["quantity"])) > 0),
            key=lambda row: row["inventory_movement_id"],
        )
        if not ordered_shipments:
            continue
        total_quantity = sum(
            (Decimal(row["quantity"]) for row in ordered_shipments), Decimal("0")
        )
        line_quantities = _allocate_amount(
            total_quantity,
            [Decimal(line["gross_amount"]) for line in lines],
        )
        remaining = [
            [movement, Decimal(movement["quantity"])] for movement in ordered_shipments
        ]
        for line, quantity in zip(lines, line_quantities, strict=True):
            if quantity <= 0:
                continue
            chunks: list[tuple[dict[str, Any], Decimal]] = []
            need = quantity
            while need > 0:
                if not remaining:
                    raise ValueError("steel invoice allocations exceed shipments")
                movement, available = remaining[0]
                take = min(need, available)
                chunks.append((movement, take))
                movement_id = movement["inventory_movement_id"]
                allocated_by_movement[movement_id] = (
                    allocated_by_movement.get(movement_id, Decimal("0")) + take
                )
                need -= take
                available -= take
                if available == 0:
                    remaining.pop(0)
                else:
                    remaining[0][1] = available
            descriptions = list(
                dict.fromkeys(
                    items[movement["inventory_item_id"]]["description"]
                    for movement, _ in chunks
                )
            )
            line_sequence = int(str(line["customer_invoice_line_id"]).rsplit("-", 1)[1])
            grades = ("ASTM A1011", "ASTM A36", "commercial quality")
            services = (
                "cut to length",
                "slit and edge conditioned",
                "pickled and oiled",
                "mill finish",
            )
            line["goods_services"] = (
                f"{descriptions[(line_sequence - 1) % len(descriptions)]}; "
                f"{grades[line_sequence % len(grades)]}; "
                f"{services[line_sequence % len(services)]}"
            )
            line["quantity"] = str(quantity)
            line["uom"] = "ton"
            fulfillment = fulfillment_by_line[line["customer_invoice_line_id"]]
            shipment_date = max(movement["posting_date"] for movement, _ in chunks)
            fulfillment["fulfillment_date"] = shipment_date
            fulfillment["fulfillment_kind"] = "shipment"
            fulfillment["quantity_fulfilled"] = str(quantity)
            fulfillment["support_reference"] = " / ".join(
                f"{movement['source_document']} ({amount} tons)"
                for movement, amount in chunks
            )
            fulfillment["delivery_document_reference"] = fulfillment[
                "support_reference"
            ]
            fulfillment["acceptance_reference"] = (
                f"POD-{shipment_date[2:4]}-{8000 + line_sequence:04d}"
            )
            fulfillment["acceptance_date"] = shipment_date
            fulfillment["acceptance_status"] = "accepted"
            fulfillment["customer_acknowledgment"] = "signed proof of delivery"
            fulfillment["performance_evidence_status"] = "complete"
            invoice = invoices[line["customer_invoice_id"]]
            for movement, _amount in chunks:
                for field, source_id in (
                    ("customer_invoice_ids", line["customer_invoice_id"]),
                    ("customer_invoice_line_ids", line["customer_invoice_line_id"]),
                    ("fulfillment_event_ids", fulfillment["fulfillment_event_id"]),
                    ("customer_ids", invoice["customer_id"]),
                ):
                    typed_ids = movement.setdefault(field, [])
                    if source_id not in typed_ids:
                        typed_ids.append(source_id)
            # Keep the original invoice economics. The public package needs a supported
            # per-ton price, not a synthetic decomposition whose rounding then forces
            # the entire A/R book to be rebuilt.
            price_per_ton = Decimal(str(line["net_amount"])) / quantity
            line.update(
                {
                    "unit_price": str(price_per_ton),
                    "customer_purchase_order_reference": (
                        f"PO-{shipment_date[2:4]}-{7000 + line_sequence:04d}"
                    ),
                    "sales_order_reference": (
                        f"SO-{shipment_date[:4]}-{30000 + line_sequence:05d}"
                    ),
                    "quote_reference": (
                        f"QT-{shipment_date[2:4]}-{1000 + line_sequence:04d}"
                    ),
                    "base_metal_price_per_unit": str(price_per_ton),
                    "grade_dimension_adder_per_unit": "0",
                    "processing_charge_per_unit": "0",
                    "freight_per_unit": "0",
                    "discount_per_unit": "0",
                    "pricing_approved_by": (
                        f"{person_name('steel-pricing-approver')}, Commercial Director"
                    ),
                }
            )
            aligned_invoice_ids.add(str(invoice["customer_invoice_id"]))
        if remaining:
            raise ValueError("steel shipments remain unallocated to invoices")
    for rows in shipments_by_month.values():
        for movement in rows:
            if allocated_by_movement.get(
                movement["inventory_movement_id"], Decimal("0")
            ) != Decimal(movement["quantity"]):
                raise ValueError("steel shipment-to-invoice assignment is incomplete")

    # Refresh selected cutoff evidence from the aligned fulfillment rows.
    line_by_invoice = {
        str(row["customer_invoice_id"]): row
        for row in records["customer_invoice_lines"]
    }
    for cutoff in records["ar_cutoff_items"]:
        invoice_id = str(cutoff["customer_invoice_id"])
        if invoice_id not in aligned_invoice_ids:
            continue
        invoice = invoices[invoice_id]
        line = line_by_invoice[invoice_id]
        fulfillment = fulfillment_by_line[str(line["customer_invoice_line_id"])]
        cutoff.update(
            {
                "customer_id": invoice["customer_id"],
                "customer_invoice_line_id": line["customer_invoice_line_id"],
                "fulfillment_event_id": fulfillment["fulfillment_event_id"],
                "amount": invoice["original_amount"],
                "invoice_date": invoice["invoice_date"],
                "posting_date": invoice["posting_date"],
                "fulfillment_date": fulfillment["fulfillment_date"],
                "recognition_date": fulfillment["fulfillment_date"],
                "support_reference": fulfillment["support_reference"],
                "customer_purchase_order_reference": line[
                    "customer_purchase_order_reference"
                ],
                "sales_order_reference": line["sales_order_reference"],
                "approved_quote_reference": line["quote_reference"],
                "quantity": line["quantity"],
                "delivery_terms": fulfillment["delivery_terms"],
                "cutoff_status": (
                    "in_period"
                    if date.fromisoformat(str(fulfillment["fulfillment_date"]))
                    <= year_end
                    and date.fromisoformat(str(invoice["posting_date"])) <= year_end
                    else "exception"
                ),
            }
        )

    validate_accounts_receivable(records, year_end)
