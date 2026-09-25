"""Accounts-payable cutoff projection operations."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_common import (
    near_year_end,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
    op,
)


@op("filter_ap_cutoff_population")
def filter_ap_cutoff_population(
    world: World, node_id: str, field: str, value: Any
) -> Any:
    """The invoice reference per cutoff-window row: one column of the cutoff register — the sibling AP-14 columns filter to the same window so the whole request renders as ONE joined table. The client-id map relabels each reference to the vendor's invoice number."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
        package_identity,
    )

    invoice_numbers = package_identity(world).invoice_numbers
    references = []
    for row in world["vendor_invoice"]:
        if not near_year_end(world, row):
            continue
        reference = invoice_numbers.get(row["vendor_invoice_id"])
        if not reference:
            raise ValueError(
                "AP cutoff invoice lacks a client-facing vendor reference: "
                f"{row['vendor_invoice_id']}"
            )
        references.append(reference)
    return references or None


@op("filter_cutoff_column")
def filter_cutoff_column(world: World, node_id: str, field: str, value: Any) -> Any:
    """A vendor_invoice column restricted to the AP-14 cutoff window."""
    return [
        row[field] for row in world["vendor_invoice"] if near_year_end(world, row)
    ] or None


@op("join_cutoff_receipt_date")
def join_cutoff_receipt_date(world: World, node_id: str, field: str, value: Any) -> Any:
    """Receipt date (service-period end for service invoices) per cutoff row."""
    receipts = {
        row["goods_receipt_id"]: row["receipt_date"]
        for row in world.get("goods_receipt") or []
    }
    return [
        receipts.get(row.get("goods_receipt_id")) or row["service_period_end"]
        for row in world["vendor_invoice"]
        if near_year_end(world, row)
    ] or None


@op("join_cutoff_vendor")
def join_cutoff_vendor(world: World, node_id: str, field: str, value: Any) -> Any:
    vendor_names = {row["vendor_id"]: row["name"] for row in world["vendor"]}
    return [
        vendor_names[row["vendor_id"]]
        for row in world["vendor_invoice"]
        if near_year_end(world, row)
    ] or None


@op("purchase_detail_report_purpose")
def purchase_detail_report_purpose(
    world: World, node_id: str, field: str, value: Any
) -> str:
    return "Purchasing and receiving cutoff population; join to invoices by vendor, invoice, PO, and receipt."
