"""Complete posted journal-line population for the auditor."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.controls import (
    journal_approvals,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_reporting import (
    journal_number,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    append_tie,
    open_pbc_sheet,
    save_workbook,
    write_header,
)


def _journal_source(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "").lower().replace("_", " ")
        for key in ("journal_source", "journal_type", "journal_entry_id")
    )
    routes = (
        (("sale", "revenue", "billing"), "Sales"),
        (("receivable", "collection"), "Accounts Receivable"),
        (("vendor", "payable", "invoice"), "Accounts Payable"),
        (("cash receipt", "deposit"), "Cash Receipts"),
        (("cash disbursement", "payment", "check"), "Cash Disbursements"),
        (("payroll", "benefit", "withholding"), "Payroll"),
        (("inventory", "cogs", "production"), "Inventory"),
        (("fixed asset", "depreciation"), "Fixed Assets"),
    )
    return next(
        (label for tokens, label in routes if any(t in text for t in tokens)), "General"
    )


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    entries = sorted(
        world.get("journal_entry") or [],
        key=lambda row: (str(row["posting_date"]), row["journal_entry_id"]),
    )
    if not entries:
        return []
    accounts = {
        row["gl_account_id"]: f"{row['account_number']} {row['name']}"
        for row in world["general_ledger_account"]
    }
    users = {
        row["accounting_user_id"]: row["user_name"]
        for row in world.get("accounting_user") or []
    }
    approvals = {
        row["journal_entry_id"]: row
        for row in journal_approvals(entries, world.get("accounting_user") or [])
    }
    lines_by_entry: dict[str, list[dict[str, Any]]] = {}
    for line in world.get("journal_entry_line") or []:
        lines_by_entry.setdefault(line["journal_entry_id"], []).append(line)

    wb, ws = open_pbc_sheet(
        "Complete Journal Entry Population", "DOC-JOURNAL-EXPORT", world
    )
    write_header(
        ws,
        [
            "Entry number",
            "Line",
            "Line ID",
            "Parent line ID",
            "Posting date",
            "Source",
            "Document number",
            "Memo",
            "Account",
            "Debit",
            "Credit",
            "Preparer",
            "Approver",
            "Status",
        ],
    )
    debits = Decimal("0")
    credits = Decimal("0")
    entry_count = 0
    line_count = 0
    for journal in entries:
        lines = lines_by_entry.get(journal["journal_entry_id"], [])
        if not lines:
            continue
        entry_count += 1
        approval = approvals.get(journal["journal_entry_id"], {})
        for line in sorted(lines, key=lambda row: int(row["line_number"])):
            debit = Decimal(str(line["debit_amount"]))
            credit = Decimal(str(line["credit_amount"]))
            line_count += 1
            debits += debit
            credits += credit
            ws.append(
                [
                    journal_number(world, journal["journal_entry_id"]),
                    line["line_number"],
                    line["journal_entry_line_id"],
                    line.get("parent_journal_entry_line_id")
                    or line["journal_entry_line_id"],
                    fmt_date(journal["posting_date"]),
                    _journal_source(journal),
                    journal.get("document_number") or "",
                    line.get("description") or journal.get("description") or "",
                    accounts[line["gl_account_id"]],
                    fmt_amount(debit) if debit else "",
                    fmt_amount(credit) if credit else "",
                    users.get(
                        approval.get("preparer_user_id") or journal.get("user_id"),
                        "System",
                    ),
                    users.get(approval.get("approver_user_id"), ""),
                    journal.get("posting_status") or "",
                ]
            )
    ws.append([])
    ties = [
        append_tie(ws, "Entry count", entry_count),
        append_tie(ws, "Line count", line_count),
        append_tie(ws, "Total debits", fmt_amount(debits)),
        append_tie(ws, "Total credits", fmt_amount(credits)),
    ]
    path = output_dir / "Complete Journal Entry Population.xlsx"
    save_workbook(wb, path)
    return [
        {
            "request_id": "DOC-JOURNAL-EXPORT",
            "family": "journal_entries",
            "path": str(path),
            "cell_map": ties,
        }
    ]
