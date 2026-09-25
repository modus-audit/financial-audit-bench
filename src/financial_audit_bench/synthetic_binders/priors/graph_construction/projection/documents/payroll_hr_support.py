"""HR authorization register supporting payroll master-file changes."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
    titled_doc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)


def _applicable(world: World) -> bool:
    return bool(world.get("employee"))


def _rate(value: Any, basis: str) -> str:
    if value in (None, ""):
        return "-"
    return f"{fmt_amount(value)} {'/hr' if 'hourly' in basis else '/yr'}"


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    events = sorted(
        world.get("payroll_authorization_event") or [],
        key=lambda row: (
            str(row["effective_date"]),
            str(row["employee_id"]),
            str(row["event_type"]),
        ),
    )
    if not events:
        return []
    public_ids = client_id_map(world)
    year = int(world["fiscal_calendar"]["fiscal_year"])
    report_date = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    doc = titled_doc("HR COMPENSATION AND PERSONNEL AUTHORIZATION REGISTER", world)
    doc.center(f"Status through {fmt_date(report_date.isoformat())}")
    doc.line()
    doc.table(
        [
            "Authorization",
            "Employee",
            "Event",
            "Effective",
            "Prior rate",
            "New rate",
            "Approved by",
            "Entered by",
            "Reviewed by",
        ],
        [
            [
                public_ids.get(
                    row["authorization_reference"], row["authorization_reference"]
                ),
                f"{public_ids.get(row['employee_id'], row['employee_id'])} {row['employee_name']}",
                str(row["event_type"]).replace("_", " ").title(),
                fmt_date(row["effective_date"]),
                _rate(row.get("prior_authorized_rate"), str(row["pay_basis"])),
                _rate(row.get("new_authorized_rate"), str(row["pay_basis"])),
                row["approved_by"],
                row["entered_by"],
                row["reviewed_by"],
            ]
            for row in events
        ],
        [20, 27, 18, 12, 14, 14, 20, 20, 20],
    )
    doc.line()
    doc.tie("authorization_event_count", "Authorization events", len(events))
    doc.wrapped(
        "HR authorization references support PAY-02 compensation and effective-date "
        "fields; approved changes are entered and independently reviewed in payroll."
    )

    path = output_dir / "payroll" / f"HR Authorization Register {year}.txt"
    return [
        entry(
            "PAY-03",
            "payroll",
            path,
            doc.write(path),
        )
    ]
