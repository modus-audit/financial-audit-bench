"""Bank-confirmation request, response, scope, and intake rendering."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.bank_reconciliation_controls import (
    require_year_end_bank_statement,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.confirmation_support import (
    _BANK_OFFICER_TITLES,
    _active_signer,
    _confirmation_cycle,
    _control_id,
    _doc_rng,
    _person,
    _return_instructions,
    _signature_style,
    _tie_table_rows,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    AUDIT_FIRM,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)


def _confirmation_scope(world: World, institution_name: str) -> list[dict[str, Any]]:
    deposit_ids = sorted(
        str(row["bank_account_id"])
        for node in ("bank_account", "payroll_bank_account")
        for row in ([world[node]] if node == "bank_account" else world.get(node) or [])
        if row.get("bank_name") == institution_name
    )
    sections = (
        ("deposit_accounts", deposit_ids),
        ("direct_liabilities", []),
        ("contingent_liabilities", []),
        ("collateral_security_compensating_balances", []),
    )
    return [
        {
            "section_number": index,
            "scope_id": scope_id,
            "source_population_ids": ids,
            "source_population_count": len(ids),
            "reported_item_ids": ids,
            "reported_item_count": len(ids),
            "response_state": "completed_positive" if ids else "completed_negative",
        }
        for index, (scope_id, ids) in enumerate(sections, 1)
    ]


def _render_bank_confirmation(
    world: World, output_dir: Path, audit_plan: dict[str, Any]
) -> dict[str, Any]:
    operating = world["bank_account"]
    identity = package_identity(world)
    statements = sorted(
        (
            row
            for row in world["bank_statement_month"]
            if row["bank_account_id"] == operating["bank_account_id"]
        ),
        key=lambda row: row["statement_period_end"],
    )
    as_of, requested, responded, received = _confirmation_cycle(
        world, f"bank|{operating['bank_name']}", audit_plan
    )
    year_end_statement = require_year_end_bank_statement(
        statements,
        bank_account_id=operating["bank_account_id"],
        statement_period_end=str(as_of),
    )
    confirmation_scope = _confirmation_scope(world, operating["bank_name"])
    rng = _doc_rng(world, f"bank.officer|{operating['bank_name']}")
    officer = _person(rng)
    officer_title = rng.choice(_BANK_OFFICER_TITLES)
    authorization_date = requested - timedelta(days=1)
    while authorization_date.weekday() >= 5:
        authorization_date -= timedelta(days=1)

    doc = TextDoc()
    control_id = _control_id("BNK", operating["bank_name"])
    doc.center(operating["bank_name"])
    doc.center(identity.bank["address"])
    doc.center(identity.bank["phone"])
    doc.center("STANDARD FORM TO CONFIRM ACCOUNT BALANCE INFORMATION")
    doc.rule("=")
    doc.line(f"FORM DCF-100 / REV. 09/25 / CONTROL {control_id}")
    doc.line()
    doc.pair("Customer", company_name(world))
    doc.pair("Information as of", fmt_date(as_of))
    doc.line()

    signer = _active_signer(world)
    doc.line("CUSTOMER AUTHORIZATION")
    doc.wrapped(
        "We authorize the financial institution named above to disclose the "
        "information requested below to our independent auditors."
    )
    if signer:
        doc.signature(
            signer["signer_name"],
            style=_signature_style(world, signer["signer_name"]),
        )
        doc.line(f"  {signer['signer_name']}, {signer['signer_title']}")
    doc.line(f"  Authorization date: {fmt_date(authorization_date.isoformat())}")
    _return_instructions(doc)
    doc.pair("Auditor send date", fmt_date(requested.isoformat()))
    doc.pair("Auditor control ID", control_id)
    doc.rule()

    doc.wrapped(
        "At the request of our customer and their independent auditors, we "
        "confirm the following deposit balances held by this institution "
        "as of the close of business on the date noted above."
    )
    doc.line()

    doc.line("1. DEPOSIT ACCOUNTS")
    account_rows = [
        [
            operating["account_name"],
            operating["masked_account_number"],
            "0.00%",
            fmt_amount(year_end_statement["ending_balance"]),
        ]
    ]
    account_ties = [("operating_balance", account_rows[0][3])]
    # Include every deposit account, including zero-balance clearing accounts.
    for account in world.get("payroll_bank_account") or []:
        if account.get("bank_name") != operating["bank_name"]:
            continue
        account_rows.append(
            [
                account["account_name"],
                account["masked_account_number"],
                "0.00%",
                fmt_amount(account["closing_balance"]),
            ]
        )
        account_ties.append(("payroll_account_balance", account_rows[-1][3]))
    doc.table(
        ["Account", "Account number", "Rate", "Balance"],
        account_rows,
        [24, 15, 7, 14],
    )
    _tie_table_rows(doc, account_ties)
    doc.line()

    signers = [
        row
        for row in world.get("bank_account_signer") or []
        if row["authorization_status"] == "active"
    ]
    if signers:
        doc.line("AUTHORIZED SIGNERS OF RECORD")
        doc.table(
            ["Name", "Title"],
            [[row["signer_name"], row["signer_title"]] for row in signers],
            [30, 30],
        )
        doc.line()
    doc.rule()

    doc.line("2. LOANS, LINES OF CREDIT, AND OTHER DIRECT LIABILITIES")
    doc.line("  None reported by this institution.")
    doc.line()

    doc.line("3. CONTINGENT LIABILITIES, GUARANTEES, AND LETTERS OF CREDIT")
    doc.line("  None reported by this institution.")
    doc.line()

    doc.line("4. COLLATERAL, SECURITY INTERESTS, AND COMPENSATING BALANCES")
    doc.line("  None reported by this institution.")
    doc.line()
    doc.line("FORM COMPLETION CONTROL")
    doc.wrapped(
        "; ".join(
            f"{row['section_number']} {str(row['scope_id']).replace('_', ' ')}: "
            f"{row['reported_item_count']}/{row['source_population_count']} "
            f"{str(row['response_state']).replace('_', ' ')}"
            for row in confirmation_scope
        )
    )
    doc.line(
        "All four sections were completed from bank records; exceptions, if "
        "any, are noted above."
    )
    contact = f"{identity.bank['phone']} ext. {rng.randint(200, 899)}"
    doc.signature(
        officer,
        style=_signature_style(world, officer),
        mode="typed",
    )
    doc.line(
        f"  {officer}, {officer_title} | {contact} | "
        f"completed {fmt_date(responded.isoformat())}"
    )
    doc.stamp(["RECEIVED", fmt_date(received.isoformat()), AUDIT_FIRM.upper()])

    year_end = fmt_date(as_of).replace("/", ".")
    path = output_dir / f"Bank Confirmation {year_end}.txt"
    return entry(
        "DOC-BANK-CONFIRMATION",
        "cash",
        path,
        doc.write(path),
    )
