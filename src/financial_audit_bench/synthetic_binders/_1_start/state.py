"""Initialize a clean synthetic-binder workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    """Prepare the output directory and required working directories."""
    del config
    if run_dir.is_symlink():
        raise ValueError(f"output directory cannot be a symbolic link: {run_dir}")
    if run_dir.exists() and not run_dir.is_dir():
        raise ValueError(f"output path is not a directory: {run_dir}")
    if run_dir.exists() and any(run_dir.iterdir()):
        binder_markers = (
            run_dir / "state.json",
            run_dir / "generation_manifest.json",
        )
        if not any(marker.is_file() for marker in binder_markers):
            raise ValueError(
                "refusing to overwrite nonempty directory that is not a "
                f"generated binder: {run_dir}"
            )
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    for folder_name in ("sampled_world", "pbc_package", "validation"):
        (run_dir / folder_name).mkdir()
