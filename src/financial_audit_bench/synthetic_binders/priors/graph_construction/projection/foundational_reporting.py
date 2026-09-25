"""Foundational trial-balance, chart, and financial-statement rows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _tie,
    account_names,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    write_header,
)

World = dict[str, Any]


def journal_number(world: World, journal_entry_id: str) -> str:
    """The client-visible journal number for a generation-lineage id."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
        package_identity,
    )

    return package_identity(world).journal_numbers.get(
        journal_entry_id, journal_entry_id
    )


def _trial_balance_rows(
    ws, world: World, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Render the retained Account | Name | Debit | Credit layout."""
    accounts = account_names(world)
    write_header(ws, ["Account", "Name", "Debit", "Credit"])

    debits = credits = Decimal("0.00")
    for row in rows:
        balance = Decimal(row["closing_balance"])
        debit = balance if balance > 0 else Decimal("0.00")
        credit = -balance if balance < 0 else Decimal("0.00")
        account = accounts[row["gl_account_id"]]
        ws.append([row["gl_account_id"], account["name"], str(debit), str(credit)])
        debits += debit
        credits += credit
    ws.append([])
    return [
        _tie(ws, "Total debits", debits),
        _tie(ws, "Total credits", credits),
    ]


def _prior_year_trial_balance(ws, world: World) -> list[dict[str, Any]]:
    return _trial_balance_rows(ws, world, world["prior_period_account_balance"])


def _current_year_trial_balance(ws, world: World) -> list[dict[str, Any]]:
    # Client exports exclude audit adjustments.
    return _trial_balance_rows(ws, world, world["trial_balance_account"])


def _chart_of_accounts(ws, world: World) -> list[dict[str, Any]]:
    # Exclude auditor-only normal-balance and FSLI fields.
    write_header(ws, ["Account", "Name", "Status", "Type"])
    for row in world["general_ledger_account"]:
        ws.append(
            [
                row["gl_account_id"],
                row["name"],
                row["lifecycle_status"].capitalize(),
                row["account_class"].replace("_", " ").capitalize(),
            ]
        )
    ws.append([])
    # The chart ends with its last account row.
    return []
