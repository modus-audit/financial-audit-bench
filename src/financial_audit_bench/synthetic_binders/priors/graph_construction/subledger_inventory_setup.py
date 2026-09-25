"""Inventory input admission, policy snapshots, location, and item construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    WAREHOUSE_ADDRESSES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
    is_manufacturing,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_admission import (
    SUBLEDGER_SYNTHETIC_POLICIES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_planning import (
    _inventory_plan,
)

INVENTORY_KEYS = (
    "inventory_items",
    "inventory_locations",
    "inventory_lot_balances",
    "inventory_movements",
    "inventory_counts",
    "inventory_count_lines",
    "inventory_rollforwards",
    "inventory_gl_reconciliations",
    "steel_production_orders",
)


@dataclass(slots=True)
class _InventoryBuildContext:
    records: dict[str, list[dict[str, Any]]]
    company: dict[str, str]
    calendar: dict[str, Any]
    year_end: date
    annual_cogs: Decimal
    opening_cost: Decimal
    ending_target: Decimal
    business_type: str
    inventory_policy: Any
    inventory_execution_policy: Any
    movement_row_policy: Any
    source_sequence_low: int
    source_sequence_high: int
    steel_policy: Any
    plan: dict[str, Any]
    manufacturing: bool
    draws: Any
    draw_labels: Any
    location: dict[str, Any]
    supplier_by_item: dict[str, str]
    items: list[dict[str, Any]]
    per_item: dict[str, dict[str, Any]] = field(default_factory=dict)


def _prepare_inventory_build(
    records: dict[str, list[dict[str, Any]]],
    company: dict[str, str],
    calendar: dict[str, Any],
    year_end: date,
    annual_cogs: Decimal,
    opening_cost: Decimal,
    ending_target: Decimal,
    business_type: str,
    vendor_master: list[dict[str, Any]],
) -> _InventoryBuildContext:
    """Admit trusted inputs and construct the location and item master."""
    inventory_policy = SUBLEDGER_SYNTHETIC_POLICIES["policy.inventory_operations"]
    inventory_execution_policy = SUBLEDGER_SYNTHETIC_POLICIES[
        "policy.inventory_execution"
    ]
    movement_row_policy = inventory_execution_policy["movement_row_contract"]
    source_sequence_low, source_sequence_high = (
        int(value)
        for value in movement_row_policy["ordinary_source_sequence_range_inclusive"]
    )
    steel_policy = SUBLEDGER_SYNTHETIC_POLICIES["policy.steel_production"]
    plan = _inventory_plan(
        company,
        calendar,
        annual_cogs,
        opening_cost,
        business_type=business_type,
        inventory_policy=inventory_policy,
        warehouse_addresses=tuple(WAREHOUSE_ADDRESSES),
    )
    manufacturing = is_manufacturing(business_type)
    master_supplier_names = tuple(
        sorted(
            {
                str(row["name"])
                for row in vendor_master
                if row.get("name") and row.get("vendor_id")
            }
        )
    )
    if not master_supplier_names:
        raise ValueError("inventory has no admitted vendor-master source")
    draws = plan["draws"]
    draw_labels = inventory_policy["deterministic_generation"]["draw_labels"]
    location_defaults = inventory_policy["generic_location_defaults"]
    location = {
        "address": plan["address"],
        "company_id": company["company_id"],
        "count_required": location_defaults["count_required"],
        "custodian_name": company["legal_name"],
        "inventory_location_id": location_defaults["inventory_location_id"],
        "location_name": plan["location_name"],
        "location_type": plan["location_type"],
        "ownership_status": location_defaults["ownership_status"],
    }
    records["inventory_locations"].append(location)
    supplier_by_item: dict[str, str] = {}

    items = []
    item_identity_policy = inventory_policy["item_identity"]
    for index, description in enumerate(plan["descriptions"], 1):
        # Grammar worlds: category, UOM, and costing method come from the fine type's
        # declared taxonomy — never a random draw (a retailer has no raw materials to
        # roll).
        category = plan["categories"][index - 1]
        item = {
            "category": category,
            "company_id": company["company_id"],
            "costing_method": plan["costing_method"],
            "costing_approval_status": item_identity_policy["costing_approval_status"],
            "description": description,
            "inventory_item_id": f"ITEM-{index:03d}",
            # Inventory SKUs do not inherit service coverage dates. Obsolescence is
            # modeled by the reserve assessment, while the item master remains active
            # for the generated count/listing.
            "item_status": item_identity_policy["item_status"],
            "reserve_group": item_identity_policy["reserve_group"],
            "sku": (
                item_identity_policy["sku_prefix_by_category"].get(
                    category,
                    item_identity_policy["sku_prefix_by_category"]["fallback"],
                )
                + f"-{plan['sku_numbers'][index - 1]}"
            ),
            "standard_cost": str(plan["units"][index - 1]),
            "uom": plan["uoms"][index - 1],
        }
        items.append(item)
        records["inventory_items"].append(item)
        supplier_by_item[item["inventory_item_id"]] = master_supplier_names[
            (index - 1) % len(master_supplier_names)
        ]

    return _InventoryBuildContext(
        records=records,
        company=company,
        calendar=calendar,
        year_end=year_end,
        annual_cogs=annual_cogs,
        opening_cost=opening_cost,
        ending_target=ending_target,
        business_type=business_type,
        inventory_policy=inventory_policy,
        inventory_execution_policy=inventory_execution_policy,
        movement_row_policy=movement_row_policy,
        source_sequence_low=source_sequence_low,
        source_sequence_high=source_sequence_high,
        steel_policy=steel_policy,
        plan=plan,
        manufacturing=manufacturing,
        draws=draws,
        draw_labels=draw_labels,
        location=location,
        supplier_by_item=supplier_by_item,
        items=items,
    )
