"""Project the generated PBCs and one compact auditor-planning packet."""

from __future__ import annotations

from pathlib import Path

from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection import (
    package_binder,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.audit_planning_inputs import (
    render,
)
from financial_audit_bench.synthetic_binders.utils import read_json, write_json


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Render client PBCs, direct evidence, and consolidated planning inputs."""
    del config
    world = read_json(run_dir / "sampled_world" / "sampled_world.json")
    plan = read_json(run_dir / "sampled_world" / "audit_plan.json")

    package_binder(
        world,
        run_dir,
        audit_plan=plan,
    )

    planning_dir = run_dir / "planning"
    entries = render(world, planning_dir, plan)
    for entry in entries:
        entry["path"] = str(Path(entry["path"]).relative_to(run_dir))

    file_map_path = run_dir / "sampled_world" / "pbc_file_map.json"
    file_map = read_json(file_map_path)
    file_map["files"].extend(entries)
    write_json(file_map_path, file_map)

    applicability_path = run_dir / "sampled_world" / "document_applicability.json"
    applicability = read_json(applicability_path)
    applicability["audit_planning_inputs"] = "generated"
    write_json(applicability_path, applicability)
