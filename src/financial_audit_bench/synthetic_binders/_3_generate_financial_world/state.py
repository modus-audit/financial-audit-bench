"""Generate the financial data used to build a synthetic binder."""

from __future__ import annotations

from pathlib import Path

from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig
from financial_audit_bench.synthetic_binders.utils import read_json


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Create and persist the private financial world."""
    from financial_audit_bench.synthetic_binders.priors.graph.construct import (
        construct_binder_world,
    )

    engagement_profile = read_json(
        run_dir / "sampled_world" / "engagement_profile.json"
    )
    summary = construct_binder_world(
        seed=config.seed,
        output_dir=run_dir / "sampled_world",
        business_type=config.business_type,
        engagement_profile=engagement_profile,
    )
    if summary["checks"]["status"] != "passed":
        raise ValueError("financial-world identity checks failed")
    world = read_json(run_dir / "sampled_world" / "sampled_world.json")
    company = world.get("company_context") or {}
    if company.get("currency_code") != "USD":
        raise ValueError("synthetic-binder generation requires a USD financial world")
    calendar = world.get("fiscal_calendar") or {}
    if int(str(calendar.get("end_date"))[:4]) != engagement_profile["fiscal_year"]:
        raise ValueError(
            "financial-world fiscal year does not match engagement profile"
        )
    # Do not release a world that falls below its minimum cash requirement.
    if not summary["feasibility"]["cash_feasible"]:
        raise ValueError(
            f"cash-infeasible world after {summary['attempts']} attempts "
            f"(minimum daily balance "
            f"{summary['feasibility']['minimum_daily_balance']}; required "
            f"{summary['feasibility']['minimum_required_balance']})"
        )
