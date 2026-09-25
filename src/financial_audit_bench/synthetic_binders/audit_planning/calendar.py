"""Engagement dates used by the retained human-auditor package."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)

_POLICY = load_authored_policy("authored.global.operating-policy.v1").values[
    "engagement_calendar"
]


def _business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def build_engagement_calendar(fiscal_year: int) -> dict[str, Any]:
    """Build the target year-end engagement dates."""
    scenario = "standard_year_end"
    offsets = _POLICY["scenario_offsets"][scenario]
    fixed = _POLICY["milestone_day_offsets"]
    year_end = date(fiscal_year, 12, 31)

    def offset(name: str) -> date:
        return year_end + timedelta(days=fixed[name])

    milestones = {
        "financial_statement_date": year_end.isoformat(),
        "inventory_observation_date": year_end.isoformat(),
        "confirmation_send_date": _business_day(
            offset("confirmation_send_date")
        ).isoformat(),
        "confirmation_response_cutoff": _business_day(
            year_end + timedelta(days=offsets["confirmation_response"])
        ).isoformat(),
        "bank_reconciliation_prepared_date": _business_day(
            offset("bank_reconciliation_prepared_date")
        ).isoformat(),
        "bank_reconciliation_reviewed_date": _business_day(
            offset("bank_reconciliation_reviewed_date")
        ).isoformat(),
        "cutoff_bank_statement_start": offset(
            "cutoff_bank_statement_start"
        ).isoformat(),
        "cutoff_bank_statement_end": offset("cutoff_bank_statement_end").isoformat(),
        "report_date": _business_day(
            year_end + timedelta(days=offsets["report"])
        ).isoformat(),
    }
    windows = [_window(*spec, milestones) for spec in _POLICY["windows"]]
    calendar = {
        "milestones": milestones,
        "procedure_windows": windows,
    }
    errors = validate_engagement_calendar(calendar)
    if errors:
        raise ValueError("; ".join(errors))
    return calendar


def _window(
    window_id: str,
    family: str,
    start_milestone: str,
    end_milestone: str,
    date_field: str,
    milestones: dict[str, str],
) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "procedure_family": family,
        "start_date": milestones[start_milestone],
        "end_date": milestones[end_milestone],
        "population_date_field": date_field,
    }


def get_window(calendar: dict[str, Any], window_id: str) -> dict[str, Any]:
    for row in calendar.get("procedure_windows") or []:
        if row.get("window_id") == window_id:
            return row
    raise ValueError(f"engagement calendar is missing procedure window {window_id}")


def validate_engagement_calendar(calendar: dict[str, Any]) -> list[str]:
    """Return structural and chronology errors for the compact calendar."""
    errors: list[str] = []
    milestones = calendar.get("milestones") or {}
    missing = set(_POLICY["required_milestones"]) - set(milestones)
    if missing:
        return [f"engagement calendar missing milestones: {sorted(missing)}"]
    try:
        parsed = {
            key: date.fromisoformat(str(value)) for key, value in milestones.items()
        }
    except (TypeError, ValueError):
        return ["engagement calendar contains a non-ISO date"]
    for left, right in _POLICY["chronology_constraints"]:
        if parsed[left] > parsed[right]:
            errors.append(f"calendar chronology requires {left} <= {right}")
    for row in calendar.get("procedure_windows") or []:
        try:
            if date.fromisoformat(row["start_date"]) > date.fromisoformat(
                row["end_date"]
            ):
                errors.append(
                    f"procedure window {row['window_id']} starts after it ends"
                )
        except (KeyError, TypeError, ValueError):
            errors.append("procedure window has invalid boundaries")
    return errors
