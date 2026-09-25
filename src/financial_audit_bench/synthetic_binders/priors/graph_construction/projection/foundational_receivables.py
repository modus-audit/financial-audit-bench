"""Accounts-receivable aging and allowance workbooks."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _add_schedule_sheet,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    open_pbc_sheet,
    save_workbook,
    write_header,
)

World = dict[str, Any]


def render_combined_ar_aging(path: Path, world: World, title: str):
    del title
    roll = world["accounts_receivable_rollforward"][0]
    wb, rec = open_pbc_sheet("AR-to-GL Reconciliation", "AR-01", world)
    write_header(rec, ["Reconciliation", "Amount"])
    cell_map = []
    for field, value in (
        ("Gross AR subledger", roll["ending_gross_receivable"]),
        ("Gross AR general ledger", roll["gl_balance"]),
        ("Difference", roll["difference"]),
    ):
        rec.append([field, str(value)])
        cell_map.append(
            {"field": field, "cell": f"B{rec.max_row}", "value": str(value)}
        )
    rec.append([])
    rec.append(
        [
            "Conclusion",
            roll.get("reconciliation_resolution") or "Reconciled without exception",
        ]
    )

    aging = world.get("accounts_receivable_aging") or []
    bases = {str(row.get("aging_basis") or "") for row in aging}
    if len(bases) != 1 or next(iter(bases)) not in {"due_date", "invoice_date"}:
        raise ValueError("AR aging requires one supported aging basis")
    basis = next(iter(bases))
    basis_label = "Due Date" if basis == "due_date" else "Invoice Date"
    detail = _add_schedule_sheet(wb, "AR Aging Detail", world)
    detail.append(["Aging basis", basis_label])
    detail.append(["Aging reference field", basis_label])
    write_header(
        detail,
        [
            "Customer",
            "Invoice",
            "Invoice Date",
            "Due Date",
            "Terms",
            "Days Past Due" if basis == "due_date" else "Days Since Invoice",
            "Original Amount",
            "Open Balance",
            "Aging Bucket",
            "Currency",
            "GL Account",
            "Business Unit",
        ],
    )
    for row in aging:
        detail.append(
            [
                row["customer_name"],
                row["invoice_number"],
                row["invoice_date"],
                row["due_date"],
                row["payment_terms"],
                row["days_past_due"],
                row["original_amount"],
                row["open_amount"],
                row["aging_bucket"],
                row["currency_code"],
                row["ar_gl_account_id"],
                row["business_unit"],
            ]
        )
    detail.append(
        ["Total", "", "", "", "", "", "", str(roll["ending_gross_receivable"])]
    )
    save_workbook(wb, path)
    return cell_map


def render_allowance_workbook(path: Path, world: World, title: str):
    del title
    policy = world["ar_allowance_policy"][0]
    estimates = world.get("ar_allowance_estimate") or []
    roll = world["accounts_receivable_rollforward"][0]
    wb, method = open_pbc_sheet("Allowance Methodology", "AR-07", world)
    for label, value in (
        ("Estimate objective", "Expected credit losses on year-end trade receivables"),
        ("Methodology", policy["methodology"]),
        ("Aging basis", policy.get("aging_date_label")),
        ("Forward-looking factors", policy.get("forward_looking_basis")),
        ("Effective date", policy.get("effective_date")),
        ("Approval status", "Approved"),
    ):
        method.append([label, value])

    pools = _add_schedule_sheet(wb, "Pool Calculation", world)
    write_header(
        pools,
        [
            "Aging Pool",
            "Exposure",
            "Loss Rate",
            "Reserve",
            "Prior Reserve",
            "Sensitivity",
        ],
    )
    for row in estimates:
        pools.append(
            [
                row["aging_pool"],
                row["exposure_amount"],
                row["loss_rate"],
                row["total_reserve"],
                row["prior_year_reserve"],
                row["sensitivity_amount"],
            ]
        )
    pools.append(
        [
            "Total",
            sum((Decimal(row["exposure_amount"]) for row in estimates), Decimal("0")),
            "",
            roll["allowance_balance"],
            sum(
                (Decimal(row["prior_year_reserve"]) for row in estimates), Decimal("0")
            ),
            sum(
                (Decimal(row["sensitivity_amount"]) for row in estimates), Decimal("0")
            ),
        ]
    )

    beginning = sum(
        (Decimal(row["prior_year_reserve"]) for row in estimates), Decimal("0")
    )
    ending = Decimal(roll["allowance_balance"])
    rollforward = _add_schedule_sheet(wb, "Rollforward", world)
    write_header(rollforward, ["Activity", "Amount"])
    for label, value in (
        ("Beginning allowance", beginning),
        ("Provision", ending - beginning),
        ("Write-offs", Decimal("0")),
        ("Ending allowance", ending),
    ):
        rollforward.append([label, value])
    save_workbook(wb, path)
    return []
