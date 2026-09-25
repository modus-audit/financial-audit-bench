"""Validate target binder files and their accounting ties."""

from __future__ import annotations

from pathlib import Path

from financial_audit_bench.synthetic_binders._6_validate_pbcs.inventory_package_validation import (
    run_inventory_ecc_package_checks,
)
from financial_audit_bench.synthetic_binders._6_validate_pbcs.journal_population import (
    run_journal_export_population_checks,
)
from financial_audit_bench.synthetic_binders._6_validate_pbcs.mapped_artifacts import (
    _validate_mapped_file,
)
from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig
from financial_audit_bench.synthetic_binders.utils import read_json, write_json


def _audit_planning_input_failures(
    run_dir: Path,
    file_map: list[dict],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    planning_dir = run_dir / "planning"
    text_path = planning_dir / "Financial Audit Planning Inputs.txt"
    if not text_path.is_file() or text_path.stat().st_size == 0:
        failures.append({"error": "financial audit planning text is missing or empty"})
    request_ids = {str(entry.get("request_id")) for entry in file_map}
    for request_id in (
        "DOC-AUDIT-PLANNING-INPUTS",
        "DOC-AUDIT-PLANNING-INPUTS-JSON",
    ):
        if request_id not in request_ids:
            failures.append({"error": f"planning file map omits {request_id}"})
    return failures


def run(run_dir: Path, config: SyntheticBinderConfig) -> None:
    file_map_contract = read_json(run_dir / "sampled_world" / "pbc_file_map.json")
    file_map = file_map_contract["files"]
    world = read_json(run_dir / "sampled_world" / "sampled_world.json")
    failures: list[dict[str, str]] = []
    tied_cells = 0

    for entry in file_map:
        path = run_dir / str(entry.get("path") or "")
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(
                {"error": f"missing or empty PBC artifact: {entry.get('path')}"}
            )
            continue
        entry_ties, entry_failures = _validate_mapped_file(run_dir, entry)
        tied_cells += entry_ties
        failures.extend(entry_failures)

    failures.extend(run_inventory_ecc_package_checks(file_map, world))
    failures.extend(_audit_planning_input_failures(run_dir, file_map))
    failures.extend(run_journal_export_population_checks(run_dir, file_map, world))

    report = {
        "status": "passed" if not failures else "failed",
        "files_validated": len(file_map),
        "cells_tied_to_lineage": tied_cells,
        "failures": failures,
    }
    write_json(run_dir / "validation" / "pbc_validation.json", report)
    if failures:
        raise ValueError(f"PBC validation failed: {failures[:3]}")
