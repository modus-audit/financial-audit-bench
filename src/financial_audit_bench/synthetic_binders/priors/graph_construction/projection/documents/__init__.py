"""Document renderers retained for manufacturing and staffing binders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    DIRECTORY,
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents import (
    cash_account_package,
    industry_staffing,
    industry_steel,
    inventory_procurement_documents,
    journal_entries_export,
    monthly_bank_statement,
    payroll_accrual_schedule,
    payroll_bank_statement,
    payroll_hr_support,
    payroll_register,
    payroll_settlement_support,
    payroll_subsequent_support,
    staffing_agreement,
    subsequent_period_statement,
    vendor_invoice_document,
)

DOCUMENTS = tuple(
    (doc_kind, module, getattr(module, "_applicable", None))
    for doc_kind, module in (
        ("monthly_bank_statement", monthly_bank_statement),
        ("cash_account_package", cash_account_package),
        ("vendor_invoice_document", vendor_invoice_document),
        ("subsequent_period_statement", subsequent_period_statement),
        ("journal_entries_export", journal_entries_export),
        ("staffing_service_agreement", staffing_agreement),
        ("payroll_register", payroll_register),
        ("payroll_hr_support", payroll_hr_support),
        ("payroll_settlement_support", payroll_settlement_support),
        ("payroll_subsequent_support", payroll_subsequent_support),
        ("payroll_accrual_schedule", payroll_accrual_schedule),
        ("payroll_bank_statement", payroll_bank_statement),
        ("inventory_procurement_documents", inventory_procurement_documents),
        ("industry_operating_records", industry_steel),
        ("industry_operating_records", industry_staffing),
    )
)


def document_is_applicable(doc_kind: str, world: World) -> bool:
    """Apply the first renderer registered for a catalog document kind."""
    for registered_kind, _, applicable in DOCUMENTS:
        if registered_kind == doc_kind:
            return applicable is None or applicable(world)
    return False


def render_documents(
    world: World,
    output_dir: Path,
    audit_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    entries: list[dict[str, Any]] = []
    applicability: dict[str, str] = {}
    for doc_kind, module, applicable in DOCUMENTS:
        if applicable is not None and not applicable(world):
            applicability[doc_kind] = "not_applicable"
            continue
        if doc_kind == "subsequent_period_statement":
            rendered = module.render(
                world, output_dir / DIRECTORY, audit_plan=audit_plan
            )
        else:
            rendered = module.render(world, output_dir / DIRECTORY)
        applicability[doc_kind] = "generated" if rendered else "applicable_but_empty"
        entries.extend(rendered)
    return entries, applicability
