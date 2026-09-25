from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from openpyxl.utils.cell import coordinate_from_string


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        encoding="utf-8",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, indent=2, ensure_ascii=False, default=str)
            stream.write("\n")
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def check_result(
    check: dict,
    sheet: str,
    passed: bool | None,
    message: str,
    *,
    locations: list[dict] | None = None,
    **details: Any,
) -> dict:
    """Serialize one verdict; table diagnostics may add fields but not change its score."""
    if passed is not None and type(passed) is not bool:
        raise ValueError("check verdict must be Boolean or an evaluation error")
    locations = locations or []
    cells = [item["cell"] for item in locations if item["sheet"] == sheet]
    rows = list(dict.fromkeys(coordinate_from_string(cell)[1] for cell in cells))
    result_type = (
        "evaluation_error"
        if passed is None
        else "correct"
        if passed
        else "answer_not_located"
        if details.get("error") == "lookup_failed" or details.get("extraction_errors")
        else "incorrect_or_missing_answer"
    )
    return {
        "id": check["id"],
        "description": check.get("description", check["id"]),
        "kind": check["kind"],
        "sheet": sheet,
        "expected": check.get("expected"),
        "observed": [],
        "message": message,
        "matched_locations": locations,
        "passing_locations": locations if passed else [],
        "matched_cells": cells,
        "passing_cells": cells if passed else [],
        "matched_rows": rows,
        "passing_rows": rows if passed else [],
        **details,
        "passed": passed,
        "score": float(passed) if passed is not None else None,
        "counts_in_score": passed is not None,
        "result_type": result_type,
    }


def score_summary(checks: list[dict]) -> dict:
    total = len(checks)
    passed = sum(check.get("passed") is True for check in checks)
    notes = [check for check in checks if check.get("kind") == "notes_have_context"]
    answers = [check for check in checks if check.get("kind") != "notes_have_context"]
    # All ancillary-content scans share one point across the workpaper.
    possible = len(answers) + bool(notes)
    earned = sum(check.get("passed") is True for check in answers)
    earned += bool(notes) and all(check.get("passed") is True for check in notes)
    valid = all(type(check.get("passed")) is bool for check in checks)
    return {
        "score": (earned / possible if possible else 0.0) if valid else None,
        "passed": bool(total) and passed == total if valid else None,
        "passed_checks": passed,
        "total_checks": total,
        "earned_points": earned if valid else None,
        "possible_points": possible,
    }
