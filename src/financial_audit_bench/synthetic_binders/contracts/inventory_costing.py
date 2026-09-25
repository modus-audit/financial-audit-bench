"""Inventory listing coverage and control totals."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping


def _rows(world: Mapping[str, Any], node: str) -> list[Mapping[str, Any]]:
    return world.get(node) or []


def validate_inventory_costing_sources(world: Mapping[str, Any]) -> None:
    """Require every inventory item to have a year-end lot or layer."""
    item_ids = {str(row["inventory_item_id"]) for row in _rows(world, "inventory_item")}
    lot_item_ids = {
        str(row["inventory_item_id"]) for row in _rows(world, "inventory_lot_balance")
    }
    if item_ids != lot_item_ids:
        raise ValueError(
            "inventory listing coverage is incomplete: "
            f"items={sorted(item_ids - lot_item_ids)}, "
            f"unknown_lots={sorted(lot_item_ids - item_ids)}"
        )


def inventory_source_controls(world: Mapping[str, Any]) -> dict[str, Any]:
    """Measure the complete year-end listing from its source records."""
    items = {
        str(row["inventory_item_id"]): row for row in _rows(world, "inventory_item")
    }
    lots = _rows(world, "inventory_lot_balance")
    keys = [str(row.get("inventory_lot_balance_id") or "") for row in lots]
    return {
        "record_count": len(lots),
        "unique_key_count": len(set(keys)),
        "categories": sorted(
            {str(items[str(row["inventory_item_id"])]["category"]) for row in lots}
        ),
        "quantity_control_total": format(
            sum(
                (Decimal(str(row["quantity_on_hand"])) for row in lots),
                Decimal("0"),
            ),
            "f",
        ),
        "cost_control_total": format(
            sum(
                (Decimal(str(row["extended_cost"])) for row in lots),
                Decimal("0"),
            ),
            "f",
        ),
    }
