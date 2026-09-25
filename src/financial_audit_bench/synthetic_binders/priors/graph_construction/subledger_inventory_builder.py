"""Ordered inventory population orchestration over bounded lifecycle phases."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_evidence import (
    _build_inventory_evidence,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_flows import (
    _build_inventory_flows,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_setup import (
    _prepare_inventory_build,
)


def _build_inventory(
    records: dict[str, list[dict[str, Any]]],
    company: dict[str, str],
    calendar: dict[str, Any],
    year_end: date,
    annual_cogs: Decimal,
    opening_cost: Decimal,
    ending_target: Decimal,
    business_type: str,
    vendor_master: list[dict[str, Any]],
) -> None:
    """A SKU list with monthly purchase and shipment flows: per item, opening + purchases - cost of sales + count adjustment = ending. The year's purchases are sized so the total ends at the target."""
    context = _prepare_inventory_build(
        records,
        company,
        calendar,
        year_end,
        annual_cogs,
        opening_cost,
        ending_target,
        business_type,
        vendor_master,
    )
    _build_inventory_flows(context)
    _build_inventory_evidence(context)
