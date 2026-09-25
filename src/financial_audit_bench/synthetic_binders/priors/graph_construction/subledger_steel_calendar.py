"""Monthly steel production calendar regularization and order construction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _allocate_amount,
    _allocate_units,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_movements import (
    _movement,
)


def _regularize_steel_production_calendar(
    records: dict[str, list[dict[str, Any]]],
    items: list[dict[str, Any]],
    per_item: dict[str, dict[str, Any]],
    fiscal_year: int,
    location: dict[str, Any],
    steel_policy: Mapping[str, Any],
    inventory_execution_policy: Mapping[str, Any],
) -> None:
    """Rebuild steel movements on one monthly production-order calendar."""
    if not items:
        return
    movements = records["inventory_movements"]
    item_ids = {row["inventory_item_id"] for row in items}
    old_by_item: dict[str, list[dict[str, Any]]] = {}
    for row in movements:
        if row["inventory_item_id"] in item_ids:
            old_by_item.setdefault(row["inventory_item_id"], []).append(row)
    year = fiscal_year
    weights = [Decimal(str(value)) for value in steel_policy["monthly_weights"]]
    stage_days = {key: int(value) for key, value in steel_policy["stage_days"].items()}
    movement_execution = steel_policy["movement_execution"]

    def business_date(month: int, day_of_month: int) -> date:
        # Use the configured day directly, without weekend or holiday adjustment.
        return date(year, month, day_of_month)

    def production_order_id(month: int) -> str:
        return str(movement_execution["production_order_number_format"]).format(
            year_2=f"{year % 100:02d}", month_2=f"{month:02d}"
        )

    def plan_quantity(total: int) -> list[int]:
        return _allocate_units(total, weights)

    def constrain_outflow(
        opening: int, inbound: list[int], outbound: list[int]
    ) -> list[int]:
        stock = opening
        deferred = 0
        result: list[int] = []
        for incoming, requested in zip(inbound, outbound, strict=True):
            available = stock + incoming
            requested += deferred
            actual = min(requested, available)
            deferred = requested - actual
            result.append(actual)
            stock = available - actual
        if deferred:
            raise ValueError("steel annual outflow exceeds available item quantity")
        return result

    rebuilt: list[dict[str, Any]] = [
        row for row in movements if row["inventory_item_id"] not in item_ids
    ]
    movement_sequence = len(rebuilt)
    for item in items:
        item_id = item["inventory_item_id"]
        category = item["category"]
        incoming_kind = (
            "purchase_receipt"
            if category == "raw_material"
            else "production_completion"
        )
        outgoing_kind = (
            "sale_shipment" if category == "finished_goods" else "production_issue"
        )
        original = old_by_item.get(item_id, [])
        incoming_rows = [
            row for row in original if row["movement_kind"] == incoming_kind
        ]
        outgoing_rows = [
            row for row in original if row["movement_kind"] == outgoing_kind
        ]
        state = per_item[item_id]
        incoming_quantity = sum(int(row["quantity"]) for row in incoming_rows) or int(
            state["purchase_qty"] + state.get("production_qty", 0)
        )
        outgoing_quantity = sum(int(row["quantity"]) for row in outgoing_rows) or int(
            state["sales_qty"]
        )
        incoming_cost = sum(
            (Decimal(str(row["extended_cost"])) for row in incoming_rows),
            Decimal("0.00"),
        ) or Decimal(state["purchase_cost"] + state.get("production_cost", 0))
        outgoing_cost = sum(
            (Decimal(str(row["extended_cost"])) for row in outgoing_rows),
            Decimal("0.00"),
        ) or Decimal(state["sales_cost"])
        incoming_plan = plan_quantity(incoming_quantity)
        outgoing_plan = constrain_outflow(
            int(per_item[item_id]["opening_qty"]),
            incoming_plan,
            plan_quantity(outgoing_quantity),
        )
        incoming_costs = _allocate_amount(
            incoming_cost, [Decimal(value) for value in incoming_plan]
        )
        outgoing_costs = _allocate_amount(
            outgoing_cost, [Decimal(value) for value in outgoing_plan]
        )
        if incoming_rows:
            fallback_exemplar = incoming_rows[0]
        elif outgoing_rows:
            fallback_exemplar = outgoing_rows[0]
        else:
            # Build a complete template when the stage has no exemplar row.
            fallback_exemplar = _movement(
                0,
                item,
                location,
                incoming_kind,
                "in",
                date(year, 1, 1),
                0,
                Decimal(item["standard_cost"]),
                Decimal("0.00"),
                movement_execution["de_novo_template_source"],
                inventory_execution_policy,
            )

        for direction, kind, quantities, costs, exemplar in (
            (
                "in",
                incoming_kind,
                incoming_plan,
                incoming_costs,
                incoming_rows[0] if incoming_rows else fallback_exemplar,
            ),
            (
                "out",
                outgoing_kind,
                outgoing_plan,
                outgoing_costs,
                outgoing_rows[0] if outgoing_rows else fallback_exemplar,
            ),
        ):
            for month, (quantity, cost) in enumerate(
                zip(quantities, costs, strict=True), start=1
            ):
                if not quantity:
                    continue
                production_order_number = production_order_id(month)
                if kind == "purchase_receipt":
                    when = business_date(month, stage_days["purchase_receipt"])
                    source = str(
                        movement_execution["purchase_receipt_source_format"]
                    ).format(
                        year_4=f"{year:04d}",
                        month_2=f"{month:02d}",
                        item_suffix_3=item_id[-3:],
                    )
                    order_link = None
                elif kind == "production_completion" and category == "work_in_process":
                    when = business_date(month, stage_days["wip_completion"])
                    source = str(
                        movement_execution["wip_completion_source_format"]
                    ).format(production_order_number=production_order_number)
                    order_link = production_order_number
                elif kind == "production_issue":
                    day_of_month = (
                        stage_days["raw_issue"]
                        if category == "raw_material"
                        else stage_days["wip_issue"]
                    )
                    when = business_date(month, day_of_month)
                    source = str(
                        movement_execution[
                            "raw_issue_source_format"
                            if category == "raw_material"
                            else "wip_issue_source_format"
                        ]
                    ).format(production_order_number=production_order_number)
                    order_link = production_order_number
                elif kind == "production_completion":
                    when = business_date(month, stage_days["finished_completion"])
                    source = str(
                        movement_execution["finished_completion_source_format"]
                    ).format(production_order_number=production_order_number)
                    order_link = production_order_number
                else:
                    when = business_date(month, stage_days["shipment"])
                    source = str(movement_execution["shipment_source_format"]).format(
                        production_order_number=production_order_number,
                        item_suffix_3=item_id[-3:],
                    )
                    order_link = production_order_number
                movement_sequence += 1
                row = dict(exemplar)
                row.update(
                    {
                        "direction": direction,
                        "extended_cost": str(cost),
                        "in_transit_status": movement_execution[
                            "in_transit_status_by_kind"
                        ][kind],
                        "inventory_item_id": item_id,
                        "inventory_movement_id": f"MOV-{movement_sequence:04d}",
                        "invoice_date": (
                            when.isoformat() if kind == "purchase_receipt" else None
                        ),
                        "movement_kind": kind,
                        "ownership_transfer_date": when.isoformat(),
                        "posting_date": when.isoformat(),
                        "production_order_number": order_link,
                        "quantity": str(quantity),
                        "receipt_date": (
                            when.isoformat() if direction == "in" else None
                        ),
                        "shipment_date": (
                            when.isoformat() if kind == "sale_shipment" else None
                        ),
                        "shipping_terms": movement_execution["shipping_terms_by_kind"][
                            kind
                        ],
                        "source_document": source,
                        "unit_cost": str(
                            (cost / Decimal(quantity)).quantize(Decimal("0.0001"))
                        ),
                    }
                )
                rebuilt.append(row)

    def rebalance_stage_output(
        input_category: str,
        input_kind: str,
        output_category: str,
        output_kind: str,
        output_day: int,
        source_format: str,
    ) -> None:
        """Move rounding-only output excess into a month with input capacity."""
        nonlocal movement_sequence

        def totals(category: str, kind: str) -> list[int]:
            values = [0] * 12
            for row in rebuilt:
                if (
                    row["movement_kind"] == kind
                    and items_by_id[row["inventory_item_id"]]["category"] == category
                ):
                    values[int(row["posting_date"][5:7]) - 1] += int(row["quantity"])
            return values

        input_totals = totals(input_category, input_kind)
        output_totals = totals(output_category, output_kind)
        for source_month in range(12):
            excess = output_totals[source_month] - input_totals[source_month]
            if excess <= 0:
                continue
            target_months = sorted(
                (
                    month
                    for month in range(12)
                    if input_totals[month] > output_totals[month]
                ),
                key=lambda month: (month < source_month, abs(month - source_month)),
            )
            source_rows = [
                row
                for row in rebuilt
                if row["movement_kind"] == output_kind
                and items_by_id[row["inventory_item_id"]]["category"] == output_category
                and int(row["posting_date"][5:7]) - 1 == source_month
            ]
            for target_month in target_months:
                capacity = input_totals[target_month] - output_totals[target_month]
                while excess > 0 and capacity > 0 and source_rows:
                    row = source_rows[-1]
                    row_quantity = int(row["quantity"])
                    moved_quantity = min(excess, capacity, row_quantity)
                    moved_cost = (
                        Decimal(row["extended_cost"])
                        * Decimal(moved_quantity)
                        / Decimal(row_quantity)
                    ).quantize(Decimal("0.01"))
                    if moved_quantity == row_quantity:
                        moved = row
                        source_rows.pop()
                    else:
                        movement_sequence += 1
                        moved = dict(row)
                        moved["inventory_movement_id"] = f"MOV-{movement_sequence:04d}"
                        moved["quantity"] = str(moved_quantity)
                        moved["extended_cost"] = str(moved_cost)
                        moved["unit_cost"] = str(
                            (moved_cost / Decimal(moved_quantity)).quantize(
                                Decimal("0.0001")
                            )
                        )
                        rebuilt.append(moved)
                        row["quantity"] = str(row_quantity - moved_quantity)
                        row["extended_cost"] = str(
                            Decimal(row["extended_cost"]) - moved_cost
                        )
                        row["unit_cost"] = str(
                            (
                                Decimal(row["extended_cost"]) / Decimal(row["quantity"])
                            ).quantize(Decimal("0.0001"))
                        )
                    month_number = target_month + 1
                    when = business_date(month_number, output_day)
                    order_id = production_order_id(month_number)
                    moved["posting_date"] = when.isoformat()
                    moved["ownership_transfer_date"] = when.isoformat()
                    moved["receipt_date"] = when.isoformat()
                    moved["production_order_number"] = order_id
                    moved["source_document"] = str(source_format).format(
                        production_order_number=order_id
                    )
                    output_totals[source_month] -= moved_quantity
                    output_totals[target_month] += moved_quantity
                    excess -= moved_quantity
                    capacity -= moved_quantity
                if excess == 0:
                    break
            if excess:
                raise ValueError("unable to rebalance steel monthly stage output")

    items_by_id = {row["inventory_item_id"]: row for row in items}
    rebalance_stage_output(
        "raw_material",
        "production_issue",
        "work_in_process",
        "production_completion",
        stage_days["wip_completion"],
        movement_execution["wip_completion_source_format"],
    )

    def rephase_outflows(
        category: str, kind: str, day: int, source_format: str
    ) -> None:
        """Rebuild outflows after upstream stage rows move between months."""
        nonlocal rebuilt, movement_sequence
        prior = [
            row
            for row in rebuilt
            if row["movement_kind"] == kind
            and items_by_id[row["inventory_item_id"]]["category"] == category
        ]
        rebuilt = [row for row in rebuilt if row not in prior]
        for item in (row for row in items if row["category"] == category):
            item_id = item["inventory_item_id"]
            item_rows = [row for row in prior if row["inventory_item_id"] == item_id]
            quantity_total = sum((int(row["quantity"]) for row in item_rows), 0)
            if not quantity_total:
                continue
            inbound = [0] * 12
            for row in rebuilt:
                if (
                    row["inventory_item_id"] == item_id
                    and row["movement_kind"] == "production_completion"
                ):
                    inbound[int(row["posting_date"][5:7]) - 1] += int(row["quantity"])
            plan = constrain_outflow(
                int(per_item[item_id]["opening_qty"]),
                inbound,
                plan_quantity(quantity_total),
            )
            active_months = [index for index, quantity in enumerate(plan) if quantity]
            costs = _allocate_amount(
                sum(
                    (Decimal(row["extended_cost"]) for row in item_rows),
                    Decimal("0.00"),
                ),
                [Decimal(plan[index]) for index in active_months],
            )
            for month_index, cost in zip(active_months, costs, strict=True):
                quantity = plan[month_index]
                when = business_date(month_index + 1, day)
                order_id = production_order_id(month_index + 1)
                movement_sequence += 1
                row = dict(item_rows[0])
                row.update(
                    {
                        "direction": "out",
                        "extended_cost": str(cost),
                        "material_cost": None,
                        "direct_labor_cost": None,
                        "production_overhead_cost": None,
                        "conversion_labor_added": None,
                        "conversion_overhead_added": None,
                        "source_cost_layer_id": None,
                        "fifo_layers_consumed": None,
                        "in_transit_status": movement_execution[
                            "in_transit_status_by_kind"
                        ][kind],
                        "inventory_movement_id": f"MOV-{movement_sequence:04d}",
                        "invoice_date": None,
                        "movement_kind": kind,
                        "ownership_transfer_date": when.isoformat(),
                        "posting_date": when.isoformat(),
                        "production_order_number": order_id,
                        "quantity": str(quantity),
                        "receipt_date": None,
                        "shipment_date": when.isoformat()
                        if kind == "sale_shipment"
                        else None,
                        "shipping_terms": movement_execution["shipping_terms_by_kind"][
                            kind
                        ],
                        "source_document": str(source_format).format(
                            production_order_number=order_id,
                            item_suffix_3=item_id[-3:],
                        ),
                        "unit_cost": str(
                            (cost / Decimal(quantity)).quantize(Decimal("0.0001"))
                        ),
                    }
                )
                rebuilt.append(row)

    rephase_outflows(
        "work_in_process",
        "production_issue",
        stage_days["wip_issue"],
        movement_execution["wip_issue_source_format"],
    )

    rebalance_stage_output(
        "work_in_process",
        "production_issue",
        "finished_goods",
        "production_completion",
        stage_days["finished_completion"],
        movement_execution["finished_completion_source_format"],
    )

    rephase_outflows(
        "finished_goods",
        "sale_shipment",
        stage_days["shipment"],
        movement_execution["shipment_source_format"],
    )

    records["inventory_movements"] = rebuilt

    def monthly_quantity(month: int, category: str, kind: str) -> Decimal:
        prefix = f"{year}-{month:02d}"
        return sum(
            (
                Decimal(row["quantity"])
                for row in rebuilt
                if row["posting_date"].startswith(prefix)
                and row["movement_kind"] == kind
                and items_by_id[row["inventory_item_id"]]["category"] == category
            ),
            Decimal("0"),
        )

    for month in range(1, 13):
        raw_issued = monthly_quantity(month, "raw_material", "production_issue")
        wip_completed = monthly_quantity(
            month, "work_in_process", "production_completion"
        )
        wip_issued = monthly_quantity(month, "work_in_process", "production_issue")
        finished_completed = monthly_quantity(
            month, "finished_goods", "production_completion"
        )
        shipped = monthly_quantity(month, "finished_goods", "sale_shipment")
        if wip_completed > raw_issued or finished_completed > wip_issued:
            raise ValueError("steel monthly production stage output exceeds input")
        records["steel_production_orders"].append(
            {
                "production_order_number": production_order_id(month),
                "production_month": f"{year}-{month:02d}",
                "raw_material_issue_date": business_date(
                    month, stage_days["raw_issue"]
                ).isoformat(),
                "wip_completion_date": business_date(
                    month, stage_days["wip_completion"]
                ).isoformat(),
                "wip_issue_date": business_date(
                    month, stage_days["wip_issue"]
                ).isoformat(),
                "finished_goods_completion_date": business_date(
                    month, stage_days["finished_completion"]
                ).isoformat(),
                "shipment_date": business_date(
                    month, stage_days["shipment"]
                ).isoformat(),
                "raw_material_issued_quantity": str(raw_issued),
                "wip_completed_quantity": str(wip_completed),
                "wip_issued_quantity": str(wip_issued),
                "finished_goods_completed_quantity": str(finished_completed),
                "finished_goods_shipped_quantity": str(shipped),
                "raw_to_wip_loss_quantity": str(raw_issued - wip_completed),
                "wip_to_finished_loss_quantity": str(wip_issued - finished_completed),
                "raw_material_cost": "0.00",
                "wip_conversion_labor_cost": "0.00",
                "wip_conversion_overhead_cost": "0.00",
                "wip_completed_cost": "0.00",
                "raw_stage_machine_hours": "0.00",
                "direct_labor_rate": str(steel_policy["labor_rate"]),
                "overhead_rate": str(steel_policy["overhead_rate"]),
                "wip_issued_cost": "0.00",
                "finishing_labor_cost": "0.00",
                "finishing_overhead_cost": "0.00",
                "finished_goods_completed_cost": "0.00",
                "finishing_machine_hours": "0.00",
                "uom": movement_execution["production_order_uom"],
                "status": movement_execution["production_order_status"],
            }
        )
