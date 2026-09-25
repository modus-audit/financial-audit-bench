"""Inventory movement rows and received-not-invoiced cutoff receipts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _money,
)


def _movement(
    index: int,
    item: dict[str, Any],
    location: dict[str, Any],
    kind: str,
    direction: str,
    when: date,
    quantity: int,
    unit: Decimal,
    cost: Decimal,
    source: str,
    execution_policy: Mapping[str, Any],
) -> dict[str, Any]:
    row_policy = execution_policy["movement_row_contract"]
    inbound = direction == "in"
    internal_usage = kind in row_policy["internal_usage_kinds"]
    inbound_lead_days = int(
        execution_policy["monthly_movements"]["purchase_receipts"][
            "ordinary_shipment_lead_calendar_days"
        ]
    )
    return {
        "cutoff_status": row_policy["cutoff_status"],
        "direction": direction,
        "extended_cost": str(cost),
        "material_cost": None,
        "direct_labor_cost": None,
        "production_overhead_cost": None,
        "conversion_labor_added": None,
        "conversion_overhead_added": None,
        "source_cost_layer_id": None,
        "fifo_layers_consumed": None,
        "in_transit_status": (
            row_policy["inbound_status"]
            if inbound
            else row_policy["internal_usage_status"]
            if internal_usage
            else row_policy["outbound_status"]
        ),
        "inventory_item_id": item["inventory_item_id"],
        "inventory_location_id": location["inventory_location_id"],
        "inventory_movement_id": str(row_policy["movement_id_format"]).format(
            sequence_4=f"{index:04d}"
        ),
        "invoice_date": when.isoformat() if inbound else None,
        "movement_kind": kind,
        "vendor_id": None,
        "purchase_order_id": None,
        "goods_receipt_id": None,
        "vendor_invoice_id": None,
        "rni_accrual_item_id": None,
        "customer_invoice_ids": [],
        "customer_invoice_line_ids": [],
        "fulfillment_event_ids": [],
        "customer_ids": [],
        "ownership_transfer_date": when.isoformat(),
        "posting_date": when.isoformat(),
        "shipping_terms": (
            row_policy["inbound_shipping_terms"]
            if inbound
            else row_policy["internal_shipping_terms"]
            if internal_usage
            else row_policy["outbound_shipping_terms"]
        ),
        "quantity": str(quantity),
        "freight_duty_amount": row_policy["freight_duty_amount"],
        "discount_amount": row_policy["discount_amount"],
        "receipt_date": when.isoformat() if inbound else None,
        "shipment_date": (
            (when - timedelta(days=inbound_lead_days)).isoformat()
            if inbound
            else None
            if internal_usage
            else when.isoformat()
        ),
        "source_document": source,
        "production_order_number": None,
        "unit_cost": str(unit),
    }


def _add_rni_receipts(
    records: dict[str, list[dict[str, Any]]],
    items: list[dict[str, Any]],
    year_end: date,
    inventory_policy: Mapping[str, Any],
    execution_policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Received-not-invoiced raw-material receipts in the final December days."""
    candidates = [item for item in items if item["category"] == "raw_material"]
    if not candidates:
        return []
    max_index = max(
        (
            int(row["inventory_movement_id"].rsplit("-", 1)[-1])
            for row in records["inventory_movements"]
        ),
        default=0,
    )

    location = records["inventory_locations"][0]
    added: list[dict[str, Any]] = []
    receipt_count = int(
        execution_policy["received_not_invoiced"]["year_end_carry_out_documents"]
    )
    shipment_days = int(inventory_policy["rni_shipment_days_before_year_end"])
    receipt_days = int(inventory_policy["rni_receipt_days_before_year_end"])
    invoice_days = int(inventory_policy["rni_invoice_days_after_year_end"])
    for offset, item in enumerate(candidates[-receipt_count:], start=1):
        received = year_end - timedelta(days=receipt_days)
        shipped = year_end - timedelta(days=shipment_days)
        invoiced = year_end + timedelta(days=invoice_days)
        if not shipped < received <= year_end < invoiced:
            raise ValueError("RNI cutoff dates do not straddle fiscal end")
        unit = Decimal(item["standard_cost"])
        quantity = max(
            int(execution_policy["received_not_invoiced"]["minimum_quantity"]),
            int(
                (
                    Decimal(str(inventory_policy["rni_receipt_target_value"])) / unit
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            ),
        )
        cost = _money(quantity * unit)
        max_index += 1
        row = _movement(
            max_index,
            item,
            location,
            "purchase_receipt",
            "in",
            received,
            quantity,
            unit,
            cost,
            source=str(
                execution_policy["received_not_invoiced"]["source_reference_format"]
            ).format(
                fiscal_end_year=year_end.year,
                document_ordinal_3=f"{offset:03d}",
            ),
            execution_policy=execution_policy,
        )
        row.update(
            {
                "in_transit_status": execution_policy["received_not_invoiced"][
                    "in_transit_status"
                ],
                "invoice_date": invoiced.isoformat(),
                "shipment_date": shipped.isoformat(),
            }
        )
        records["inventory_movements"].append(row)
        added.append(row)
    return added


def _apply_rni_receipt_state(
    per_item: dict[str, dict[str, Any]],
    rni_rows: list[dict[str, Any]],
    include_cost: bool = True,
) -> None:
    """Fold the RNI receipts into the per-item rollforward accumulators."""
    for row in rni_rows:
        state = per_item[row["inventory_item_id"]]
        state["purchase_qty"] += int(Decimal(row["quantity"]))
        if include_cost:
            state["purchase_cost"] += Decimal(row["extended_cost"])


__all__: tuple[str, ...] = ()
