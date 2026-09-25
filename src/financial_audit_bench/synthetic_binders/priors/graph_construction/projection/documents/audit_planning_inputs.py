"""Central financial-audit planning input register for every binder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.planning_inputs import (
    public_planning_input_rows,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)


def _calendar_rows(value: Any) -> list[list[str]]:
    """Turn the centralized calendar sentence into reviewable milestones."""
    rows: list[list[str]] = []
    for part in str(value).split(";"):
        match = re.match(r"\s*(.*?)\s+(\d{4}-\d{2}-\d{2})\s*$", part)
        if not match:
            continue
        rows.append(
            [match.group(1).replace("_", " ").strip().title(), fmt_date(match.group(2))]
        )
    return rows


def render(
    world: World, output_dir: Path, plan: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = public_planning_input_rows(world, plan)
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = TextDoc()
    doc.center("FINANCIAL AUDIT PLANNING INPUTS")
    doc.center(company_name(world))
    doc.rule("=")
    doc.pair("Fiscal year end", fmt_date(world["fiscal_calendar"]["end_date"]))
    business = str(world.get("business_type") or "Not specified")
    doc.pair("Business profile", business.replace("_", " ").title())
    doc.line()
    doc.wrapped(
        "This centralized register states available engagement facts, standing "
        "assumptions, and planning decisions still required. A missing planning "
        "input must not be inferred from the supporting evidence."
    )
    current_category = ""
    for item in rows:
        if item["category"] != current_category:
            current_category = item["category"]
            doc.line()
            doc.line(current_category.upper())
            doc.rule("-")
            doc.line()
        doc.line(f"Planning input {item['planning_input_number']} | {item['input']}")
        calendar_rows = _calendar_rows(item["value"])
        if len(calendar_rows) < 3:
            calendar_rows = []
        if calendar_rows:
            doc.table(["Milestone", "Date"], calendar_rows, [50, 16])
        else:
            doc.wrapped(f"Value / assumption: {item['value']}")
        doc.pair("Owner", item["owner"].title())
        doc.pair("Applicability", item["applicability"].replace("_", " ").title())
        doc.pair("Availability", item["availability"].title())
        doc.pair("Application", item["application"].title())
    text_path = output_dir / "Financial Audit Planning Inputs.txt"
    json_path = output_dir / "Financial Audit Planning Inputs.json"
    text_ties = doc.write(text_path)
    json_path.write_text(json.dumps({"planning_inputs": rows}, indent=2) + "\n")
    return [
        entry("DOC-AUDIT-PLANNING-INPUTS", "audit_planning", text_path, text_ties),
        entry("DOC-AUDIT-PLANNING-INPUTS-JSON", "audit_planning", json_path, []),
    ]
