"""Auditor-received confirmations: bank and receivable balances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.confirmation_bank import (
    _render_bank_confirmation as _render_bank_confirmation,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.confirmation_receivables import (
    _render_ar_confirmations as _render_ar_confirmations,
)


def render_confirmations(
    world: World,
    output_dir: Path,
    audit_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Render the direct-to-auditor confirmations; entries + applicability."""
    entries = [_render_bank_confirmation(world, output_dir, audit_plan)]
    applicability = {"bank_confirmation": "generated"}
    if world.get("accounts_receivable_aging"):
        ar_entries = _render_ar_confirmations(world, output_dir, audit_plan)
        applicability["ar_confirmation"] = (
            "generated" if ar_entries else "applicable_but_empty"
        )
        entries.extend(ar_entries)
    else:
        applicability["ar_confirmation"] = "not_applicable"
    return entries, applicability
