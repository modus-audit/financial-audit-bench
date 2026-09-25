"""Inventory count, NRV, lot, rollforward, and reconciliation assembly."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_setup import (
    _InventoryBuildContext,
)


def _build_inventory_evidence(context: _InventoryBuildContext) -> None:
    """Attach procurement identity and construct count-to-GL evidence."""
    records = context.records
    calendar = context.calendar
    company = context.company
    year_end = context.year_end
    inventory_execution_policy = context.inventory_execution_policy
    inventory_policy = context.inventory_policy
    items = context.items
    location = context.location
    money = _money
    per_item = context.per_item
    plan = context.plan
    draws = context.draws
    draw_labels = context.draw_labels
    supplier_by_item = context.supplier_by_item

    # The independent execution snapshot fixes the observation at fiscal end;
    # no sampled observation date or fixed freeze-time constant is consumed.
    count_header_policy = inventory_execution_policy["physical_count_header"]
    count_date = year_end + timedelta(
        days=int(count_header_policy["date_offset_days_from_fiscal_end"])
    )
    period_start = date.fromisoformat(str(calendar["start_date"]))
    if not period_start <= count_date <= year_end:
        raise ValueError(
            "inventory observation date must fall inside the fiscal period"
        )
    count = {
        "count_date": count_date.isoformat(),
        "count_method": count_header_policy["method"],
        "count_status": count_header_policy["status"],
        "count_team": count_header_policy["count_team"],
        "final_results_reference": str(
            count_header_policy["final_results_reference_format"]
        ).format(fiscal_end_year=year_end.year),
        "instructions_reference": str(
            count_header_policy["instructions_reference_format"]
        ).format(fiscal_end_year=year_end.year),
        "inventory_count_id": count_header_policy["inventory_count_id"],
        "inventory_freeze_timestamp": (
            f"{count_date.isoformat()}T{count_header_policy['freeze_time']}"
        ),
        "inventory_location_id": location["inventory_location_id"],
        "supervisor": count_header_policy["supervisor"],
    }
    records["inventory_counts"].append(count)
    count_line_policy = inventory_execution_policy["count_line_rules"]
    variance_count = min(
        len(items),
        int(
            (
                Decimal(len(items))
                * Decimal(str(inventory_policy["count_selection_share"]))
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ),
    )
    variance_items = {
        item["inventory_item_id"]
        for item in sorted(
            items,
            key=lambda row: row["inventory_item_id"],
        )[:variance_count]
    }
    for index, item in enumerate(items, 1):
        state = per_item[item["inventory_item_id"]]
        item_movements = [
            row
            for row in records["inventory_movements"]
            if row["inventory_item_id"] == item["inventory_item_id"]
        ]
        cost_layer_date = year_end - timedelta(
            days=draws.randint(
                str(draw_labels["cost_layer_age"]).format(
                    item_id=item["inventory_item_id"]
                ),
                *(int(value) for value in inventory_policy["active_age_days"]),
            )
        )
        year_end_book_qty = (
            state["opening_qty"]
            + state["purchase_qty"]
            + state.get("production_qty", 0)
            - state["sales_qty"]
        )
        book_qty = Decimal(state["opening_qty"])
        post_count_quantity = Decimal("0")
        for movement in item_movements:
            movement_quantity = Decimal(str(movement["quantity"]))
            signed_quantity = (
                movement_quantity
                if movement["direction"] == "in"
                else -movement_quantity
            )
            if date.fromisoformat(str(movement["posting_date"])) <= count_date:
                book_qty += signed_quantity
            else:
                post_count_quantity += signed_quantity
        if book_qty + post_count_quantity != Decimal(year_end_book_qty):
            raise ValueError(
                f"inventory count bridge does not tie for {item['inventory_item_id']}"
            )
        shortage = int(
            (
                book_qty * Decimal(str(inventory_policy["count_shortage_share"]))
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        variance = (
            -min(shortage, int(book_qty))
            if item["inventory_item_id"] in variance_items and shortage > 0
            else 0
        )
        counted = book_qty + Decimal(variance)
        auditor_observed = book_qty + Decimal(variance)
        ending_quantity = counted + post_count_quantity
        unit = state["unit"]
        count_unit = unit
        state["variance_qty"] = variance
        steel_layers = state.get("ending_layers")
        if steel_layers is not None and variance < 0:
            shortage = Decimal(-variance)
            removed_cost = Decimal("0.00")
            while shortage > 0:
                layer = steel_layers[-1]
                take = min(shortage, layer["quantity"])
                ratio = take / layer["quantity"]
                component_costs = []
                for component in ("material", "labor", "overhead"):
                    component_cost = (
                        layer[component]
                        if take == layer["quantity"]
                        else money(layer[component] * ratio)
                    )
                    layer[component] -= component_cost
                    component_costs.append(component_cost)
                layer["quantity"] -= take
                removed_cost += sum(component_costs, Decimal("0.00"))
                shortage -= take
                if layer["quantity"] == 0:
                    steel_layers.pop()
            state["variance_cost"] = -removed_cost
            unit_places = int(count_line_policy["steel_variance_unit_decimal_places"])
            count_unit = (removed_cost / Decimal(-variance)).quantize(
                Decimal("1").scaleb(-unit_places),
                rounding=ROUND_HALF_EVEN,
            )
            if money(Decimal(variance) * count_unit) != state["variance_cost"]:
                raise ValueError(
                    "steel count unit cost does not extend to FIFO variance relief"
                )
        else:
            state["variance_cost"] = money(variance * unit)
        state["ending_qty"] = ending_quantity
        state["ending_cost"] = (
            state["opening_cost"]
            + state["purchase_cost"]
            + state.get("production_cost", Decimal("0"))
            - state["sales_cost"]
            + state["variance_cost"]
        )
        count_lot_id = str(count_line_policy["generic_lot_balance_id_format"]).format(
            item_ordinal_3=f"{index:03d}"
        )
        if steel_layers:
            lot_total = Decimal("0.00")
            for layer_index, layer in enumerate(steel_layers, 1):
                layer_cost = layer["material"] + layer["labor"] + layer["overhead"]
                layer_unit = (layer_cost / layer["quantity"]).quantize(
                    Decimal("0.0001")
                )
                lot_id = str(count_line_policy["steel_lot_balance_id_format"]).format(
                    item_ordinal_3=f"{index:03d}",
                    layer_ordinal_2=f"{layer_index:02d}",
                )
                if layer_index == 1:
                    count_lot_id = lot_id
                lot_total += layer_cost
                records["inventory_lot_balances"].append(
                    {
                        "as_of_date": year_end.isoformat(),
                        "condition": item["category"].replace("_", " "),
                        "cost_layer_date": layer["date"],
                        "extended_cost": str(layer_cost),
                        "material_cost": str(layer["material"]),
                        "direct_labor_cost": str(layer["labor"]),
                        "production_overhead_cost": str(layer["overhead"]),
                        "in_transit_status": "received",
                        "inventory_item_id": item["inventory_item_id"],
                        "inventory_location_id": location["inventory_location_id"],
                        "inventory_lot_balance_id": lot_id,
                        "lot_number": layer["layer_id"],
                        "owner_entity_id": company["legal_name"],
                        "ownership_status": "owned",
                        "quantity_on_hand": str(layer["quantity"]),
                        "unit_cost": str(layer_unit),
                        "vendor_reference": layer["source"],
                    }
                )
            if lot_total != state["ending_cost"]:
                raise ValueError("steel FIFO layers do not tie to ending inventory")
        else:
            records["inventory_lot_balances"].append(
                {
                    "as_of_date": year_end.isoformat(),
                    "condition": item["category"].replace("_", " "),
                    "cost_layer_date": cost_layer_date.isoformat(),
                    "extended_cost": str(state["ending_cost"]),
                    "material_cost": None,
                    "direct_labor_cost": None,
                    "production_overhead_cost": None,
                    "in_transit_status": "received",
                    "inventory_item_id": item["inventory_item_id"],
                    "inventory_location_id": location["inventory_location_id"],
                    "inventory_lot_balance_id": count_lot_id,
                    "lot_number": f"LOT-{plan['lot_base'] + index * 3}",
                    "owner_entity_id": company["legal_name"],
                    "ownership_status": "owned",
                    "quantity_on_hand": str(ending_quantity),
                    "unit_cost": str(unit),
                    "vendor_reference": supplier_by_item[item["inventory_item_id"]],
                }
            )
        records["inventory_count_lines"].append(
            {
                "approval_status": count_line_policy["approval_status"],
                "approved_by": count["supervisor"],
                "auditor_observation_source": count_line_policy[
                    "auditor_observation_source"
                ],
                "auditor_observed_quantity": str(auditor_observed),
                "book_quantity": str(book_qty),
                "condition": item["category"].replace("_", " "),
                "counted_quantity": str(counted),
                "inventory_count_id": count["inventory_count_id"],
                "inventory_count_line_id": str(
                    count_line_policy["line_id_format"]
                ).format(item_ordinal_3=f"{index:03d}"),
                "inventory_item_id": item["inventory_item_id"],
                "inventory_lot_balance_id": count_lot_id,
                "recount_quantity": str(counted),
                "selection_source": count_line_policy["selection_source"],
                "tag_number": str(count_line_policy["tag_number_format"]).format(
                    item_ordinal_6=f"{index:06d}"
                ),
                "unit_cost": str(count_unit),
                "variance_amount": str(state["variance_cost"]),
                "variance_quantity": str(variance),
            }
        )
        if money(
            Decimal(records["inventory_count_lines"][-1]["variance_quantity"])
            * Decimal(records["inventory_count_lines"][-1]["unit_cost"])
        ) != Decimal(records["inventory_count_lines"][-1]["variance_amount"]):
            raise ValueError("inventory count-line variance extension does not tie")
        records["inventory_rollforwards"].append(
            {
                "count_adjustment_cost": str(state["variance_cost"]),
                "count_adjustment_quantity": str(variance),
                "ending_gross_cost": str(state["ending_cost"]),
                "ending_net_cost": str(state["ending_cost"]),
                "ending_quantity": str(ending_quantity),
                "fiscal_calendar_id": str(calendar["fiscal_calendar_id"]),
                "inventory_item_id": item["inventory_item_id"],
                "opening_cost": str(state["opening_cost"]),
                "opening_quantity": str(state["opening_qty"]),
                "production_cost": str(state.get("production_cost", Decimal("0"))),
                "production_quantity": str(state.get("production_qty", 0)),
                "purchase_cost": str(state["purchase_cost"]),
                "purchase_quantity": str(
                    0 if state.get("production_cost") else state["purchase_qty"]
                ),
                "reserve_amount": "0.00",
                "sales_cost": str(state["sales_cost"]),
                "sales_quantity": str(state["sales_qty"]),
                "writeoff_cost": "0.00",
                "writeoff_quantity": "0",
            }
        )

    ending_total = sum(
        (state["ending_cost"] for state in per_item.values()), Decimal("0")
    )
    records["inventory_gl_reconciliations"].append(
        {
            "difference": "0.00",
            "gl_gross_balance": str(ending_total),
            "gl_reserve_balance": "0.00",
            "inventory_gl_account_id": "GL-INVENTORY-001",
            "inventory_gl_reconciliation_id": "INV-RECON-001",
            "reconciliation_status": "tied",
            "review_status": "reviewed",
            "reserve_balance": "0.00",
            "subledger_gross_cost": str(ending_total),
            "subledger_net_cost": str(ending_total),
        }
    )
