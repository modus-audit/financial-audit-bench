"""Three-stage steel production-flow construction."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    _allocate_amount,
    _allocate_units,
    _money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_steel_calendar import (
    _regularize_steel_production_calendar,
)


def _reshape_steel_production(
    records: dict[str, list[dict[str, Any]]],
    items: list[dict[str, Any]],
    per_item: dict[str, dict[str, Any]],
    annual_cogs: Decimal,
    fiscal_year: int,
    location: dict[str, Any],
    steel_policy: Mapping[str, Any],
    inventory_execution_policy: Mapping[str, Any],
) -> None:
    """Convert the generic purchase/shipment book into a three-stage mill flow."""
    by_category: dict[str, list[dict[str, Any]]] = {
        "raw_material": [],
        "work_in_process": [],
        "finished_goods": [],
    }
    for item in items:
        if item["category"] in by_category:
            by_category[item["category"]].append(item)
    if any(not rows for rows in by_category.values()):
        return
    movements_by_item: dict[str, list[dict[str, Any]]] = {}
    for movement in records["inventory_movements"]:
        movements_by_item.setdefault(movement["inventory_item_id"], []).append(movement)

    target_end = {
        item_id: state["opening_cost"] + state["purchase_cost"] - state["sales_cost"]
        for item_id, state in per_item.items()
    }

    def item_weights(category: str, side: str) -> list[Decimal]:
        values = []
        for item in by_category[category]:
            state = per_item[item["inventory_item_id"]]
            values.append(
                state["sales_cost"] if side == "out" else state["purchase_cost"]
            )
        return values

    def assign(
        item: dict[str, Any],
        inbound: Decimal,
        outbound: Decimal,
        inbound_quantity: int,
        outbound_quantity: int,
        inbound_kind: str,
        outbound_kind: str,
    ) -> None:
        item_id = item["inventory_item_id"]
        # A low-scale allocation may legitimately leave an item with no generic row. The
        # monthly steel regularizer below constructs any required positive stage row
        # from the admitted state, so absence is not an exceptional or donor-filled
        # condition.
        rows = movements_by_item.get(item_id, [])
        incoming = [row for row in rows if row["direction"] == "in"]
        outgoing = [row for row in rows if row["direction"] == "out"]
        for group, total, total_quantity, kind, prefix in (
            (
                incoming,
                inbound,
                inbound_quantity,
                inbound_kind,
                "PRD" if inbound_kind == "production_completion" else "RCV",
            ),
            (
                outgoing,
                outbound,
                outbound_quantity,
                outbound_kind,
                "ISS" if outbound_kind == "production_issue" else "SHP",
            ),
        ):
            quantities = _allocate_units(
                total_quantity,
                [Decimal(str(row["quantity"])) for row in group],
            )
            costs = _allocate_amount(
                total, [Decimal(quantity) for quantity in quantities]
            )
            for row, quantity_int, cost in zip(group, quantities, costs):
                quantity = Decimal(quantity_int)
                row["quantity"] = str(quantity_int)
                row["extended_cost"] = str(cost)
                row["unit_cost"] = str(
                    (cost / quantity).quantize(Decimal("0.0001"))
                    if quantity
                    else Decimal("0")
                )
                row["movement_kind"] = kind
                row["source_document"] = (
                    f"{prefix}-{row['posting_date'].replace('-', '')}-{row['inventory_movement_id'][-4:]}"
                )
                if kind in {"production_completion", "production_issue"}:
                    row["invoice_date"] = None
                    row["receipt_date"] = (
                        row["posting_date"] if kind == "production_completion" else None
                    )
                    row["shipment_date"] = None
                    row["shipping_terms"] = None
                    row["in_transit_status"] = (
                        "completed" if kind == "production_completion" else "issued"
                    )
        state = per_item[item_id]
        state["purchase_qty"] = (
            inbound_quantity if inbound_kind == "purchase_receipt" else 0
        )
        state["production_qty"] = (
            inbound_quantity if inbound_kind == "production_completion" else 0
        )
        state["purchase_cost"] = (
            inbound if inbound_kind == "purchase_receipt" else Decimal("0")
        )
        state["production_cost"] = (
            inbound if inbound_kind == "production_completion" else Decimal("0")
        )
        state["sales_qty"] = outbound_quantity
        state["sales_cost"] = outbound
        book_qty = (
            state["opening_qty"]
            + state["purchase_qty"]
            + state["production_qty"]
            - state["sales_qty"]
        )
        if book_qty > 0:
            state["unit"] = (target_end[item_id] / book_qty).quantize(Decimal("0.0001"))
            item["standard_cost"] = str(state["unit"])

    raw = by_category["raw_material"]
    wip = by_category["work_in_process"]
    finished = by_category["finished_goods"]

    # The generic inventory pass spreads external COGS over every category. Reusing only
    # its FG shipment quantity made a manufacturer ship a small fraction of planned
    # demand: RM/WIP generic outflows had become internal issues, while their share of
    # demand simply disappeared.
    def category_opening_quantity(rows: list[dict[str, Any]]) -> int:
        return sum(per_item[item["inventory_item_id"]]["opening_qty"] for item in rows)

    def category_target_ending_quantity(rows: list[dict[str, Any]]) -> int:
        return sum(
            per_item[item["inventory_item_id"]]["opening_qty"]
            + per_item[item["inventory_item_id"]]["purchase_qty"]
            - per_item[item["inventory_item_id"]]["sales_qty"]
            for item in rows
        )

    generic_fg_quantity = sum(
        per_item[item["inventory_item_id"]]["sales_qty"] for item in finished
    )
    generic_fg_cost = sum(
        (per_item[item["inventory_item_id"]]["sales_cost"] for item in finished),
        Decimal("0"),
    )
    if generic_fg_quantity > 0 and generic_fg_cost > 0:
        estimated_fg_unit_cost = generic_fg_cost / Decimal(generic_fg_quantity)
    else:
        # The independent low-scale policy forbids assuming that a generic pre-pass
        # emitted a movement for every item. A positive annual cost target therefore
        # uses the admitted finished-item costs as its de-novo quantity proxy; a zero
        # target needs no production reshape.
        if annual_cogs == 0:
            return
        estimated_fg_unit_cost = sum(
            (Decimal(item["standard_cost"]) for item in finished),
            Decimal("0"),
        ) / Decimal(len(finished))
        if estimated_fg_unit_cost <= 0:
            raise ValueError(
                "steel production has no positive admitted finished-item cost"
            )
    finished_shipment_total = max(
        1,
        math.ceil(annual_cogs / estimated_fg_unit_cost),
    )

    def required_stage_quantities(
        shipment_total: int,
    ) -> tuple[int, int, int, int, int]:
        finished_completion = max(
            1,
            shipment_total
            + category_target_ending_quantity(finished)
            - category_opening_quantity(finished),
        )
        # Each stage has an explicit synthetic yield loss. Back-solving with a ceiling
        # guarantees that the upstream issue can support the requested downstream
        # completion without inventing output quantity.
        wip_to_finished_yield = Decimal(
            str(steel_policy["finished_output_units"])
        ) / Decimal(str(steel_policy["wip_output_units"]))
        wip_issue = max(
            1,
            math.ceil(Decimal(finished_completion) / wip_to_finished_yield),
        )
        wip_completion = max(
            1,
            wip_issue
            + category_target_ending_quantity(wip)
            - category_opening_quantity(wip),
        )
        raw_to_wip_yield = Decimal(str(steel_policy["wip_output_units"])) / Decimal(
            str(steel_policy["raw_input_units"])
        )
        raw_issue = max(
            1,
            math.ceil(Decimal(wip_completion) / raw_to_wip_yield),
        )
        raw_purchase = max(
            1,
            raw_issue
            + category_target_ending_quantity(raw)
            - category_opening_quantity(raw),
        )
        return (
            finished_completion,
            wip_issue,
            wip_completion,
            raw_issue,
            raw_purchase,
        )

    def estimated_conversion_cost(
        wip_completion: int,
        finished_completion: int,
    ) -> Decimal:
        def stage_cost(
            quantity: int,
            hours_per_ton: Decimal,
            labor_rate: Decimal,
            overhead_rate: Decimal,
        ) -> Decimal:
            hours = (Decimal(quantity) * hours_per_ton).quantize(Decimal("0.01"))
            return _money(hours * labor_rate) + _money(hours * overhead_rate)

        return stage_cost(
            wip_completion,
            Decimal(str(steel_policy["wip_hours_per_ton"])),
            Decimal(str(steel_policy["labor_rate"])),
            Decimal(str(steel_policy["overhead_rate"])),
        ) + stage_cost(
            finished_completion,
            Decimal(str(steel_policy["finishing_hours_per_ton"])),
            Decimal(str(steel_policy["labor_rate"])),
            Decimal(str(steel_policy["overhead_rate"])),
        )

    # Conversion is capitalized with a COGS credit. Therefore customer shipment relief
    # must cover both the annual net COGS plan and conversion capitalized during the
    # year.
    for _ in range(8):
        stage_quantities = required_stage_quantities(finished_shipment_total)
        conversion = estimated_conversion_cost(stage_quantities[2], stage_quantities[0])
        revised_shipment_total = max(
            1,
            math.ceil((annual_cogs + conversion) / estimated_fg_unit_cost),
        )
        if revised_shipment_total == finished_shipment_total:
            break
        finished_shipment_total = revised_shipment_total
    (
        finished_completion_total,
        wip_issue_total,
        wip_completion_total,
        raw_issue_total,
        raw_purchase_total,
    ) = required_stage_quantities(finished_shipment_total)
    raw_purchase_quantities = _allocate_units(
        raw_purchase_total,
        [Decimal(per_item[item["inventory_item_id"]]["purchase_qty"]) for item in raw],
    )
    raw_capacities = [
        per_item[item["inventory_item_id"]]["opening_qty"] + purchase
        for item, purchase in zip(raw, raw_purchase_quantities)
    ]
    raw_issue_quantities = _allocate_units(
        raw_issue_total,
        [Decimal(value) for value in raw_capacities],
        raw_capacities,
    )

    wip_completion_quantities = _allocate_units(
        wip_completion_total,
        [Decimal(per_item[item["inventory_item_id"]]["purchase_qty"]) for item in wip],
    )
    wip_capacities = [
        per_item[item["inventory_item_id"]]["opening_qty"] + completion
        for item, completion in zip(wip, wip_completion_quantities)
    ]
    wip_issue_quantities = _allocate_units(
        wip_issue_total,
        [Decimal(value) for value in wip_capacities],
        wip_capacities,
    )

    finished_completion_quantities = _allocate_units(
        finished_completion_total,
        [
            Decimal(per_item[item["inventory_item_id"]]["purchase_qty"])
            for item in finished
        ],
    )
    finished_capacities = [
        per_item[item["inventory_item_id"]]["opening_qty"] + completion
        for item, completion in zip(finished, finished_completion_quantities)
    ]
    finished_shipment_quantities = _allocate_units(
        finished_shipment_total,
        [Decimal(value) for value in finished_capacities],
        finished_capacities,
    )

    fg_sales = _allocate_amount(annual_cogs, item_weights("finished_goods", "out"))
    fg_production = [
        sale
        + target_end[item["inventory_item_id"]]
        - per_item[item["inventory_item_id"]]["opening_cost"]
        for item, sale in zip(finished, fg_sales)
    ]
    for item, production, sale, production_qty, shipment_qty in zip(
        finished,
        fg_production,
        fg_sales,
        finished_completion_quantities,
        finished_shipment_quantities,
    ):
        assign(
            item,
            production,
            sale,
            production_qty,
            shipment_qty,
            "production_completion",
            "sale_shipment",
        )

    wip_issues = _allocate_amount(
        sum(fg_production, Decimal("0")), item_weights("work_in_process", "out")
    )
    wip_production = [
        issue
        + target_end[item["inventory_item_id"]]
        - per_item[item["inventory_item_id"]]["opening_cost"]
        for item, issue in zip(wip, wip_issues)
    ]
    for item, production, issue, production_qty, issue_qty in zip(
        wip,
        wip_production,
        wip_issues,
        wip_completion_quantities,
        wip_issue_quantities,
    ):
        assign(
            item,
            production,
            issue,
            production_qty,
            issue_qty,
            "production_completion",
            "production_issue",
        )

    raw_issues = _allocate_amount(
        sum(wip_production, Decimal("0")), item_weights("raw_material", "out")
    )
    raw_purchases = [
        issue
        + target_end[item["inventory_item_id"]]
        - per_item[item["inventory_item_id"]]["opening_cost"]
        for item, issue in zip(raw, raw_issues)
    ]
    for item, purchase, issue, purchase_qty, issue_qty in zip(
        raw,
        raw_purchases,
        raw_issues,
        raw_purchase_quantities,
        raw_issue_quantities,
    ):
        assign(
            item,
            purchase,
            issue,
            purchase_qty,
            issue_qty,
            "purchase_receipt",
            "production_issue",
        )
    _regularize_steel_production_calendar(
        records,
        items,
        per_item,
        fiscal_year,
        location,
        steel_policy,
        inventory_execution_policy,
    )
