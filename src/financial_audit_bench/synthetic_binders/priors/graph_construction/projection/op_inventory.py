"""Inventory count, costing, purchase, and production projection operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    OPS,
    World,
    op,
)


@op("render_count_movement_controls")
def render_count_movement_controls(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    return [
        {
            "inventory_freeze_timestamp": row["inventory_freeze_timestamp"],
            "cutoff_statuses": sorted(
                {move["cutoff_status"] for move in world["inventory_movement"]}
            ),
        }
        for row in world["inventory_count"]
    ]


def _inventory_item_column(world: World, population: str, field: str) -> list[Any]:
    items = {row["inventory_item_id"]: row for row in world["inventory_item"]}
    return [
        items[row["inventory_item_id"]][field] for row in world.get(population) or []
    ]


@op("join_count_location")
def join_count_location(world: World, node_id: str, field: str, value: Any) -> Any:
    return [row["location_name"] for row in world["inventory_location"]]


for _name, _population, _field in (
    ("join_lot_item_sku", "inventory_lot_balance", "sku"),
    ("join_lot_item_category", "inventory_lot_balance", "category"),
    ("join_lot_item_uom", "inventory_lot_balance", "uom"),
    ("join_lot_item_costing_method", "inventory_lot_balance", "costing_method"),
):
    OPS[_name] = (
        lambda world, _node, _source_field, _value, population=_population, field=_field: (
            _inventory_item_column(world, population, field)
        )
    )


def _inventory_costing_contract(
    world: World,
) -> dict[str, Any]:
    from financial_audit_bench.synthetic_binders.contracts.inventory_costing import (
        inventory_source_controls,
    )

    return inventory_source_controls(world)


for _name, _key in {
    "record_count": "record_count",
    "unique_keys": "unique_key_count",
    "quantity": "quantity_control_total",
    "cost": "cost_control_total",
}.items():
    OPS[f"measure_inventory_lot_{_name}"] = (
        lambda world, _node, _field, _value, key=_key: _inventory_costing_contract(
            world
        )[key]
    )

OPS["measure_inventory_lot_categories"] = lambda world, *_args: ", ".join(
    _inventory_costing_contract(world)["categories"]
)


@op("join_lot_location_name")
def join_lot_location_name(world: World, node_id: str, field: str, value: Any) -> Any:
    locations = {
        row["inventory_location_id"]: row["location_name"]
        for row in world["inventory_location"]
    }
    return [
        locations[row["inventory_location_id"]]
        for row in world["inventory_lot_balance"]
    ]


def _purchase_receipts(world: World) -> list[dict[str, Any]]:
    categories = {
        row["inventory_item_id"]: row["category"]
        for row in world.get("inventory_item") or []
    }
    return [
        row
        for row in world["inventory_movement"]
        if row["movement_kind"] == "purchase_receipt"
        # WIP is manufactured, never bought. Every other category follows its actual
        # transaction: a purchased finished good belongs in the invoice test even when
        # the entity also manufactures other SKUs.
        and categories.get(row["inventory_item_id"]) != "work_in_process"
    ]


@op("filter_purchase_receipts")
def filter_purchase_receipts(world: World, node_id: str, field: str, value: Any) -> Any:
    return [row[field] for row in _purchase_receipts(world)]


@op("join_purchase_item_sku")
def join_purchase_item_sku(world: World, node_id: str, field: str, value: Any) -> Any:
    skus = {row["inventory_item_id"]: row["sku"] for row in world["inventory_item"]}
    return [skus[row["inventory_item_id"]] for row in _purchase_receipts(world)]


def _production_buildup_rows(world: World) -> list[dict[str, Any]]:
    categories = {
        row["inventory_item_id"]: row["category"]
        for row in world.get("inventory_item") or []
    }
    return [
        row
        for row in world.get("inventory_movement") or []
        if row.get("movement_kind") == "production_completion"
        and categories.get(row.get("inventory_item_id"))
        in {"work_in_process", "finished_goods"}
    ]


@op("filter_production_buildup")
def filter_production_buildup(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    return [row[field] for row in _production_buildup_rows(world)]


@op("join_production_buildup_sku_job")
def join_production_buildup_sku_job(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    items = {row["inventory_item_id"]: row for row in world["inventory_item"]}
    return [
        f"{items[row['inventory_item_id']]['sku']} / {row['production_order_number']}"
        for row in _production_buildup_rows(world)
    ]


@op("join_production_buildup_category")
def join_production_buildup_category(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    items = {row["inventory_item_id"]: row for row in world["inventory_item"]}
    return [
        items[row["inventory_item_id"]]["category"]
        for row in _production_buildup_rows(world)
    ]


@op("render_production_buildup_stage")
def render_production_buildup_stage(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    items = {row["inventory_item_id"]: row for row in world["inventory_item"]}
    return [
        f"{items[row['inventory_item_id']]['category'].replace('_', ' ')} completion"
        for row in _production_buildup_rows(world)
    ]


@op("derive_production_buildup_variance")
def derive_production_buildup_variance(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    return [
        str(
            Decimal(row["extended_cost"])
            - Decimal(row["material_cost"])
            - Decimal(row["direct_labor_cost"])
            - Decimal(row["production_overhead_cost"])
        )
        for row in _production_buildup_rows(world)
    ]
