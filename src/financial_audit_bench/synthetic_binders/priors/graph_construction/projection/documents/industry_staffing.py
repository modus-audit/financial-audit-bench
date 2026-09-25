"""Compact staffing time and billing support workbook."""

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


def _applicable(world: World) -> bool:
    return world.get("business_type") == "staffing_services"


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    entries = world.get("staffing_time_entry") or []
    if not entries:
        return []
    workers = {row["employee_id"]: row for row in world.get("employee") or []}
    customers = {row["customer_id"]: row for row in world.get("customer") or []}
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    workbook, time_sheet = open_pbc_sheet(
        "Approved Assignment Time",
        "DOC-STAFFING-TIME",
        world=world,
    )
    write_header(
        time_sheet,
        [
            "Week Ending",
            "Invoice",
            "Customer",
            "Assignment",
            "Worker",
            "Regular Hours",
            "Overtime Hours",
            "Billable Hours",
            "Bill Rate",
            "Pay Rate",
            "Internal Approval",
            "Customer Approval",
        ],
    )
    for row in sorted(
        entries,
        key=lambda value: (
            value["week_ending"],
            value["employee_id"],
            value["staffing_time_entry_id"],
        ),
    ):
        time_sheet.append(
            [
                row["week_ending"],
                invoices[row["customer_invoice_id"]]["invoice_number"],
                customers[row["customer_id"]]["customer_name"],
                row["assignment_reference"],
                workers[row["employee_id"]]["full_name"],
                Decimal(str(row["regular_hours"])),
                Decimal(str(row["overtime_hours"])),
                Decimal(str(row["billable_hours"])),
                Decimal(str(row["bill_rate"])),
                Decimal(str(row["pay_rate"])),
                f"{row['internal_approved_by']} / {row['internal_approved_date']}",
                f"{row['approved_by']} / {row['approved_date']}",
            ]
        )
    time_sheet.freeze_panes = "A2"

    reconciliation = workbook.create_sheet("Billing Reconciliation")
    write_header(
        reconciliation,
        [
            "Invoice",
            "Customer",
            "Service Period",
            "Billable Hours",
            "Time-Derived Billing",
            "Invoice Amount",
            "Difference",
            "Billing Deadline",
            "Invoice Date",
            "Cadence Status",
        ],
    )
    entries_by_invoice: dict[str, list[dict[str, Any]]] = {}
    for row in entries:
        entries_by_invoice.setdefault(row["customer_invoice_id"], []).append(row)
    for invoice_id, invoice_entries in sorted(entries_by_invoice.items()):
        invoice = invoices[invoice_id]
        hours = sum(
            (Decimal(str(row["billable_hours"])) for row in invoice_entries),
            Decimal("0"),
        )
        time_amount = sum(
            (
                Decimal(str(row["billable_hours"])) * Decimal(str(row["bill_rate"]))
                for row in invoice_entries
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        invoice_amount = Decimal(str(invoice["original_amount"]))
        reconciliation.append(
            [
                invoice["invoice_number"],
                customers[invoice["customer_id"]]["customer_name"],
                f"{min(row['week_ending'] for row in invoice_entries)} to "
                f"{max(row['week_ending'] for row in invoice_entries)}",
                hours,
                time_amount,
                invoice_amount,
                time_amount - invoice_amount,
                invoice["billing_deadline"],
                invoice["invoice_date"],
                (
                    "Within cadence"
                    if invoice["invoice_date"] <= invoice["billing_deadline"]
                    else "Approved exception"
                ),
            ]
        )
    reconciliation.freeze_panes = "A2"

    path = (
        output_dir
        / "payroll"
        / "Approved Assignment Time and Billing Reconciliation.xlsx"
    )
    return [_save(workbook, path, "DOC-STAFFING-TIME", "payroll")]
