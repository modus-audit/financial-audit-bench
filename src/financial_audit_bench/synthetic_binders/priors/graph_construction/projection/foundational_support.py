"""Shared workbook identity, schedule, and lineage helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    append_tie,
)

World = dict[str, Any]


def _export_header(ws, world: World, prior_year: bool = False) -> None:
    """Rewrite the masthead period line with the report's own date wording."""
    calendar = world["fiscal_calendar"]
    end = str(calendar["end_date"])
    if prior_year:
        year = int(calendar["fiscal_year"]) - 1
        end = end.replace(str(calendar["fiscal_year"]), str(year), 1)
    end_date = date.fromisoformat(end)
    ws["A3"] = f"As of {end_date.strftime('%B %d, %Y')}"


def _export_footer(ws, world: World) -> None:
    from datetime import timedelta

    run_date = date.fromisoformat(
        str(world["fiscal_calendar"]["end_date"])
    ) + timedelta(days=14)
    ws.append([])
    ws.append([f"{run_date.strftime('%A, %b %d, %Y')} - Accrual Basis"])


_tie = append_tie


def account_names(world: World) -> dict[str, dict[str, Any]]:
    return {row["gl_account_id"]: row for row in world["general_ledger_account"]}


def _add_schedule_sheet(wb, title: str, world: World):
    ws = wb.create_sheet(title)
    ws.append([world["company_context"]["legal_name"]])
    ws.append([title])
    end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    ws.append([f"As of {end.strftime('%B %d, %Y')}"])
    ws.append([])
    return ws
