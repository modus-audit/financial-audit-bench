"""Inventory quantity, cost, count, lot, and production-order validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


def validate_inventory(records: dict[str, list[dict[str, Any]]]) -> None:
    """Re-derive quantity/cost rollforward, count, and GL ties per item."""
    movements_by_item: dict[str, list[dict[str, Any]]] = {}
    for movement in records["inventory_movements"]:
        movements_by_item.setdefault(movement["inventory_item_id"], []).append(movement)
    lines_by_item = {
        row["inventory_item_id"]: row for row in records["inventory_count_lines"]
    }
    counts_by_id = {
        row["inventory_count_id"]: row for row in records["inventory_counts"]
    }
    ending_total = Decimal("0")
    for rollforward in records["inventory_rollforwards"]:
        item_id = rollforward["inventory_item_id"]
        moved = movements_by_item.get(item_id, [])
        line = lines_by_item[item_id]
        count = counts_by_id[line["inventory_count_id"]]
        count_date = date.fromisoformat(str(count["count_date"]))
        running_quantity = Decimal(rollforward["opening_quantity"])
        count_date_book_quantity = Decimal(rollforward["opening_quantity"])
        for movement in sorted(
            moved,
            # The production policy permits a completion and its next-stage issue on the
            # same accounting date. At that daily granularity, the completed inbound
            # layer exists before the outbound issue; movement IDs are document
            # identities, not timestamps.
            key=lambda row: (
                row["posting_date"],
                0 if row["direction"] == "in" else 1,
                row["inventory_movement_id"],
            ),
        ):
            quantity = Decimal(movement["quantity"])
            signed_quantity = quantity if movement["direction"] == "in" else -quantity
            running_quantity += signed_quantity
            if date.fromisoformat(str(movement["posting_date"])) <= count_date:
                count_date_book_quantity += signed_quantity
            if running_quantity < 0:
                raise ValueError(
                    f"inventory quantity goes negative for {item_id} at "
                    f"{movement['inventory_movement_id']} "
                    f"({movement['posting_date']} {movement['movement_kind']} "
                    f"quantity={quantity}; running={running_quantity})"
                )
        purchases = sum(
            (
                Decimal(m["extended_cost"])
                for m in moved
                if m["movement_kind"] == "purchase_receipt"
            ),
            Decimal("0"),
        )
        production = sum(
            (
                Decimal(m["extended_cost"])
                for m in moved
                if m["movement_kind"] == "production_completion"
            ),
            Decimal("0"),
        )
        sales = sum(
            (Decimal(m["extended_cost"]) for m in moved if m["direction"] == "out"),
            Decimal("0"),
        )
        if purchases != Decimal(rollforward["purchase_cost"]):
            raise ValueError("inventory purchases do not tie to movements")
        if production != Decimal(rollforward["production_cost"]):
            raise ValueError("inventory production does not tie to movements")
        if sales != Decimal(rollforward["sales_cost"]):
            raise ValueError("inventory cost of sales does not tie to movements")
        ending = (
            Decimal(rollforward["opening_cost"])
            + purchases
            + production
            - sales
            + Decimal(rollforward["count_adjustment_cost"])
        )
        if ending != Decimal(rollforward["ending_gross_cost"]):
            raise ValueError(
                f"inventory cost rollforward does not tie for {item_id}: "
                f"computed {ending}, recorded {rollforward['ending_gross_cost']}"
            )
        if count_date_book_quantity != Decimal(line["book_quantity"]):
            raise ValueError(
                f"inventory count-date book quantity does not tie for {item_id}"
            )
        if Decimal(line["counted_quantity"]) - Decimal(
            line["book_quantity"]
        ) != Decimal(line["variance_quantity"]):
            raise ValueError("inventory count variance does not tie")
        expected_ending_quantity = running_quantity + Decimal(line["variance_quantity"])
        if expected_ending_quantity != Decimal(rollforward["ending_quantity"]):
            raise ValueError(
                f"inventory count-to-year-end quantity bridge does not tie for {item_id}"
            )
        if Decimal(line["variance_amount"]) != Decimal(
            rollforward["count_adjustment_cost"]
        ):
            raise ValueError("inventory count variance amount does not tie")
        if Decimal(line["variance_quantity"]) * Decimal(line["variance_amount"]) < 0:
            raise ValueError("inventory count variance quantity and value disagree")
        ending_total += ending
    reconciliation = records["inventory_gl_reconciliations"][0]
    if Decimal(reconciliation["subledger_gross_cost"]) != ending_total:
        raise ValueError("inventory reconciliation does not tie to the rollforwards")
    if Decimal(reconciliation["difference"]):
        raise ValueError("inventory subledger does not tie to generated GL controls")

    lots_by_item: dict[str, list[dict[str, Any]]] = {}
    for lot in records["inventory_lot_balances"]:
        lots_by_item.setdefault(lot["inventory_item_id"], []).append(lot)
        if any(
            lot.get(field) is not None
            for field in (
                "material_cost",
                "direct_labor_cost",
                "production_overhead_cost",
            )
        ):
            component_total = sum(
                (
                    Decimal(str(lot.get(field) or "0"))
                    for field in (
                        "material_cost",
                        "direct_labor_cost",
                        "production_overhead_cost",
                    )
                ),
                Decimal("0"),
            )
            if component_total != Decimal(lot["extended_cost"]):
                raise ValueError("inventory FIFO lot components do not tie")
    for rollforward in records["inventory_rollforwards"]:
        item_lots = lots_by_item.get(rollforward["inventory_item_id"], [])
        if sum(
            (Decimal(row["extended_cost"]) for row in item_lots), Decimal("0")
        ) != Decimal(rollforward["ending_gross_cost"]):
            raise ValueError("inventory lots do not tie to item rollforward")
        if sum(
            (Decimal(row["quantity_on_hand"]) for row in item_lots), Decimal("0")
        ) != Decimal(rollforward["ending_quantity"]):
            raise ValueError("inventory lot quantities do not tie to item rollforward")

    orders = records.get("steel_production_orders") or []
    if orders:
        if len(orders) != 12:
            raise ValueError(
                "steel production calendar must contain twelve monthly orders"
            )
        order_ids = {row["production_order_number"] for row in orders}
        for order in orders:
            dates = [
                date.fromisoformat(order[field])
                for field in (
                    "raw_material_issue_date",
                    "wip_completion_date",
                    "wip_issue_date",
                    "finished_goods_completion_date",
                    "shipment_date",
                )
            ]
            if dates != sorted(dates):
                raise ValueError(
                    "steel production order stage dates are out of sequence"
                )
            raw = Decimal(order["raw_material_issued_quantity"])
            wip_completed = Decimal(order["wip_completed_quantity"])
            wip_issued = Decimal(order["wip_issued_quantity"])
            finished = Decimal(order["finished_goods_completed_quantity"])
            if not (Decimal("0") <= wip_completed <= raw):
                raise ValueError("steel raw-to-WIP production yield is impossible")
            if not (Decimal("0") <= finished <= wip_issued):
                raise ValueError("steel WIP-to-finished production yield is impossible")
            raw_cost = Decimal(order["raw_material_cost"])
            wip_conversion = Decimal(order["wip_conversion_labor_cost"]) + Decimal(
                order["wip_conversion_overhead_cost"]
            )
            if raw_cost + wip_conversion != Decimal(order["wip_completed_cost"]):
                raise ValueError("steel WIP production cost build does not tie")
            finishing_conversion = Decimal(order["finishing_labor_cost"]) + Decimal(
                order["finishing_overhead_cost"]
            )
            if Decimal(order["wip_issued_cost"]) + finishing_conversion != Decimal(
                order["finished_goods_completed_cost"]
            ):
                raise ValueError(
                    "steel finished-goods production cost build does not tie"
                )
        linked = [
            row
            for row in records["inventory_movements"]
            if row.get("production_order_number")
        ]
        if any(row["production_order_number"] not in order_ids for row in linked):
            raise ValueError("steel movement references an unknown production order")
        items = {row["inventory_item_id"]: row for row in records["inventory_items"]}

        def movement_total(category: str, kind: str) -> Decimal:
            return sum(
                (
                    Decimal(row["quantity"])
                    for row in linked
                    if row["movement_kind"] == kind
                    and items[row["inventory_item_id"]]["category"] == category
                ),
                Decimal("0"),
            )

        comparisons = (
            ("raw_material_issued_quantity", "raw_material", "production_issue"),
            ("wip_completed_quantity", "work_in_process", "production_completion"),
            ("wip_issued_quantity", "work_in_process", "production_issue"),
            (
                "finished_goods_completed_quantity",
                "finished_goods",
                "production_completion",
            ),
            ("finished_goods_shipped_quantity", "finished_goods", "sale_shipment"),
        )
        for field, category, kind in comparisons:
            order_total = sum((Decimal(row[field]) for row in orders), Decimal("0"))
            if order_total != movement_total(category, kind):
                raise ValueError(f"steel production orders do not tie for {field}")
