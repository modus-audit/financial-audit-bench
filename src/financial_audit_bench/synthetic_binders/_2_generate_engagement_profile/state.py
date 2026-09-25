"""Generate the engagement profile used by later FSM states."""

from __future__ import annotations

from pathlib import Path

from financial_audit_bench.synthetic_binders.audit_planning.calendar import (
    build_engagement_calendar,
)
from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig
from financial_audit_bench.synthetic_binders.utils import write_json


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Write the seeded engagement profile."""
    profile = {
        "business_type": config.business_type,
        "seed": config.seed,
        "fiscal_year": 2025,
        "engagement_calendar": build_engagement_calendar(2025),
    }
    write_json(run_dir / "sampled_world" / "engagement_profile.json", profile)
