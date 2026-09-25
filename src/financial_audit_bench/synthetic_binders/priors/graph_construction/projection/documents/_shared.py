"""Shared vocabulary for the per-type PBC document modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)

World = dict[str, Any]

DIRECTORY = "documents"


def titled_doc(title: str, world: World) -> TextDoc:
    """A document opened with the standard client masthead: centered title,
    company name, and a rule -- the header 12 of the document modules share."""
    doc = TextDoc()
    # One client accounting system = one print look across its exports.
    doc.center(title)
    doc.center(company_name(world))
    doc.rule("=")
    return doc


def entry(
    doc_id: str,
    family: str,
    path: Path,
    ties: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "request_id": doc_id,
        "family": family,
        "path": str(path),
        "line_map": ties,
    }


def company_name(world: World) -> str:
    return world["company_context"]["legal_name"]


def vendor_names(world: World) -> dict[str, str]:
    return {row["vendor_id"]: row["name"] for row in world["vendor"]}


def invoices_by_id(world: World) -> dict[str, dict[str, Any]]:
    return {row["vendor_invoice_id"]: row for row in world["vendor_invoice"]}
