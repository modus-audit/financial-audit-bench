"""Shared workbook finalization for industry operating records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    save_workbook,
)


def _save(wb, path: Path, request_id: str, family: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_workbook(wb, path)
    return {
        "request_id": request_id,
        "family": family,
        "path": str(path),
        "cell_map": [],
    }
