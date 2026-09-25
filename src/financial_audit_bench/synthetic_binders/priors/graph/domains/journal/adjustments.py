"""Post the retained fixed-asset adjustment into the client ledger."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.presentation import (
    DESCRIPTION_BY_TYPE,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.rule(
    "post_client_adjusting_entries",
    inputs=("fixed_asset", "fixed_asset_depreciation", "fiscal_calendar"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def post_client_adjusting_entries(
    fixed_assets: list[dict[str, Any]],
    depreciation_rows: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets = {row["fixed_asset_id"]: row for row in fixed_assets}
    entries: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    description = DESCRIPTION_BY_TYPE.get("depreciation", "depreciation")
    for row in depreciation_rows:
        amount = Decimal(str(row["current_depreciation"]))
        if not amount:
            continue
        asset = assets[row["fixed_asset_id"]]
        entry_id = f"JOURNAL-ADJ-DEPR-{row['fixed_asset_id']}"
        entries.append(
            {
                "journal_entry_id": entry_id,
                "journal_type": "adjusting",
                "posting_date": str(calendar["end_date"]),
                "voucher_id": row["fixed_asset_id"],
            }
        )
        for account, signed in (
            (asset["depreciation_expense_gl_account_id"], amount),
            (asset["accumulated_depreciation_gl_account_id"], -amount),
        ):
            lines.append(
                {
                    "journal_entry_id": entry_id,
                    "gl_account_id": account,
                    "signed_amount": str(signed),
                    "description": description,
                    "posting_type": "ledger",
                }
            )
    return entries, lines
