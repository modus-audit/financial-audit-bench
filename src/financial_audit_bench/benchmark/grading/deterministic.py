"""Evaluate scalar answers, selection coverage, and ancillary-content checks."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .comparison import (
    equivalent,
    normalize_text,
    parse_number,
    qualified_date,
    yes_no,
)
from .lookup import (
    AmbiguousLookupError,
    LookupResult,
    LookupStatus,
    MergedIdentitySheet,
    MissingColumnError,
    resolve_answer,
    table_record_rows,
    unique_header_column,
)
from .results import (
    check_result,
)
from .rubric import (
    Check,
    compile_check,
    lookup_details,
)
from .template_contract import (
    missing_answer_context,
    scan_columns,
)
from .workbook import (
    WorkbookEvidence,
    answer_cell,
    has_submitted_input,
    submitted_comment_text,
)


@dataclass(frozen=True)
class CheckEvaluation:
    report: dict
    lookup: LookupResult | None = None


def populated_answer(sheet: Any, cell: Any, evidence: WorkbookEvidence | None) -> bool:
    formulas = evidence.formulas if evidence else None
    supplied = evidence.template if evidence else None
    source = formulas[sheet.title][cell.coordinate] if formulas is not None else cell
    if source.data_type == "f" and (
        supplied is None or not has_submitted_input(source, formulas, supplied)
    ):
        return False
    return cell.value is not None and bool(str(cell.value).strip())


def summary_row(
    check: dict,
    sheet: Any,
    header: int,
    row: int,
    evidence: WorkbookEvidence | None = None,
) -> bool:
    summary = check.get("summary_rows")
    if not summary:
        return False
    label = normalize_text(
        sheet.cell(row, unique_header_column(sheet, header, summary["column"])).value
    )
    declared_role = label in {
        normalize_text(value) for value in summary.get("labels", [])
    } or any(
        label.startswith(normalize_text(prefix))
        for prefix in summary.get("label_prefixes", [])
    )
    if not declared_role:
        return False
    for name in summary["identity_columns"]:
        cell = sheet.cell(row, unique_header_column(sheet, header, name))
        kind = summary.get("identity_types", {}).get(name)
        if (
            documented_field(sheet, cell, kind, evidence)
            if kind
            else populated_answer(sheet, cell, evidence)
        ):
            return False
    return True


def documented_field(
    sheet: Any, cell: Any, kind: str, evidence: WorkbookEvidence | None
) -> bool:
    if not populated_answer(sheet, cell, evidence):
        return False
    value = cell.value
    if re.fullmatch(r"(?:y|yes)\s*[/|\-–—]\s*(?:n|no)", normalize_text(value)):
        return False
    if kind == "numeric":
        return parse_number(value) is not None
    if kind == "date":
        return qualified_date(value) is not None
    if kind == "yn":
        return (
            normalize_text(value) not in {"y/n", "yes/no"} and yes_no(value) is not None
        )
    return normalize_text(value) not in {
        "n/a",
        "na",
        "not applicable",
        "y/n",
        "yes/no",
        "pending",
        "not tested",
        "missing",
        "not provided",
        "tbd",
        "-",
        "—",
    }


def _scan_table(sheet: Any, check: dict, names: list[str]) -> tuple:
    header, rows = table_record_rows(sheet, check, scan_columns(check, names))
    columns = {name: unique_header_column(sheet, header, name) for name in names}
    return header, rows, columns


def evaluate_selection_coverage(
    check: dict, sheet: Any, tolerance: Decimal, evidence: WorkbookEvidence | None
) -> dict:
    """Count complete, unique eligible records, excluding declared summary rows."""
    record_names = check["key_columns"] + check["completion_columns"]
    header, record_rows, columns = _scan_table(sheet, check, record_names)
    identities = MergedIdentitySheet(sheet)
    population = check["eligible_keys"]
    comparisons = check.get("key_comparisons", {})
    # Validate the fixed source independently of the submission. A submitted
    # cell can name multiple distinct items without the source being ambiguous.
    for index, key in enumerate(population):
        for other in population[index + 1 :]:
            if any(
                all(
                    equivalent(
                        actual[name],
                        expected,
                        comparison=comparisons.get(name, "auto"),
                        tolerance=tolerance,
                    )
                    for name, expected in wanted.items()
                )
                for actual, wanted in ((key, other), (other, key))
            ):
                raise ValueError(
                    "source population has ambiguous normalized identities"
                )
    observed: dict[int, list[int]] = defaultdict(list)
    completed, invalid, incomplete, ambiguous = set(), [], [], []
    diagnostic_cells = []
    for row in record_rows:
        if summary_row(check, sheet, header, row, evidence):
            continue
        # The templates put audit tickmarks below the headers. These are not
        # selected transactions, even when their amount column contains "V".
        row_values = [
            sheet.cell(row, columns[name]).value
            for name in record_names
            if populated_answer(sheet, sheet.cell(row, columns[name]), evidence)
        ]
        if not row_values:
            continue
        matches = [
            i
            for i, key in enumerate(population)
            if all(
                equivalent(
                    identities.cell(row, columns[name]).value,
                    expected,
                    comparison=comparisons.get(name, "auto"),
                    tolerance=tolerance,
                )
                for name, expected in key.items()
            )
        ]
        if not matches and all(
            str(value).strip() in {"TB", "CF", "V", "R", "F", "T", "PBC"}
            for value in row_values
        ):
            continue
        if len(matches) != 1:
            (ambiguous if matches else invalid).append(row)
            diagnostic_cells.extend(
                sheet.cell(row, columns[name]).coordinate
                for name in check["key_columns"]
            )
            continue
        index = matches[0]
        observed[index].append(row)
        missing = [
            name
            for name in check["completion_columns"]
            if not documented_field(
                sheet,
                sheet.cell(row, columns[name]),
                check.get("completion_types", {}).get(name, "documented"),
                evidence,
            )
        ]
        if missing:
            incomplete.append(row)
            diagnostic_cells.extend(
                sheet.cell(row, columns[name]).coordinate for name in missing
            )
        else:
            completed.add(index)
    duplicates = {i: rows for i, rows in observed.items() if len(rows) > 1}
    for rows in duplicates.values():
        diagnostic_cells.extend(
            sheet.cell(row, columns[name]).coordinate
            for row in rows
            for name in check["key_columns"]
        )
    mandatory = {
        i for i, key in enumerate(population) if key in check["mandatory_keys"]
    }
    missing_mandatory = [population[i] for i in sorted(mandatory - completed)]
    # Coverage measures the eligible population. Optional work outside that
    # population earns no count credit, but does not erase completed required
    # testing. A substitute still fails because required selections/minimums
    # are checked against eligible identities only.
    passed = (
        len(completed) >= check["minimum_count"]
        and not missing_mandatory
        and not incomplete
        and not duplicates
    )
    message = f"{len(completed)} unique eligible items documented; minimum {check['minimum_count']} of {len(population)}"
    if missing_mandatory:
        message += f"; missing mandatory items: {missing_mandatory}"
    for label, rows in (
        ("outside population (not counted)", invalid),
        ("incomplete rows", incomplete),
        ("multiple eligible identities (not counted)", ambiguous),
        ("duplicate rows", [r for rows in duplicates.values() for r in rows]),
    ):
        if rows:
            message += f"; {label}: {rows}"
    cells = [] if passed else list(dict.fromkeys(diagnostic_cells))
    locations = [{"sheet": sheet.title, "cell": cell} for cell in cells]
    return check_result(
        check,
        sheet.title,
        passed,
        message,
        locations=locations,
        context_locations=missing_answer_context(
            compile_check(check, sheet.title), sheet
        )
        if not locations
        else [],
        expected=check["minimum_count"],
        observed=[len(completed)],
        population_count=len(population),
        tested_count=len(completed),
        missing_mandatory=missing_mandatory,
        out_of_population_rows=invalid,
        ambiguous_identity_rows=ambiguous,
        incomplete_rows=incomplete,
        duplicate_rows=list(duplicates.values()),
    )


def evaluate_row_count(
    check: dict, sheet: Any, evidence: WorkbookEvidence | None
) -> dict:
    """Limit populated record rows without comparing them to an answer population."""
    header, record_rows, by_name = _scan_table(sheet, check, check["key_columns"])
    columns = [by_name[name] for name in check["key_columns"]]
    rows = [
        row
        for row in record_rows
        if not summary_row(check, sheet, header, row, evidence)
        and any(
            documented_field(sheet, sheet.cell(row, column), "documented", evidence)
            for column in columns
        )
    ]
    passed = len(rows) <= check["maximum_count"]
    return check_result(
        check,
        sheet.title,
        passed,
        f"{len(rows)} record rows; maximum {check['maximum_count']}",
        locations=[]
        if passed
        else [
            {"sheet": sheet.title, "cell": sheet.cell(row, column).coordinate}
            for row in rows
            for column in columns
            if documented_field(sheet, sheet.cell(row, column), "documented", evidence)
        ],
        expected=check["maximum_count"],
        observed=[len(rows)],
        counted_rows=rows,
    )


def evaluate_date_window(
    check: dict, sheet: Any, evidence: WorkbookEvidence | None
) -> dict:
    """Flag dated transaction rows outside the declared period, once per table."""
    _, record_rows, columns = _scan_table(
        sheet, check, [check["date_column"], *check["record_columns"]]
    )
    date_column = columns[check["date_column"]]
    record_columns = [columns[name] for name in check["record_columns"]]
    start, stop = qualified_date(check["start_date"]), qualified_date(check["end_date"])
    locations = []
    for row in record_rows:
        if not all(
            documented_field(sheet, sheet.cell(row, col), "documented", evidence)
            for col in record_columns
        ):
            continue
        cell = sheet.cell(row, date_column)
        value = qualified_date(cell.value)
        # Missing dates, period summaries and N/A are not assertions of an out-of-window date.
        if value is not None and not start <= value <= stop:
            locations.append({"sheet": sheet.title, "cell": cell.coordinate})
    return check_result(
        check,
        sheet.title,
        not locations,
        f"{len(locations)} dated transaction rows outside {check['start_date']} through {check['end_date']}",
        locations=locations,
        expected=0,
        observed=[len(locations)],
    )


def evaluate_note_context(
    check: dict[str, Any], sheet: Any, evidence: WorkbookEvidence | None
) -> dict[str, Any]:
    """Flag ancillary content when every primary field in a record row is empty."""
    header, record_rows, columns = _scan_table(
        sheet, check, check["note_columns"] + check["primary_columns"]
    )
    note_columns = [columns[name] for name in check["note_columns"]]
    primary_columns = [columns[name] for name in check["primary_columns"]]
    identities = MergedIdentitySheet(sheet)
    formulas = evidence.formulas if evidence else None

    orphan_cells, orphan_rows = [], []
    for row in record_rows:
        if summary_row(check, sheet, header, row, evidence):
            continue
        notes = [sheet.cell(row, column) for column in note_columns]
        notes = [
            cell
            for cell in notes
            if populated_answer(sheet, cell, evidence)
            or bool(
                (
                    submitted_comment_text(
                        (
                            formulas[sheet.title][cell.coordinate]
                            if formulas is not None
                            else cell
                        ).comment
                    )
                    or ""
                ).strip()
            )
        ]
        if notes and not any(
            populated_answer(sheet, identities.cell(row, column), evidence)
            for column in primary_columns
        ):
            orphan_cells.extend(cell.coordinate for cell in notes)
            orphan_rows.append(row)
    passed = not orphan_cells
    locations = [{"sheet": sheet.title, "cell": cell} for cell in orphan_cells]
    message = (
        "No content on rows with empty primary fields"
        if passed
        else "Primary fields are empty on rows containing: " + ", ".join(orphan_cells)
    )
    return check_result(
        check,
        sheet.title,
        passed,
        message,
        locations=locations,
        context_locations=missing_answer_context(
            compile_check(check, sheet.title), sheet
        )
        if not locations
        else [],
        expected=0,
        observed=[len(orphan_rows)],
    )


def evaluate_check(
    check: Check,
    actual_sheet: Any,
    *,
    tolerance: Decimal = Decimal("0.01"),
    evidence: WorkbookEvidence | None = None,
) -> CheckEvaluation:
    lookup = None

    def finish(report: dict) -> CheckEvaluation:
        return CheckEvaluation(report, lookup)

    rule = check.to_dict()

    def lookup_failure(message: str, *, ambiguous: bool) -> CheckEvaluation:
        return finish(
            check_result(
                rule,
                actual_sheet.title,
                False,
                message,
                error="ambiguous_lookup" if ambiguous else "lookup_failed",
                context_locations=missing_answer_context(check, actual_sheet),
                selector=lookup_details(rule),
            )
        )

    try:
        if check.kind == "notes_have_context":
            return finish(evaluate_note_context(rule, actual_sheet, evidence))
        if check.kind == "selection_coverage":
            return finish(
                evaluate_selection_coverage(rule, actual_sheet, tolerance, evidence)
            )
        if check.kind == "row_count":
            return finish(evaluate_row_count(rule, actual_sheet, evidence))
        if check.kind == "date_window":
            return finish(evaluate_date_window(rule, actual_sheet, evidence))
        lookup = resolve_answer(check.selector, actual_sheet, tolerance=tolerance)
        if lookup.status != LookupStatus.RESOLVED:
            return lookup_failure(lookup.message, ambiguous=lookup.unresolved)
        cell = lookup.cell
        if evidence is not None and answer_cell(
            check.selector,
            cell,
            lookup.label,
            evidence.formulas,
            evidence.template,
            evidence.unchanged,
        ).get("context_only"):
            return lookup_failure(
                "required answer contains only template content or blank inputs",
                ambiguous=False,
            )
        passed = equivalent(
            cell.value,
            check.expected,
            comparison=check.comparison,
            tolerance=check.tolerance,
        )
        locations = [{"sheet": actual_sheet.title, "cell": cell.coordinate}]
        return finish(
            check_result(
                rule,
                actual_sheet.title,
                passed,
                "check passed" if passed else "value differs from the reference",
                locations=locations,
                observed=[cell.value],
            )
        )
    except (AmbiguousLookupError, MissingColumnError) as error:
        return lookup_failure(
            str(error), ambiguous=isinstance(error, AmbiguousLookupError)
        )
