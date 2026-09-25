"""FIFO layer costing for the steel production lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _allocate_amount,
    _money,
)


def _apply_steel_fifo_costing(
    records: dict[str, list[dict[str, Any]]],
    items: list[dict[str, Any]],
    per_item: dict[str, dict[str, Any]],
    steel_policy: Mapping[str, Any],
) -> None:
    """Cost the steel mill's physical flow from dated FIFO layers."""
    items_by_id = {row["inventory_item_id"]: row for row in items}
    movements = records["inventory_movements"]
    orders = {
        row["production_order_number"]: row
        for row in records["steel_production_orders"]
    }
    layers: dict[str, list[dict[str, Any]]] = {}
    layer_sequence = 0

    def new_layer(
        item_id: str,
        when: str,
        quantity: Decimal,
        material: Decimal,
        labor: Decimal,
        overhead: Decimal,
        source: str,
    ) -> None:
        nonlocal layer_sequence
        if quantity <= 0:
            return
        layer_sequence += 1
        layers.setdefault(item_id, []).append(
            {
                "layer_id": f"Layer {layer_sequence:04d}",
                "date": when,
                "quantity": quantity,
                "material": material,
                "labor": labor,
                "overhead": overhead,
                "source": source,
            }
        )

    def opening_components(
        category: str, total: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        if category == "raw_material":
            return total, Decimal("0.00"), Decimal("0.00")
        material_per_ton = Decimal(str(steel_policy["raw_material_cost_per_ton"]))
        wip_hours = Decimal(str(steel_policy["wip_hours_per_ton"]))
        finishing_hours = Decimal(str(steel_policy["finishing_hours_per_ton"]))
        labor_rate = Decimal(str(steel_policy["labor_rate"]))
        overhead_rate = Decimal(str(steel_policy["overhead_rate"]))
        wip_labor = wip_hours * labor_rate
        wip_overhead = wip_hours * overhead_rate
        finishing_labor = finishing_hours * labor_rate
        finishing_overhead = finishing_hours * overhead_rate
        if category == "work_in_process":
            component_total = material_per_ton + wip_labor + wip_overhead
            labor_per_ton = wip_labor
        else:
            component_total = (
                material_per_ton
                + wip_labor
                + wip_overhead
                + finishing_labor
                + finishing_overhead
            )
            labor_per_ton = wip_labor + finishing_labor
        material_share = material_per_ton / component_total
        labor_share = labor_per_ton / component_total
        material = _money(total * material_share)
        labor = _money(total * labor_share)
        return material, labor, total - material - labor

    for item in items:
        item_id = item["inventory_item_id"]
        state = per_item[item_id]
        material, labor, overhead = opening_components(
            item["category"], state["opening_cost"]
        )
        new_layer(
            item_id,
            f"{int(next(iter(orders.values()))['production_month'][:4]) - 1}-12-31",
            Decimal(state["opening_qty"]),
            material,
            labor,
            overhead,
            f"AUDITED-OPENING-{item_id}",
        )

    def consume(
        item_id: str, quantity: Decimal
    ) -> tuple[Decimal, Decimal, Decimal, str]:
        remaining = quantity
        material = labor = overhead = Decimal("0.00")
        consumed: list[str] = []
        queue = layers[item_id]
        while remaining > 0:
            if not queue:
                raise ValueError(f"steel FIFO layer shortage for {item_id}")
            layer = queue[0]
            take = min(remaining, layer["quantity"])
            ratio = take / layer["quantity"]
            if take == layer["quantity"]:
                take_material = layer["material"]
                take_labor = layer["labor"]
                take_overhead = layer["overhead"]
            else:
                take_material = _money(layer["material"] * ratio)
                take_labor = _money(layer["labor"] * ratio)
                take_overhead = _money(layer["overhead"] * ratio)
            layer["quantity"] -= take
            layer["material"] -= take_material
            layer["labor"] -= take_labor
            layer["overhead"] -= take_overhead
            material += take_material
            labor += take_labor
            overhead += take_overhead
            consumed.append(f"{layer['layer_id']} [{layer['source']}; {take} tons]")
            remaining -= take
            if layer["quantity"] == 0:
                queue.pop(0)
        return material, labor, overhead, ", ".join(consumed)

    def set_movement_cost(
        row: dict[str, Any],
        material: Decimal,
        labor: Decimal,
        overhead: Decimal,
        consumed: str = "",
    ) -> None:
        total = material + labor + overhead
        quantity = Decimal(row["quantity"])
        row["material_cost"] = str(material)
        row["direct_labor_cost"] = str(labor)
        row["production_overhead_cost"] = str(overhead)
        row["extended_cost"] = str(total)
        row["unit_cost"] = str((total / quantity).quantize(Decimal("0.0001")))
        row["fifo_layers_consumed"] = consumed or None

    # Dated supplier receipts establish the raw-material FIFO layers.
    raw_receipts = sorted(
        (
            row
            for row in movements
            if row["movement_kind"] == "purchase_receipt"
            and items_by_id[row["inventory_item_id"]]["category"] == "raw_material"
        ),
        key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
    )
    for row in raw_receipts:
        item = items_by_id[row["inventory_item_id"]]
        month = int(row["posting_date"][5:7])
        item_offset = int(row["inventory_item_id"][-3:])
        month_factor = Decimal(str(steel_policy["raw_price_month_factors"][month - 1]))
        offsets = steel_policy["raw_price_sku_offsets"]
        market_factor = month_factor + Decimal(str(offsets[item_offset % len(offsets)]))
        unit_cost = (Decimal(item["standard_cost"]) * market_factor).quantize(
            Decimal("0.0001")
        )
        material = _money(Decimal(row["quantity"]) * unit_cost)
        set_movement_cost(row, material, Decimal("0.00"), Decimal("0.00"))
        new_layer(
            row["inventory_item_id"],
            row["posting_date"],
            Decimal(row["quantity"]),
            material,
            Decimal("0.00"),
            Decimal("0.00"),
            row["source_document"],
        )
        row["source_cost_layer_id"] = layers[row["inventory_item_id"]][-1]["layer_id"]

    def stage_rows(category: str, kind: str) -> list[dict[str, Any]]:
        return sorted(
            (
                row
                for row in movements
                if row["movement_kind"] == kind
                and items_by_id[row["inventory_item_id"]]["category"] == category
            ),
            key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
        )

    raw_by_order: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for row in stage_rows("raw_material", "production_issue"):
        material, labor, overhead, consumed = consume(
            row["inventory_item_id"], Decimal(row["quantity"])
        )
        set_movement_cost(row, material, labor, overhead, consumed)
        order_id = row["production_order_number"]
        prior = raw_by_order.get(order_id, (Decimal("0"),) * 3)
        raw_by_order[order_id] = tuple(
            prior[index] + value
            for index, value in enumerate((material, labor, overhead))
        )

    def allocate_completion(
        category: str,
        inherited_by_order: dict[str, tuple[Decimal, Decimal, Decimal]],
        hours_per_ton: Decimal,
        labor_rate: Decimal,
        overhead_rate: Decimal,
        order_prefix: str,
    ) -> None:
        rows_by_order: dict[str, list[dict[str, Any]]] = {}
        for row in stage_rows(category, "production_completion"):
            rows_by_order.setdefault(row["production_order_number"], []).append(row)
        for order_id, rows in rows_by_order.items():
            inherited = inherited_by_order.get(order_id, (Decimal("0"),) * 3)
            quantity = sum((Decimal(row["quantity"]) for row in rows), Decimal("0"))
            machine_hours = (quantity * hours_per_ton).quantize(Decimal("0.01"))
            conversion_labor = _money(machine_hours * labor_rate)
            conversion_overhead = _money(machine_hours * overhead_rate)
            material_total = inherited[0]
            labor_total = inherited[1] + conversion_labor
            overhead_total = inherited[2] + conversion_overhead
            weights = [Decimal(row["quantity"]) for row in rows]
            material_parts = _allocate_amount(material_total, weights)
            labor_parts = _allocate_amount(labor_total, weights)
            overhead_parts = _allocate_amount(overhead_total, weights)
            added_labor_parts = _allocate_amount(conversion_labor, weights)
            added_overhead_parts = _allocate_amount(conversion_overhead, weights)
            for row, material, labor, overhead, added_labor, added_overhead in zip(
                rows,
                material_parts,
                labor_parts,
                overhead_parts,
                added_labor_parts,
                added_overhead_parts,
                strict=True,
            ):
                set_movement_cost(row, material, labor, overhead)
                row["conversion_labor_added"] = str(added_labor)
                row["conversion_overhead_added"] = str(added_overhead)
                new_layer(
                    row["inventory_item_id"],
                    row["posting_date"],
                    Decimal(row["quantity"]),
                    material,
                    labor,
                    overhead,
                    row["source_document"],
                )
                row["source_cost_layer_id"] = layers[row["inventory_item_id"]][-1][
                    "layer_id"
                ]
            order = orders[order_id]
            if order_prefix == "wip":
                order.update(
                    {
                        "raw_material_cost": str(sum(inherited, Decimal("0"))),
                        "wip_conversion_labor_cost": str(conversion_labor),
                        "wip_conversion_overhead_cost": str(conversion_overhead),
                        "wip_completed_cost": str(
                            material_total + labor_total + overhead_total
                        ),
                        "raw_stage_machine_hours": str(machine_hours),
                        "direct_labor_rate": str(labor_rate),
                        "overhead_rate": str(overhead_rate),
                    }
                )
            else:
                order.update(
                    {
                        "wip_issued_cost": str(sum(inherited, Decimal("0"))),
                        "finishing_labor_cost": str(conversion_labor),
                        "finishing_overhead_cost": str(conversion_overhead),
                        "finished_goods_completed_cost": str(
                            material_total + labor_total + overhead_total
                        ),
                        "finishing_machine_hours": str(machine_hours),
                    }
                )

    allocate_completion(
        "work_in_process",
        raw_by_order,
        Decimal(str(steel_policy["wip_hours_per_ton"])),
        Decimal(str(steel_policy["labor_rate"])),
        Decimal(str(steel_policy["overhead_rate"])),
        "wip",
    )
    wip_by_order: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for row in stage_rows("work_in_process", "production_issue"):
        material, labor, overhead, consumed = consume(
            row["inventory_item_id"], Decimal(row["quantity"])
        )
        set_movement_cost(row, material, labor, overhead, consumed)
        order_id = row["production_order_number"]
        prior = wip_by_order.get(order_id, (Decimal("0"),) * 3)
        wip_by_order[order_id] = tuple(
            prior[index] + value
            for index, value in enumerate((material, labor, overhead))
        )
    allocate_completion(
        "finished_goods",
        wip_by_order,
        Decimal(str(steel_policy["finishing_hours_per_ton"])),
        Decimal(str(steel_policy["labor_rate"])),
        Decimal(str(steel_policy["overhead_rate"])),
        "finished",
    )
    for row in stage_rows("finished_goods", "sale_shipment"):
        material, labor, overhead, consumed = consume(
            row["inventory_item_id"], Decimal(row["quantity"])
        )
        set_movement_cost(row, material, labor, overhead, consumed)

    for item in items:
        item_id = item["inventory_item_id"]
        item_movements = [
            row for row in movements if row["inventory_item_id"] == item_id
        ]
        state = per_item[item_id]
        state["purchase_cost"] = sum(
            (
                Decimal(row["extended_cost"])
                for row in item_movements
                if row["movement_kind"] == "purchase_receipt"
            ),
            Decimal("0.00"),
        )
        state["production_cost"] = sum(
            (
                Decimal(row["extended_cost"])
                for row in item_movements
                if row["movement_kind"] == "production_completion"
            ),
            Decimal("0.00"),
        )
        state["sales_cost"] = sum(
            (
                Decimal(row["extended_cost"])
                for row in item_movements
                if row["direction"] == "out"
            ),
            Decimal("0.00"),
        )
        item_layers = layers.get(item_id, [])
        state["ending_layers"] = item_layers
        ending_quantity = sum(
            (layer["quantity"] for layer in item_layers), Decimal("0")
        )
        ending_cost = sum(
            (
                layer["material"] + layer["labor"] + layer["overhead"]
                for layer in item_layers
            ),
            Decimal("0.00"),
        )
        if ending_quantity:
            state["unit"] = (ending_cost / ending_quantity).quantize(Decimal("0.0001"))
            item["standard_cost"] = str(state["unit"])
