"""Catalog-backed journal presentation and chart-of-accounts enrichment."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.contracts.journal_risk import (
    apply_account_semantic_roles,
)
from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
    to_mutable_json,
)


def enrich_general_ledger_accounts(
    accounts: list[dict[str, Any]],
) -> None:
    """Add the account-master fields required by the retained JE package."""
    normal_balance = {
        "asset": "debit",
        "contra_asset": "credit",
        "equity": "credit",
        "expense": "debit",
        "liability": "credit",
        "revenue": "credit",
        "gains": "credit",
    }
    for account in accounts:
        account.update(
            {
                "account_number": account["gl_account_id"],
                "changed_date": "2025-01-01",
                "created_date": "2016-01-01",
                "fsli_mapping": account["name"],
                "lifecycle_status": "active",
                "normal_balance": normal_balance[account["account_class"]],
            }
        )
    apply_account_semantic_roles(accounts)


_journal_presentation_values = load_authored_policy(
    "authored.global.operating-policy.v1"
).values["journal_presentation"]


def _entry_time(journal_entry_id: str, start_hour: int, span_hours: int) -> str:
    """Assign an entry a deterministic time inside its processing window."""
    selector = sum(map(ord, journal_entry_id))
    minutes = selector % (span_hours * 60)
    return f"{start_hour + minutes // 60:02d}:{minutes % 60:02d}:{selector % 60:02d}"


# journal_type -> a substantive posting description used consistently by the
# ledger and its generated support package.
DESCRIPTION_BY_TYPE = to_mutable_json(
    _journal_presentation_values["description_by_type"]
)


# Benchmark classification for entry families that use the generated manual approval
# workflow. The accounted ``manual_entry_share`` finalize feature may retag a closed
# subset of these families per world.
MANUAL_JOURNAL_TYPES = tuple(_journal_presentation_values["manual_journal_types"])

del _journal_presentation_values
