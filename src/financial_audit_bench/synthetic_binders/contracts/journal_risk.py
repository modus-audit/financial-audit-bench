"""Minimal journal metadata retained by the public generator."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)

_POLICY = load_authored_policy("authored.global.operating-policy.v1").values[
    "journal_risk"
]
_ACCESS_RIGHTS = dict(_POLICY["access_rights_by_privilege"])
_ESTIMATE_TOKENS = _POLICY["estimate_tokens"]


def assign_stable_journal_line_ids(world: dict[str, Any]) -> None:
    """Fill missing journal-line IDs with readable sequence numbers."""
    rows = world.get("journal_entry_line") or []
    used = {
        str(row["journal_entry_line_id"])
        for row in rows
        if row.get("journal_entry_line_id")
    }
    number = 1
    for row in rows:
        if not row.get("journal_entry_line_id"):
            while f"JEL-{number:08d}" in used:
                number += 1
            row["journal_entry_line_id"] = f"JEL-{number:08d}"
            used.add(row["journal_entry_line_id"])
            number += 1
        row.setdefault("parent_journal_entry_line_id", row["journal_entry_line_id"])


def assign_stable_public_journal_ids(world: dict[str, Any]) -> None:
    """Assign readable journal numbers in posting order."""
    entries = sorted(
        world.get("journal_entry") or [],
        key=lambda row: (str(row.get("posting_date") or ""), row["journal_entry_id"]),
    )
    year = str((world.get("fiscal_calendar") or {}).get("fiscal_year") or "")
    for number, entry in enumerate(entries, 1):
        entry["public_journal_entry_id"] = f"GJ-{year}-{number:06d}"


def infer_account_semantic_role(account: Mapping[str, Any]) -> str:
    explicit = str(account.get("semantic_role") or "").strip().casefold()
    if explicit:
        return explicit.replace(" ", "_").replace("-", "_")
    text = " ".join(
        str(account.get(field) or "").casefold()
        for field in ("name", "account_name", "fsli_mapping")
    )
    for token, role in (
        ("suspense", "suspense"),
        ("unclassified", "unclassified"),
        ("withholding", "payroll_withholding_clearing"),
        ("clearing", "clearing"),
        ("holding", "holding"),
    ):
        if token in text:
            return role
    return "operating"


def apply_account_semantic_roles(accounts: Sequence[dict[str, Any]]) -> None:
    for account in accounts:
        account["semantic_role"] = infer_account_semantic_role(account)


def apply_accounting_user_access_rights(users: Sequence[dict[str, Any]]) -> None:
    for user in users:
        privilege = str(user.get("privilege_level") or "").casefold()
        create, post, approve, automated = _ACCESS_RIGHTS.get(
            privilege, (False, False, False, False)
        )
        user.update(
            can_create_journal_entries=create,
            can_post_journal_entries=post,
            can_approve_journal_entries=approve,
            automated_posting_only=automated,
            access_approver_role=(
                "Chief Financial Officer" if privilege == "approver" else "Controller"
            ),
            segregation_conflict=bool(create and approve),
        )


def classify_journal_risk_flags(
    entry: Mapping[str, Any], fiscal_year_end: str
) -> dict[str, bool]:
    posting_date = str(entry.get("posting_date") or "")
    text = " ".join(
        str(entry.get(field) or "").casefold().replace("_", " ")
        for field in ("journal_type", "journal_source", "description")
    )
    post_close = posting_date > fiscal_year_end or "post close" in text
    top_side = "top side" in text or "topside" in text
    consolidation = "consolidat" in text or "elimination" in text
    estimate = any(token in text for token in _ESTIMATE_TOKENS)
    return {
        "post_close_flag": post_close,
        "top_side_flag": top_side,
        "consolidation_flag": consolidation,
        "estimate_flag": estimate,
        "ordinary_period_end_flag": bool(
            posting_date == fiscal_year_end
            and not post_close
            and not top_side
            and not consolidation
        ),
    }


def apply_journal_risk_flags(
    entries: Sequence[dict[str, Any]], fiscal_year_end: str
) -> None:
    for entry in entries:
        entry.update(classify_journal_risk_flags(entry, fiscal_year_end))


def refresh_journal_risk_contract(world: dict[str, Any]) -> None:
    entries = world.get("journal_entry") or []
    assign_stable_public_journal_ids(world)
    assign_stable_journal_line_ids(world)
    apply_journal_risk_flags(entries, str(world["fiscal_calendar"]["end_date"]))
    apply_account_semantic_roles(world.get("general_ledger_account") or [])
    apply_accounting_user_access_rights(world.get("accounting_user") or [])
