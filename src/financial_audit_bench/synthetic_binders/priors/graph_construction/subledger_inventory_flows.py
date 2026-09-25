"""Monthly inventory flows and manufacturing transforms."""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _allocate_units,
    _money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_movements import (
    _add_rni_receipts,
    _apply_rni_receipt_state,
    _movement,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_setup import (
    _InventoryBuildContext,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_steel_costing import (
    _apply_steel_fifo_costing,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_steel_flow import (
    _reshape_steel_production,
)


def _build_inventory_flows(context: _InventoryBuildContext) -> None:
    """Construct purchases, usage, shipments, RNI, and specialized flows."""
    records = context.records
    annual_cogs = context.annual_cogs
    ending_target = context.ending_target
    opening_cost = context.opening_cost
    business_type = context.business_type
    year_end = context.year_end
    draw_labels = context.draw_labels
    draws = context.draws
    inventory_execution_policy = context.inventory_execution_policy
    inventory_policy = context.inventory_policy
    items = context.items
    location = context.location
    manufacturing = context.manufacturing
    money = _money
    movement_builder = _movement
    movement_row_policy = context.movement_row_policy
    plan = context.plan
    source_sequence_high = context.source_sequence_high
    source_sequence_low = context.source_sequence_low
    steel_policy = context.steel_policy
    supplier_by_item = context.supplier_by_item
    allocate_units = _allocate_units

    total_purchases = annual_cogs + ending_target - opening_cost
    if total_purchases < 0:
        total_purchases = annual_cogs  # never sell below zero stock
        ending_target = opening_cost
    # Per-world seasonal sales curve: a drawn amplitude and peak month plus per-month
    # jitter, shared by every SKU so the whole book shares one authored seasonal curve
    # across the exercise.
    generation_policy = inventory_policy["deterministic_generation"]
    amplitude = draws.uniform(
        draw_labels["season_amplitude"],
        *inventory_policy["seasonal_amplitude"],
    )
    peak_month = draws.uniform(
        draw_labels["season_peak"],
        *generation_policy["peak_month_band"],
    )
    season = [
        (1 + float(amplitude) * math.cos((month - float(peak_month)) * math.pi / 6))
        * float(
            draws.uniform(
                str(draw_labels["season_jitter"]).format(month_2=f"{month:02d}"),
                *inventory_policy["month_jitter"],
            )
        )
        for month in range(1, 13)
    ]
    season_total = sum(season)
    movement_index = 0
    per_item: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        share = plan["shares"][index]
        unit = Decimal(item["standard_cost"])
        opening_qty = plan["opening_qtys"][index]
        item_cogs = money(annual_cogs * share)
        item_purchases = money(total_purchases * share)
        state = {
            "opening_qty": opening_qty,
            "opening_cost": money(opening_qty * unit),
            "purchase_qty": 0,
            "purchase_cost": Decimal("0"),
            "sales_qty": 0,
            "sales_cost": Decimal("0"),
            "unit": unit,
        }
        per_item[item["inventory_item_id"]] = state
        # Monthly flows: purchases land early in the month, shipments after, so per-item
        # stock never goes negative. Shipments follow the world's seasonal curve;
        # purchases are reorder-point POs in drawn lot sizes, with December truing
        # purchases up to the annual back-solve so the year still ends at the target.
        annual_ship = int(item_cogs / unit) if unit else 0
        annual_buy = int(item_purchases / unit) if unit else 0
        ship_plan = [int(annual_ship * weight / season_total) for weight in season]
        remainders = sorted(
            range(12),
            key=lambda m: annual_ship * season[m] / season_total - ship_plan[m],
            reverse=True,
        )
        for m in remainders[: annual_ship - sum(ship_plan)]:
            ship_plan[m] += 1
        lot = max(
            1,
            round(
                Decimal(annual_buy)
                * draws.uniform(
                    str(draw_labels["lot_fraction"]).format(
                        item_id=item["inventory_item_id"]
                    ),
                    *inventory_policy["lot_fraction"],
                )
            ),
        )
        cover = (
            draws.uniform(
                str(draw_labels["reorder_cover"]).format(
                    item_id=item["inventory_item_id"]
                ),
                *inventory_policy["reorder_cover_months"],
            )
            * Decimal(annual_ship)
            / Decimal(12)
        )
        weekly_perishable = item["category"] in {"food_protein", "food_produce"}
        perishable_buy_plan = (
            allocate_units(annual_buy, [Decimal(str(value)) for value in season])
            if weekly_perishable
            else None
        )
        for month in range(1, 13):
            available = (
                state["opening_qty"] + state["purchase_qty"] - state["sales_qty"]
            )
            bought = state["purchase_qty"]
            need = ship_plan[month - 1]
            if perishable_buy_plan is not None:
                buy_qty = perishable_buy_plan[month - 1]
            elif month == 12:
                buy_qty = annual_buy - bought
            elif available - need < cover and bought < annual_buy:
                shortfall = need + cover - available
                buy_qty = min(
                    max(math.ceil(shortfall / lot), 1) * lot, annual_buy - bought
                )
            else:
                buy_qty = 0
            if buy_qty > 0:
                ordinary_receipt_policy = inventory_execution_policy[
                    "monthly_movements"
                ]["purchase_receipts"]
                receipt_days = tuple(
                    int(value) for value in ordinary_receipt_policy["nominal_days"]
                )
                receipt_plan = allocate_units(
                    buy_qty, [Decimal("1")] * len(receipt_days)
                )
                for receipt_slot, (receipt_qty, receipt_day) in enumerate(
                    zip(receipt_plan, receipt_days, strict=True),
                    start=1,
                ):
                    if not receipt_qty:
                        continue
                    buy_date = date(year_end.year, month, receipt_day)
                    movement_index += 1
                    cost = money(receipt_qty * unit)
                    receipt_reference = draws.randint(
                        str(draw_labels["receipt_reference"]).format(
                            item_id=item["inventory_item_id"],
                            month_2=f"{month:02d}",
                            slot_2=f"{receipt_slot:02d}",
                        ),
                        source_sequence_low,
                        source_sequence_high,
                    )
                    receipt_row = movement_builder(
                        movement_index,
                        item,
                        location,
                        "purchase_receipt",
                        "in",
                        buy_date,
                        receipt_qty,
                        unit,
                        cost,
                        source=(
                            f"{movement_row_policy['ordinary_receipt_source_prefix']}-"
                            f"{receipt_reference}"
                        ),
                        execution_policy=inventory_execution_policy,
                    )
                    receipt_row["invoice_date"] = (
                        buy_date
                        + timedelta(
                            days=int(
                                ordinary_receipt_policy[
                                    "ordinary_invoice_lag_calendar_days"
                                ]
                            )
                        )
                    ).isoformat()
                    receipt_row["shipment_date"] = (
                        buy_date
                        - timedelta(
                            days=int(
                                ordinary_receipt_policy[
                                    "ordinary_shipment_lead_calendar_days"
                                ]
                            )
                        )
                    ).isoformat()
                    records["inventory_movements"].append(receipt_row)
                    state["purchase_qty"] += receipt_qty
                    state["purchase_cost"] += cost
            available = (
                state["opening_qty"] + state["purchase_qty"] - state["sales_qty"]
            )
            ship_qty = min(need, max(available - 1, 0))
            if ship_qty > 0:
                ship_day_rules = inventory_execution_policy["monthly_movements"][
                    "shipment_usage"
                ]["nominal_days"]
                ship_dates = [
                    (
                        date(year_end.year, month + 1, 1) - timedelta(days=1)
                        if day == "LAST_CALENDAR_DAY" and month < 12
                        else year_end
                        if day == "LAST_CALENDAR_DAY"
                        else date(year_end.year, month, int(day))
                    )
                    for day in ship_day_rules
                ]
                shipment_plan = allocate_units(
                    ship_qty, [Decimal("1")] * len(ship_dates)
                )
                for shipment_slot, (shipment_qty, ship_date) in enumerate(
                    zip(shipment_plan, ship_dates, strict=True),
                    start=1,
                ):
                    if not shipment_qty:
                        continue
                    movement_index += 1
                    cost = money(shipment_qty * unit)
                    shipment_reference = draws.randint(
                        str(draw_labels["shipment_reference"]).format(
                            item_id=item["inventory_item_id"],
                            month_2=f"{month:02d}",
                            slot_2=f"{shipment_slot:02d}",
                        ),
                        source_sequence_low,
                        source_sequence_high,
                    )
                    records["inventory_movements"].append(
                        movement_builder(
                            movement_index,
                            item,
                            location,
                            plan["outbound_kind"],
                            "out",
                            ship_date,
                            shipment_qty,
                            unit,
                            cost,
                            source=(
                                f"{plan['outbound_source_prefix']}-{shipment_reference}"
                            ),
                            execution_policy=inventory_execution_policy,
                        )
                    )
                state["sales_qty"] += ship_qty
                state["sales_cost"] += money(ship_qty * unit)

    if manufacturing:
        _reshape_steel_production(
            records,
            items,
            per_item,
            annual_cogs,
            year_end.year,
            location,
            steel_policy,
            inventory_execution_policy,
        )
        # RNI receipts join before costing so they earn a year-end FIFO
        # layer; their state folds in after, once costing fixes the price.
        rni_rows = _add_rni_receipts(
            records,
            items,
            year_end,
            inventory_policy,
            inventory_execution_policy,
        )
        _apply_steel_fifo_costing(
            records,
            items,
            per_item,
            steel_policy,
        )
        _apply_rni_receipt_state(per_item, rni_rows, include_cost=False)
    elif business_type == "manufacturing":
        rni_rows = _add_rni_receipts(
            records,
            items,
            year_end,
            inventory_policy,
            inventory_execution_policy,
        )
        _apply_rni_receipt_state(per_item, rni_rows)

    # Purchase-cost support must retain the supplier identity on the same receipt row as
    # quantity, price, and receiving evidence. Deriving the vendor later from a year-end
    # lot is both lossy (consumed FIFO layers are gone) and incorrect for manufactured
    # WIP/finished-goods layers, whose source reference is a production order rather
    # than a supplier.
    for movement in records["inventory_movements"]:
        movement["vendor_reference"] = (
            supplier_by_item[movement["inventory_item_id"]]
            if movement["movement_kind"] == "purchase_receipt"
            else None
        )
    context.per_item = per_item
