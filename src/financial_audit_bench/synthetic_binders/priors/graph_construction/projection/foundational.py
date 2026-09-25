"""Render the foundational PBC requests from the generated world."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    filename,
    save_workbook,
    open_pbc_sheet,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _export_footer as _export_footer,
    _export_header as _export_header,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_revenue import (
    render_manufacturing_revenue_detail as render_manufacturing_revenue_detail,
    render_manufacturing_revenue_policy as render_manufacturing_revenue_policy,
    render_manufacturing_revenue_reconciliation as render_manufacturing_revenue_reconciliation,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_receivables import (
    render_allowance_workbook as render_allowance_workbook,
    render_combined_ar_aging as render_combined_ar_aging,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_payables import (
    render_combined_ap_aging as render_combined_ap_aging,
    render_subsequent_disbursements as render_subsequent_disbursements,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_rni import (
    render_received_not_invoiced as render_received_not_invoiced,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_inventory_cutoff import (
    render_inventory_cutoff_population as render_inventory_cutoff_population,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_accruals import (
    render_accrual_rollforward as render_accrual_rollforward,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_reporting import (
    _chart_of_accounts,
    _current_year_trial_balance,
    _prior_year_trial_balance,
)

World = dict[str, Any]

FAMILY = "foundational"


def render_foundational_pbcs(
    registry: dict[str, Any], world: World, output_dir: Path
) -> list[dict[str, Any]]:
    """Write one workbook per foundational request; return the file map."""
    renderers = {
        # FOUNDATIONAL-01 deliberately absent: see the module docstring. FOUNDATIONAL-06
        # (AJEs/close checklist), -07 (draft FS), and -08 (materiality) are auditor
        # workpapers and never render.
        "FOUNDATIONAL-02": _prior_year_trial_balance,
        "FOUNDATIONAL-03": _current_year_trial_balance,
        "FOUNDATIONAL-05": _chart_of_accounts,
    }
    file_map = []
    for request_id, request in registry["foundational_requests"].items():
        renderer = renderers.get(request_id)
        if renderer is None:
            continue
        name = filename(request_id, request["request"])
        path = output_dir / FAMILY / f"{name}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        wb, ws = open_pbc_sheet(name, request_id, world)
        _export_header(ws, world, prior_year=request_id == "FOUNDATIONAL-02")
        cell_map = renderer(ws, world)
        _export_footer(ws, world)
        save_workbook(wb, path)
        file_map.append(
            {
                "request_id": request_id,
                "family": FAMILY,
                "path": str(path),
                "cell_map": cell_map,
            }
        )
    return file_map
