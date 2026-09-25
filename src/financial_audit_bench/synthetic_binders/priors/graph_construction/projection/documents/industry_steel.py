"""Compact manufacturing production and yield workbook."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.industry_support import (
    _save,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    open_pbc_sheet,
    write_header,
)


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    orders = world.get("steel_production_order") or []
    if not orders:
        return []

    wb, ws = open_pbc_sheet("Production Orders", "DOC-STEEL-PRODUCTION", world=world)
    write_header(
        ws,
        [
            "Production Order",
            "Month",
            "Raw Issue Date",
            "Raw Issued (tons)",
            "WIP Completion Date",
            "WIP Completed (tons)",
            "Finished Completion Date",
            "Finished Completed (tons)",
            "Shipment Date",
            "Finished Shipped (tons)",
            "Raw-to-WIP Loss (tons)",
            "WIP-to-Finished Loss (tons)",
            "Status",
        ],
    )
    ordered = sorted(orders, key=lambda row: row["production_month"])
    for order in ordered:
        ws.append(
            [
                order["production_order_number"],
                order["production_month"],
                order["raw_material_issue_date"],
                order["raw_material_issued_quantity"],
                order["wip_completion_date"],
                order["wip_completed_quantity"],
                order["finished_goods_completion_date"],
                order["finished_goods_completed_quantity"],
                order["shipment_date"],
                order["finished_goods_shipped_quantity"],
                order["raw_to_wip_loss_quantity"],
                order["wip_to_finished_loss_quantity"],
                order["status"],
            ]
        )
    ws.freeze_panes = "A2"

    yield_sheet = wb.create_sheet("Monthly Yield")
    write_header(
        yield_sheet,
        [
            "Month",
            "Raw Material Issued (tons)",
            "WIP Completed (tons)",
            "Raw-to-WIP Yield",
            "WIP Issued (tons)",
            "Finished Goods Completed (tons)",
            "WIP-to-Finished Yield",
            "Finished Goods Shipped (tons)",
        ],
    )
    for order in ordered:
        raw = Decimal(str(order["raw_material_issued_quantity"]))
        wip = Decimal(str(order["wip_completed_quantity"]))
        wip_issued = Decimal(str(order["wip_issued_quantity"]))
        finished = Decimal(str(order["finished_goods_completed_quantity"]))
        yield_sheet.append(
            [
                order["production_month"],
                raw,
                wip,
                wip / raw if raw else None,
                wip_issued,
                finished,
                finished / wip_issued if wip_issued else None,
                order["finished_goods_shipped_quantity"],
            ]
        )
    yield_sheet.freeze_panes = "A2"
    path = output_dir / "inventory" / "Steel Production Orders and Yield.xlsx"
    return [_save(wb, path, "DOC-STEEL-PRODUCTION", "inventory")]
