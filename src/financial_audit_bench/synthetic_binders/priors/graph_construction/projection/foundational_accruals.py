"""Render the client accrual rollforward."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    open_pbc_sheet,
    save_workbook,
    write_header,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)

World = dict[str, Any]


def render_accrual_rollforward(
    path: Path, world: World, title: str
) -> list[dict[str, Any]] | None:
    accruals = world.get("accrued_expense") or []
    if not accruals:
        return None
    wb, ws = open_pbc_sheet(title, "AP-07", world)
    ws.title = "Accrual Rollforward"
    write_header(
        ws,
        [
            "Accrual ID",
            "Vendor",
            "Accrual Type",
            "Service Period End",
            "Basis",
            "Opening Balance",
            "Additions",
            "Reversals",
            "Settlements",
            "Ending Balance",
        ],
    )
    vendors = {row["vendor_id"]: row["name"] for row in world.get("vendor") or []}
    public_ids = client_id_map(world)
    totals = [Decimal("0")] * 5
    for row in sorted(
        accruals,
        key=lambda item: (item["service_period_end"], item["accrued_expense_id"]),
    ):
        if row["vendor_id"] not in vendors:
            raise ValueError(f"accrual {row['accrued_expense_id']} has no vendor")
        values = [
            Decimal(str(row.get(field) or "0"))
            for field in (
                "opening_balance",
                "addition_amount",
                "reversal_amount",
                "settlement_amount",
                "ending_balance",
            )
        ]
        ws.append(
            [
                public_ids.get(row["accrued_expense_id"], row["accrued_expense_id"]),
                vendors[row["vendor_id"]],
                str(row["category"]).replace("_", " ").title(),
                row["service_period_end"],
                str(row["estimate_method"]).replace("_", " ").title(),
                *values,
            ]
        )
        totals = [total + value for total, value in zip(totals, values)]

    if (world.get("company_feature_profile") or {}).get("has_payroll"):
        closing = {
            row["gl_account_id"]: Decimal(row["closing_balance"])
            for row in world.get("trial_balance_account") or []
        }
        opening = {
            row["gl_account_id"]: Decimal(row["opening_balance"])
            for row in world.get("opening_account_balance") or []
        }
        payroll_end = -closing.get("GL-WH-PAYABLE-001", Decimal("0"))
        payroll_open = -opening.get("GL-WH-PAYABLE-001", Decimal("0"))
        if payroll_end > 0 or payroll_open > 0:
            change = payroll_end - payroll_open
            values = [
                payroll_open,
                max(change, Decimal("0")),
                Decimal("0"),
                max(-change, Decimal("0")),
                payroll_end,
            ]
            ws.append(
                [
                    "GL-WH-PAYABLE-001",
                    "Payroll",
                    "Accrued payroll and withholdings",
                    world["fiscal_calendar"]["end_date"],
                    "Payroll register",
                    *values,
                ]
            )
            totals = [total + value for total, value in zip(totals, values)]

    total_row = ws.max_row + 1
    ws.append(["Total", "", "", "", "", *totals])
    save_workbook(wb, path)
    return [
        {
            "field": "Total opening accruals",
            "cell": f"F{total_row}",
            "value": str(totals[0]),
        },
        {
            "field": "Total ending accruals",
            "cell": f"J{total_row}",
            "value": str(totals[4]),
        },
    ]
