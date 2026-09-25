"""Inventory costing, production-stage, and delegated purchase postings."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.inventory.common import (
    COGS_GL,
    INVENTORY_GL,
    _minor_units,
    _sales,
    _scaled_decimal,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "relieve_inventory_cogs",
    inputs=("inventory_movement", "inventory_count_line", "inventory_count"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=80,
    gate="has_inventory",
)
def relieve_inventory_cogs(
    movements: list[dict[str, Any]],
    count_lines: list[dict[str, Any]],
    counts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Non-cash entries: Dr COGS / Cr inventory for each inventory outflow
    and the physical-count shrinkage variance."""
    entries, lines = [], []

    def post(entry_id: str, journal_type: str, posting_date: str, amount: int) -> None:
        entries.append(
            {
                "journal_entry_id": entry_id,
                "journal_type": journal_type,
                "posting_date": posting_date,
                "voucher_id": entry_id.replace("JOURNAL-", "V"),
            }
        )
        lines.extend(
            (
                {
                    "gl_account_id": COGS_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(amount, 2)),
                },
                {
                    "gl_account_id": INVENTORY_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(-amount, 2)),
                },
            )
        )

    for sale in _sales(movements):
        post(
            f"JOURNAL-COGS-{sale['inventory_movement_id']}",
            "cogs_relief",
            sale["posting_date"],
            _minor_units(sale["extended_cost"], "inventory outflow extended cost"),
        )
    count_date = {c["inventory_count_id"]: c["count_date"] for c in counts}
    for line in count_lines:
        shrinkage = -_minor_units(
            line["variance_amount"], "inventory count variance amount"
        )
        if shrinkage:
            post(
                f"JOURNAL-SHRINK-{line['inventory_count_line_id']}",
                "inventory_shrinkage",
                count_date[line["inventory_count_id"]],
                shrinkage,
            )
    return entries, lines


@REGISTRY.rule(
    "capitalize_inventory_conversion_costs",
    inputs=("inventory_movement",),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=81,
    gate="has_inventory",
)
def capitalize_inventory_conversion_costs(
    movements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for movement in movements:
        if movement["movement_kind"] != "production_completion":
            continue
        conversion = _minor_units(
            movement.get("conversion_labor_added") or "0",
            "inventory conversion labor",
        ) + _minor_units(
            movement.get("conversion_overhead_added") or "0",
            "inventory conversion overhead",
        )
        if not conversion:
            continue
        entry_id = f"JOURNAL-CONVERSION-{movement['inventory_movement_id']}"
        entries.append(
            {
                "journal_entry_id": entry_id,
                "journal_type": "production_conversion_capitalization",
                "posting_date": movement["posting_date"],
                "voucher_id": entry_id.replace("JOURNAL-", "V"),
            }
        )
        lines.extend(
            (
                {
                    "gl_account_id": INVENTORY_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(conversion, 2)),
                },
                {
                    "gl_account_id": COGS_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(-conversion, 2)),
                },
            )
        )
    return entries, lines


@REGISTRY.rule(
    "transfer_inventory_production_stages",
    inputs=("inventory_movement", "inventory_item"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=82,
    gate="has_inventory",
)
def transfer_inventory_production_stages(
    movements: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Net-zero entries on the inventory control account whose legs the account projection routes to the stage sub-accounts (RM -> WIP -> FG), so the stage balances roll instead of drifting on one-sided postings."""
    category = {row["inventory_item_id"]: row["category"] for row in items}
    stage_by_category = {"raw_material": "WIP", "work_in_process": "FG"}
    monthly_cost: dict[tuple[str, str], int] = {}
    monthly_date: dict[tuple[str, str], str] = {}
    for row in movements:
        if row["movement_kind"] != "production_issue":
            continue
        stage = stage_by_category.get(category.get(row["inventory_item_id"], ""))
        if stage is None:
            continue
        key = (row["posting_date"][:7], stage)
        monthly_cost[key] = monthly_cost.get(key, 0) + _minor_units(
            row["extended_cost"], "inventory production-issue extended cost"
        )
        monthly_date[key] = max(monthly_date.get(key, ""), row["posting_date"])
    entries: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for (month, stage), amount in sorted(monthly_cost.items()):
        if not amount:
            continue
        entry_id = f"JOURNAL-INVXFER-{stage}-{month.replace('-', '')}"
        entries.append(
            {
                "journal_entry_id": entry_id,
                "journal_type": "production_stage_transfer",
                "posting_date": monthly_date[(month, stage)],
                "voucher_id": entry_id.replace("JOURNAL-", "V"),
            }
        )
        lines.extend(
            (
                {
                    "gl_account_id": INVENTORY_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(amount, 2)),
                },
                {
                    "gl_account_id": INVENTORY_GL,
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(_scaled_decimal(-amount, 2)),
                },
            )
        )
    return entries, lines
