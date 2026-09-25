"""Generate the audit plan consumed during binder projection."""

from __future__ import annotations

from pathlib import Path

from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.planning_inputs import (
    build_audit_plan,
)
from financial_audit_bench.synthetic_binders.utils import read_json, write_json


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Build and persist the audit plan."""
    del config
    world = read_json(run_dir / "sampled_world" / "sampled_world.json")
    profile = read_json(run_dir / "sampled_world" / "engagement_profile.json")
    plan = build_audit_plan(world, profile)
    write_json(run_dir / "sampled_world" / "audit_plan.json", plan)
