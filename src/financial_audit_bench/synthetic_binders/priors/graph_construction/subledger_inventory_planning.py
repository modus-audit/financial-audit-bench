"""Deterministic inventory scale, item, and opening-balance planning."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    WAREHOUSE_ADDRESSES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_admission import (
    SUBLEDGER_SYNTHETIC_POLICIES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _money,
    _synthetic_draws,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
    subledger_scale,
)


def _inventory_plan(
    company: dict[str, str],
    calendar: dict[str, Any],
    annual_cogs: Decimal,
    opening_cost: Decimal,
    business_type: str,
    *,
    inventory_policy: Mapping[str, Any],
    warehouse_addresses: tuple[str, ...],
) -> dict[str, Any]:
    """First-phase inventory draws (SKU list, unit costs, opening lots), shared verbatim by the builder and ``opening_inventory_cost`` so the prior-period TB opens at the exact allocated cost — integer lot quantities truncate the pro-rata split, so the plan is the truth."""
    generation_policy = inventory_policy["deterministic_generation"]
    draw_labels = generation_policy["draw_labels"]
    draws = _synthetic_draws(
        company,
        calendar,
        "inventory",
    )
    grammar = (
        load_authored_policy("authored.inventory.rules.v1").values["grammar"]
        if business_type == "manufacturing"
        else None
    )
    if business_type != "manufacturing" or not grammar:
        raise ValueError("inventory planning requires the manufacturing grammar")

    def authored_sku_count(unit_cost_proxy: Decimal, available: int) -> int:
        estimated_units = int(
            (annual_cogs / unit_cost_proxy).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        for upper_bound, count in inventory_policy["sku_count_tiers"]:
            if upper_bound is None or estimated_units < int(upper_bound):
                return min(int(count), available)
        raise ValueError("inventory SKU tier policy is incomplete")

    pool = [
        (category, description, uom)
        for category, uom, templates in grammar["categories"]
        for description in templates
    ]
    bands = grammar["unit_cost_bands"]
    grammar_midpoints = [
        (Decimal(str(edges[0])) + Decimal(str(edges[1]))) / 2
        for edges in bands.values()
    ]
    unit_cost_proxy = sum(grammar_midpoints, Decimal("0")) / len(grammar_midpoints)
    count_items = authored_sku_count(unit_cost_proxy, len(pool))
    drawn = [
        pool[index]
        for index in sorted(
            draws.sample(
                draw_labels["candidate_sample"],
                range(len(pool)),
                count_items,
            )
        )
    ]
    descriptions = [description for _, description, _ in drawn]
    uoms = [uom for _, _, uom in drawn]
    units = [
        _money(
            draws.uniform(
                str(draw_labels["grammar_unit_cost"]).format(
                    item_ordinal_3=f"{index:03d}"
                ),
                *bands[category],
            )
        )
        for index, (category, _, _) in enumerate(drawn, start=1)
    ]
    categories = [category for category, _, _ in drawn]
    if not warehouse_addresses:
        raise ValueError("inventory has no admitted warehouse-address source")
    address = draws.choice(draw_labels["warehouse"], warehouse_addresses)
    sku_low, sku_high = inventory_policy["item_identity"]["sku_seed_range_inclusive"]
    step_low, step_high = inventory_policy["item_identity"]["sku_step_range_inclusive"]
    sku_base = draws.randint(draw_labels["sku_seed"], int(sku_low), int(sku_high))
    sku_numbers = []
    cursor = sku_base
    for index in range(1, count_items + 1):
        cursor += draws.randint(
            str(draw_labels["sku_step"]).format(item_ordinal_3=f"{index:03d}"),
            int(step_low),
            int(step_high),
        )
        sku_numbers.append(cursor)
    # Equal weights avoid asserting a hidden item-concentration distribution.
    weights = [1.0 for _ in range(count_items)]
    weight_total = sum(weights)
    shares = [Decimal(str(weight / weight_total)) for weight in weights]
    opening_qtys = [
        int(_money(opening_cost * share) / unit) if unit else 0
        for share, unit in zip(shares, units)
    ]
    return {
        "draws": draws,
        "descriptions": descriptions,
        "categories": categories,
        "uoms": uoms,
        "costing_method": grammar["costing_method"],
        "location_name": grammar["location_name"],
        "location_type": grammar["location_type"],
        "outbound_kind": inventory_policy["outbound_kind"],
        "outbound_source_prefix": inventory_policy["outbound_source_prefix"],
        "address": address,
        "sku_base": sku_base,
        "sku_numbers": sku_numbers,
        "lot_base": draws.randint(
            draw_labels["lot_seed"],
            *(int(value) for value in generation_policy["lot_seed_range_inclusive"]),
        )
        * int(generation_policy["lot_seed_multiplier"]),
        "shares": shares,
        "units": units,
        "opening_qtys": opening_qtys,
    }


def opening_inventory_cost(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    operating_scale: dict[str, str],
) -> Decimal:
    """The exact opening inventory cost the engine will allocate — the
    prior-period balance vector opens the GL here so the TB and subledger
    agree to the cent."""
    if not feature_profile.get("has_inventory"):
        return Decimal("0.00")
    scale = subledger_scale(company, feature_profile, calendar, operating_scale)
    plan = _inventory_plan(
        company,
        calendar,
        _inventory_usage(scale, feature_profile),
        scale["opening_inventory"],
        business_type=str(feature_profile.get("business_type") or ""),
        inventory_policy=SUBLEDGER_SYNTHETIC_POLICIES["policy.inventory_operations"],
        warehouse_addresses=tuple(WAREHOUSE_ADDRESSES),
    )
    return sum(
        (_money(qty * unit) for qty, unit in zip(plan["opening_qtys"], plan["units"])),
        Decimal("0.00"),
    )


def _inventory_usage(
    scale: dict[str, Decimal],
    feature_profile: dict[str, bool],
) -> Decimal:
    """Annual cost run through inventory: sales COGS for any goods book
    (trade or register retail), a modest supplies flow otherwise."""
    return scale["cogs"]
