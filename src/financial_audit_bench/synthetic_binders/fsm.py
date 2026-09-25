from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders._1_start import state as start_state
from financial_audit_bench.synthetic_binders._2_generate_engagement_profile import (
    state as generate_engagement_profile_state,
)
from financial_audit_bench.synthetic_binders._3_generate_financial_world import (
    state as generate_financial_world_state,
)
from financial_audit_bench.synthetic_binders._4_generate_audit_plan import (
    state as generate_audit_plan_state,
)
from financial_audit_bench.synthetic_binders._5_project_binder import (
    state as projection_state,
)
from financial_audit_bench.synthetic_binders._6_validate_pbcs import (
    state as validate_pbcs_state,
)
from financial_audit_bench.synthetic_binders.models import (
    SyntheticBinderConfig,
)
from financial_audit_bench.synthetic_binders.utils import write_json


def run_synthetic_binder(
    config: SyntheticBinderConfig,
) -> dict[str, Any]:
    """Generate one binder."""
    if not config.run_id:
        config.run_id = f"synthetic-{datetime.now(UTC):%y%m%d%H%M%S}"
    run_dir = Path(config.output_dir)
    state_path = run_dir / "state.json"
    stages = (
        ("START", start_state.run),
        ("GENERATE_ENGAGEMENT_PROFILE", generate_engagement_profile_state.run),
        ("GENERATE_FINANCIAL_WORLD", generate_financial_world_state.run),
        ("GENERATE_AUDIT_PLAN", generate_audit_plan_state.run),
        ("PROJECT_BINDER", projection_state.run),
        ("VALIDATE_PBCS", validate_pbcs_state.run),
    )
    for stage_name, stage in stages:
        try:
            stage(run_dir, config)
        except Exception as error:
            if stage_name == "START":
                raise
            message = f"{stage_name}: {type(error).__name__}: {error}"
            write_json(
                state_path,
                {
                    "run_id": config.run_id,
                    "config": config.to_json(),
                    "current_state": stage_name,
                    "status": "error",
                    "error": message,
                },
            )
            raise

    result = {
        "run_id": config.run_id,
        "config": config.to_json(),
        "current_state": "DONE",
        "status": "package_ready",
        "error": None,
    }
    _write_generation_manifest(run_dir, config)
    _remove_successful_run_working_files(run_dir)
    return result


def _write_generation_manifest(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Write a file inventory for one completed run."""
    from financial_audit_bench.synthetic_binders.utils import read_json

    pbc_validation_path = run_dir / "validation" / "pbc_validation.json"
    pbc_validation = (
        read_json(pbc_validation_path) if pbc_validation_path.exists() else {}
    )
    auditor_roots = ("pbc_package", "direct_to_auditor", "planning")
    allowed_suffixes = {".csv", ".docx", ".txt", ".xls", ".xlsm", ".xlsx"}
    artifacts = [
        path
        for root_name in auditor_roots
        for path in sorted((run_dir / root_name).rglob("*"))
        if path.is_file() and path.suffix.casefold() in allowed_suffixes
    ]
    artifact_rows = [
        {
            "path": path.relative_to(run_dir).as_posix(),
            "bytes": path.stat().st_size,
        }
        for path in artifacts
    ]
    payload = {
        "run_id": config.run_id,
        "business_type": config.business_type,
        "seed": config.seed,
        "validation_status": pbc_validation.get("status"),
        "artifacts": artifact_rows,
    }
    write_json(run_dir / "generation_manifest.json", payload)


def _remove_successful_run_working_files(run_dir: Path) -> None:
    """Leave only the generated human-auditor package and its inventory."""
    for directory in ("sampled_world", "validation"):
        shutil.rmtree(run_dir / directory, ignore_errors=True)
    for file_path in (run_dir / "planning" / "Financial Audit Planning Inputs.json",):
        if file_path.exists():
            file_path.unlink()
