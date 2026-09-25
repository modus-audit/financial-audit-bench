"""Render a compact INV-10 inventory cutoff schedule."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _add_schedule_sheet,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    open_pbc_sheet,
    save_workbook,
    write_header,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)

World = dict[str, Any]


def render_inventory_cutoff_population(
    path: Path, world: World, title: str
) -> list[dict[str, Any]] | None:
    """Render five receipts and shipments on each side of year end."""
    movements = world.get("inventory_movement") or []
    receipts = _latest(movements, "purchase_receipt")
    shipments = _latest(movements, "sale_shipment")
    if not receipts and not shipments:
        return None
    if len(receipts) < 5 or len(shipments) < 5:
        raise ValueError("INV-10 requires five receipts and shipments")

    client_ids = client_id_map(world)
    vendors = {row["vendor_id"]: row["name"] for row in world.get("vendor") or []}
    customers = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer") or []
    }
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    invoice_lines = {
        row["customer_invoice_line_id"]: row
        for row in world.get("customer_invoice_line") or []
    }
    fulfillments = {
        row["fulfillment_event_id"]: row for row in world.get("fulfillment_event") or []
    }
    vendor_invoices = {
        row["goods_receipt_id"]: row
        for row in world.get("vendor_invoice") or []
        if row.get("goods_receipt_id")
    }
    rni = {
        row["inventory_movement_id"]: row for row in world.get("rni_accrual_item") or []
    }

    def public(value: Any) -> str:
        key = str(value or "")
        return str(client_ids.get(key, key))

    def source_row(movement: dict[str, Any]) -> dict[str, Any]:
        movement_id = str(movement["inventory_movement_id"])
        if movement["movement_kind"] == "purchase_receipt":
            invoice = vendor_invoices.get(str(movement.get("goods_receipt_id") or ""))
            accrual = rni.get(movement_id)
            if invoice is None and accrual is None:
                raise ValueError(
                    f"INV-10 receipt {movement_id} lacks AP or RNI support"
                )
            invoice_date = (
                invoice["document_date"]
                if invoice
                else accrual["subsequent_invoice_date"]
            )
            return _row(
                movement,
                public(movement_id),
                "Receipt",
                vendors.get(
                    movement.get("vendor_id"), movement.get("vendor_reference", "")
                ),
                public(movement.get("purchase_order_id")),
                public(movement.get("goods_receipt_id")),
                public(invoice["vendor_invoice_id"]) if invoice else "Invoice pending",
                invoice_date,
                invoice["posting_date"][:7] if invoice else invoice_date[:7],
                "RNI accrued at year end" if accrual else "Matched to AP invoice",
            )

        invoice_ids = sorted(
            str(value) for value in movement.get("customer_invoice_ids") or []
        )
        linked_invoices = [
            invoices[value] for value in invoice_ids if value in invoices
        ]
        line_ids = sorted(
            str(value) for value in movement.get("customer_invoice_line_ids") or []
        )
        linked_lines = [
            invoice_lines[value] for value in line_ids if value in invoice_lines
        ]
        fulfillment_ids = sorted(
            str(value) for value in movement.get("fulfillment_event_ids") or []
        )
        linked_fulfillments = [
            fulfillments[value] for value in fulfillment_ids if value in fulfillments
        ]
        customer_names = [
            customers[value]
            for value in sorted(
                str(value) for value in movement.get("customer_ids") or []
            )
            if value in customers
        ]
        if (
            not linked_invoices
            or not linked_lines
            or not linked_fulfillments
            or not customer_names
        ):
            raise ValueError(f"INV-10 shipment {movement_id} lacks sales support")
        orders = sorted({str(row["sales_order_reference"]) for row in linked_lines})
        deliveries = sorted(
            str(row.get("delivery_document_reference") or row["support_reference"])
            for row in linked_fulfillments
        )
        invoice_date = max(str(row["invoice_date"]) for row in linked_invoices)
        posting_periods = sorted(
            {str(row["posting_date"])[:7] for row in linked_invoices}
        )
        return _row(
            movement,
            public(movement_id),
            "Shipment",
            " / ".join(customer_names),
            " / ".join(orders),
            " / ".join(deliveries),
            " / ".join(str(row["invoice_number"]) for row in linked_invoices),
            invoice_date,
            " / ".join(posting_periods),
            "Matched to revenue invoice and delivery support",
        )

    before = [source_row(row) for row in receipts[-5:] + shipments[-5:]]
    missing_rni = {public(value) for value in rni} - {
        row["movement_id"] for row in before
    }
    if missing_rni:
        raise ValueError(f"INV-10 omits year-end RNI movements: {sorted(missing_rni)}")

    year_end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    after = [_subsequent(row, year_end, index) for index, row in enumerate(before, 1)]
    rows = before + after

    wb, detail = open_pbc_sheet(title, "INV-10", world)
    detail.title = "Cutoff Population"
    write_header(
        detail,
        [
            "Movement ID",
            "Direction",
            "Period Side",
            "Transaction Date",
            "Shipment / Receipt Date",
            "Ownership Transfer Date",
            "Terms",
            "Counterparty",
            "PO / Sales Order",
            "GRN / BOL",
            "Invoice",
            "Invoice Date",
            "Quantity",
            "Amount",
            "Inventory Period",
            "AP / Revenue Period",
            "Cross-ledger Status",
        ],
    )
    for row in sorted(
        rows, key=lambda value: (value["transaction_date"], value["direction"])
    ):
        detail.append(list(row.values()))

    controls = _add_schedule_sheet(wb, "Population Controls", world)
    write_header(controls, ["Direction", "Period Side", "Count", "Amount"])
    for direction in ("Receipt", "Shipment"):
        for side in ("Before year end", "After year end"):
            selected = [
                row
                for row in rows
                if row["direction"] == direction and row["period_side"] == side
            ]
            controls.append([direction, side, len(selected), _sum(selected)])
    controls.append(["Total", "", len(rows), _sum(rows)])
    save_workbook(wb, path)
    return []


def _latest(movements: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return sorted(
        (row for row in movements if row.get("movement_kind") == kind),
        key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
    )


def _number(value: Any) -> int | float:
    amount = Decimal(str(value))
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def _row(
    movement: dict[str, Any],
    movement_id: str,
    direction: str,
    counterparty: str,
    order: str,
    physical_support: str,
    invoice: str,
    invoice_date: str,
    counterledger_period: str,
    status: str,
) -> dict[str, Any]:
    physical_date = movement.get("receipt_date") or movement.get("shipment_date")
    return {
        "movement_id": movement_id,
        "direction": direction,
        "period_side": "Before year end",
        "transaction_date": movement["posting_date"],
        "physical_date": physical_date,
        "ownership_date": movement["ownership_transfer_date"],
        "terms": movement["shipping_terms"],
        "counterparty": counterparty,
        "po_or_order": order,
        "grn_or_bol": physical_support,
        "invoice": invoice,
        "invoice_date": invoice_date,
        "quantity": _number(movement["quantity"]),
        "amount": _number(movement["extended_cost"]),
        "inventory_period": movement["posting_date"][:7],
        "counterledger_period": counterledger_period,
        "status": status,
    }


def _subsequent(row: dict[str, Any], year_end: date, index: int) -> dict[str, Any]:
    """Make a small next-period comparison row from a selected source row."""
    event_date = year_end + timedelta(days=((index - 1) % 5) + 1)
    year = event_date.year
    number = ((index - 1) % 5) + 1
    receipt = row["direction"] == "Receipt"
    return {
        **row,
        "movement_id": f"{'RCV' if receipt else 'SHP'}-MOV-{year}-{number:04d}",
        "period_side": "After year end",
        "transaction_date": event_date.isoformat(),
        "physical_date": event_date.isoformat(),
        "ownership_date": event_date.isoformat(),
        "po_or_order": f"{'PO' if receipt else 'SO'}-{year}-{number:04d}",
        "grn_or_bol": f"{'GRN' if receipt else 'BOL'}-{year}-{number:04d}",
        "invoice": f"{'VINV' if receipt else 'SINV'}-{year}-{number:04d}",
        "invoice_date": event_date.isoformat(),
        "inventory_period": event_date.strftime("%Y-%m"),
        "counterledger_period": event_date.strftime("%Y-%m"),
        "status": "Matched to subsequent-period source support",
    }


def _sum(rows: list[dict[str, Any]]) -> Decimal:
    return sum((Decimal(str(row["amount"])) for row in rows), Decimal("0.00"))
