"""Journal workflow, source-support, and extraction records."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.presentation import (
    _entry_time,
)


def journal_approvals(
    entries: list[dict[str, Any]], users: list[dict[str, str]]
) -> list[dict[str, str | bool]]:
    """Derive the approval record displayed and validated for each entry."""
    user_ids = {row["accounting_user_id"] for row in users}
    if {"USER-PREPARER", "USER-APPROVER"} - user_ids:
        raise ValueError("journal approval users are incomplete")
    return [
        {
            "approval_status": "approved",
            "approval_threshold": "1000.00",
            "approval_timestamp": (
                f"{entry['posting_date']}T"
                f"{_entry_time(entry['journal_entry_id'] + '-APPROVAL', 12, 2)}"
                if entry["generation_type"] == "manual"
                else f"{entry['posting_date'][:4]}-01-02T09:00:00"
            ),
            "approver_user_id": "USER-APPROVER",
            "journal_approval_id": f"APPROVAL-{entry['journal_entry_id']}",
            "journal_entry_id": entry["journal_entry_id"],
            "override_used": False,
            "preparer_user_id": entry["user_id"],
            "self_approval": False,
        }
        for entry in entries
    ]
