"""Immutable rubric definitions, authoring validation, and compilation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

from openpyxl.utils import column_index_from_string

from .comparison import COMPARISONS, normalize_text, parse_date
from .evidence_contract import (
    MAX_EVIDENCE_CELLS,
    criterion_character_limit,
    validate_cell_count,
)

# Immutable check definitions


def freeze(value: Any) -> Any:
    """Detach authoring JSON and prevent nested mutation during evaluation."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Create ordinary JSON containers at report and lookup boundaries."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class TableSelector:
    column: str
    key: Mapping[str, Any]
    options: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {**thaw(self.options), "key": thaw(self.key), "column": self.column}


@dataclass(frozen=True)
class FieldSelector:
    column_letter: str
    column: str
    match: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {
            "column_letter": self.column_letter,
            "column": self.column,
            "match": thaw(self.match),
        }


@dataclass(frozen=True)
class FullTableSelector:
    options: Mapping[str, Any]

    def to_dict(self) -> dict:
        return thaw(self.options)


AnswerSelector = TableSelector | FieldSelector | FullTableSelector


@dataclass(frozen=True)
class Check:
    id: str
    sheet: str
    kind: str
    description: str
    expected: Any
    selector: AnswerSelector | None
    comparison: str
    tolerance: Decimal
    definition: Mapping[str, Any]

    def to_dict(self) -> dict:
        """Return the declared rule, never a synthetic rule for a judge fallback."""
        return thaw(self.definition)


# Criterion descriptions shared by judging and feedback


def lookup_details(check: dict) -> dict:
    """Expose the requested identity/field even when no answer cell was located."""
    if len(check.get("extract", [])) > 1:
        return {
            "extracts": [
                {"sheet": spec["sheet"], **lookup_details({**check, "extract": [spec]})}
                for spec in check["extract"]
            ]
        }
    spec = check.get("extract", [{}])[0]
    selector = spec.get("selector", spec if spec else check)
    if "match" in selector:
        key = selector["match"].get("key", {})
    else:
        key = selector.get("key", {})
    columns = spec.get("columns", spec.get("column_letters", []))
    column = columns[0] if columns else check.get("column", check.get("column_letter"))
    section = selector.get("section", {})
    if "match" in selector:
        section = {
            name: selector["match"][name]
            for name in ("after", "before")
            if name in selector["match"]
        }
    return {
        "key": key,
        "column": column,
        **({"columns": columns} if len(columns) > 1 else {}),
        "section": section,
        "key_columns": check.get("key_columns", []),
        "required_headers": selector.get("required_headers", []),
        "end_header": selector.get("end_header", []),
    }


def display_value(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}: {display_value(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(display_value(item) for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float, Decimal)):
        return (
            format(Decimal(str(value)), ",f").rstrip("0").rstrip(".")
            if "." in str(value)
            else f"{value:,}"
        )
    return str(value) if value is not None else "blank"


def expectation(check: dict, tolerance: Decimal) -> str:
    if check.get("kind") == "notes_have_context":
        return (
            "Rows with ancillary content must have a primary value or a summary label."
        )
    if check.get("kind") == "selection_coverage":
        return f"Document at least {check['minimum_count']} unique eligible items, including every mandatory selection."
    if check.get("kind") == "row_count":
        return f"At most {check['maximum_count']} record rows in this table."
    if check.get("kind") == "date_window":
        return f"Dated transaction rows must fall within {check['start_date']} through {check['end_date']}."
    if "expected" not in check:
        return ""
    expected = check["expected"]
    label = check.get("column") or (
        f"Column {check['column_letter']}" if check.get("column_letter") else "Value"
    )
    comparison = check.get("comparison", "auto")
    if comparison == "yn":
        expected = {"y": "Yes", "n": "No"}.get(str(expected).lower(), expected)
    text = f"{label}: {display_value(expected)}"
    if comparison in {"auto", "numeric"} and not isinstance(expected, bool):
        try:
            number = Decimal(str(expected).replace(",", "").replace("$", ""))
            if number.is_finite():
                allowed = Decimal(
                    str(check.get("numeric_absolute_tolerance", tolerance))
                )
                text += (
                    f" (within {display_value(allowed)})" if allowed else " (exactly)"
                )
        except InvalidOperation:
            pass
    return text + "."


# Authoring validation and compilation


SELECTOR_FIELDS = {
    "key",
    "column",
    "column_letter",
    "match",
    "section",
    "required_headers",
    "end_header",
    "key_comparisons",
    "key_search_columns",
    "key_aliases",
    "key_alias_comparisons",
}


# Evidence citations document supplied source material, not completed answers.
METADATA_FIELDS = {
    "id",
    "description",
    "kind",
    "evidence_source",
    "header_row",
    "reject_conflicting_values",
}


VALUE_FIELDS = {"expected", "comparison", "numeric_absolute_tolerance"}


def validate_fields(data: dict, allowed: set[str], label: str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object")
    unknown = data.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")


def _tolerance(value: Any) -> None:
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(
            "numeric_absolute_tolerance must be finite and nonnegative"
        ) from error
    if not number.is_finite() or number < 0:
        raise ValueError("numeric_absolute_tolerance must be finite and nonnegative")


def _comparisons(modes: Any, keys: set[str]) -> None:
    if (
        not isinstance(modes, dict)
        or modes.keys() - keys
        or any(
            not isinstance(mode, str) or mode not in COMPARISONS
            for mode in modes.values()
        )
    ):
        raise ValueError(
            "comparisons must name supported modes for declared key columns"
        )


def _keys(key: Any, *, letters: bool = False) -> None:
    if not isinstance(key, dict) or not key:
        raise ValueError("row keys must be nonempty column/value mappings")
    for name, values in key.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("row keys require nonempty column names")
        if letters:
            column_index_from_string(name)
        values = values if isinstance(values, list) else [values]
        if not values or any(
            value is None or isinstance(value, (dict, list)) or not str(value).strip()
            for value in values
        ):
            raise ValueError("row keys require nonempty scalar identities")


def _match(match: dict) -> None:
    validate_fields(
        match, {"key", "comparisons", "after", "before", "following"}, "match"
    )
    if ("key" in match) == ("following" in match):
        raise ValueError("match requires a row key or a response after a named heading")
    for field in ("key", "after", "before"):
        if field in match:
            _keys(match[field], letters=True)
    _comparisons(match.get("comparisons", {}), set(match.get("key", {})))
    if "following" in match:
        following = match["following"]
        validate_fields(following, {"column", "index"}, "following")
        if (
            "after" not in match
            or type(following.get("index", 1)) is not int
            or following.get("index", 1) < 1
        ):
            raise ValueError("following requires an after heading and positive index")
        column_index_from_string(following.get("column", ""))


def _table_options(check: dict) -> None:
    for field in ("required_headers", "end_header"):
        if field in check:
            _named_columns(check[field], field)
    if "section" in check:
        section = check["section"]
        validate_fields(section, {"after", "before"}, "section")
        if not section:
            raise ValueError("section requires after and/or before label mappings")
        for key in section.values():
            _keys(key, letters=True)


def extract_selector(spec: dict) -> dict:
    if "selector" in spec:
        if spec["selector"].get("kind") == "table":
            return {**spec["selector"], "columns": spec["columns"]}
        selector = {
            **spec["selector"],
            "kind": "row_value",
            "column": spec["columns"][0],
        }
        if len(spec["columns"]) > 1:
            # Resolve one shared table layout, including every selected field.
            names = [*selector.get("required_headers", []), *spec["columns"]]
            selector["required_headers"] = list(
                {normalize_text(name): name for name in names}.values()
            )
        return selector
    return {
        "kind": "row_value",
        "match": spec["match"],
        "column_letter": spec["column_letters"][0],
        "column": spec.get("column_labels", {}).get(
            spec["column_letters"][0], spec["column_letters"][0]
        ),
    }


def _named_columns(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(name, str) or not name.strip() for name in value)
        or len({normalize_text(name) for name in value}) != len(value)
    ):
        raise ValueError(f"{field} must list distinct nonempty column names")
    return value


def validate_answer_selector(check: dict) -> None:
    """One target column, with lookup metadata that never depends on the answer."""
    validate_fields(
        check, SELECTOR_FIELDS | VALUE_FIELDS | METADATA_FIELDS, "answer check"
    )
    if ("match" in check) == ("key" in check):
        raise ValueError("answer selection requires exactly one key or match")
    if "match" in check:
        if not isinstance(check.get("match"), dict) or not check.get("column_letter"):
            raise ValueError("fixed fields require match and one column_letter")
        column_index_from_string(check["column_letter"])
        _match(check["match"])
        if "section" in check or "required_headers" in check or "end_header" in check:
            raise ValueError("fixed fields use match.after/before section labels")
    else:
        _keys(check["key"])
        if not isinstance(check.get("column"), str) or not check["column"].strip():
            raise ValueError("table answer selection requires one named column")
        if "column_letter" in check:
            raise ValueError("table answers must use named column headers")
        _table_options(check)
        _comparisons(check.get("key_comparisons", {}), set(check["key"]))
        for field in ("key_aliases", "key_search_columns"):
            values = check.get(field, {})
            if not isinstance(values, dict) or values.keys() - check["key"].keys():
                raise ValueError(f"{field} must refer to declared key columns")
            for names in values.values():
                _named_columns(names, field)
        aliases = check.get("key_alias_comparisons", {})
        if (
            not isinstance(aliases, dict)
            or aliases.keys() - check.get("key_aliases", {}).keys()
            or any(mode != "identifier" for mode in aliases.values())
        ):
            raise ValueError(
                "key_alias_comparisons must map declared aliases to identifier"
            )


TABLE_FIELDS = {"section", "required_headers", "end_header"}
SCAN_FIELDS = {
    "notes_have_context": {
        "primary_columns",
        "note_columns",
        "summary_rows",
        "expected",
    },
    "selection_coverage": {
        "key_columns",
        "completion_columns",
        "eligible_keys",
        "mandatory_keys",
        "minimum_count",
        "population_source",
        "summary_rows",
        "key_comparisons",
        "completion_types",
    },
    "row_count": {"key_columns", "maximum_count", "summary_rows"},
    "date_window": {"date_column", "record_columns", "start_date", "end_date"},
}


def _column_types(types: Any, columns: list[str], label: str) -> None:
    if (
        not isinstance(types, dict)
        or types.keys() - set(columns)
        or any(
            not isinstance(kind, str)
            or kind not in {"numeric", "date", "yn", "documented"}
            for kind in types.values()
        )
    ):
        raise ValueError(
            f"{label} must type declared columns as numeric, date, yn or documented"
        )


def _validate_summary_rows(summary: dict) -> None:
    validate_fields(
        summary,
        {"column", "labels", "label_prefixes", "identity_columns", "identity_types"},
        "summary_rows",
    )
    if not isinstance(summary.get("column"), str) or not summary["column"].strip():
        raise ValueError("summary_rows requires a label column")
    if not (summary.get("labels") or summary.get("label_prefixes")):
        raise ValueError("summary_rows requires labels or label_prefixes")
    for field in ("labels", "label_prefixes", "identity_columns"):
        if field == "identity_columns" or field in summary:
            _named_columns(summary.get(field), "summary " + field)
    _column_types(
        summary.get("identity_types", {}),
        summary["identity_columns"],
        "summary identity_types",
    )


def _validate_table_scan(check: dict) -> None:
    kind = check["kind"]
    validate_fields(check, METADATA_FIELDS | TABLE_FIELDS | SCAN_FIELDS[kind], kind)
    _table_options(check)
    if (
        kind != "notes_have_context"
        and not check.get("end_header")
        and not check.get("section", {}).get("before")
    ):
        raise ValueError(f"{kind} requires an explicit table end")
    if check.get("summary_rows") is not None:
        _validate_summary_rows(check["summary_rows"])

    if kind == "notes_have_context":
        for field in ("primary_columns", "note_columns"):
            _named_columns(check.get(field), field)
        if set(map(normalize_text, check["primary_columns"])) & set(
            map(normalize_text, check["note_columns"])
        ):
            raise ValueError("primary and ancillary columns must be disjoint")
    elif kind == "selection_coverage":
        _validate_selection_coverage(check)
    elif kind == "row_count":
        _named_columns(check.get("key_columns"), "key_columns")
        if type(check.get("maximum_count")) is not int or check["maximum_count"] < 0:
            raise ValueError("maximum_count must be a nonnegative integer")
    elif kind == "date_window":
        _named_columns([check.get("date_column")], "date column")
        _named_columns(check.get("record_columns"), "record columns")
        start, end = (
            parse_date(check.get("start_date")),
            parse_date(check.get("end_date")),
        )
        if start is None or end is None or start > end:
            raise ValueError("date_window requires ordered start_date and end_date")


def validate_automatic_check(check: dict) -> None:
    kind = check.get("kind")
    if kind in SCAN_FIELDS:
        _validate_table_scan(check)
    elif kind == "row_value":
        validate_answer_selector(check)
        if "expected" not in check or isinstance(check["expected"], (dict, list)):
            raise ValueError("answer checks require one embedded expected value")
        mode = check.get("comparison", "auto")
        if not isinstance(mode, str) or mode not in COMPARISONS:
            raise ValueError(f"Unknown comparison: {mode!r}")
        if "numeric_absolute_tolerance" in check:
            _tolerance(check["numeric_absolute_tolerance"])
    else:
        raise ValueError(
            "automatic checks require row_value, notes_have_context, selection_coverage, row_count or date_window"
        )


def _validate_selection_coverage(check: dict) -> None:
    """A fixed source population and bounded table; no submission-driven exclusions."""
    columns = _named_columns(check.get("key_columns"), "key_columns")
    _named_columns(check.get("completion_columns"), "completion_columns")
    population = check.get("eligible_keys")
    mandatory = check.get("mandatory_keys")
    if not isinstance(population, list) or not isinstance(mandatory, list):
        raise ValueError(
            "selection_coverage requires eligible_keys and mandatory_keys lists"
        )
    for key in population + mandatory:
        if (
            not isinstance(key, dict)
            or set(key) != set(columns)
            or any(
                value is None
                or isinstance(value, (list, dict, bool))
                or not str(value).strip()
                for value in key.values()
            )
        ):
            raise ValueError(
                "population keys must supply every key column with a scalar identity"
            )
    serialized = [json.dumps(key, sort_keys=True) for key in population]
    if len(serialized) != len(set(serialized)):
        raise ValueError("eligible_keys contains duplicate identities")
    required = [json.dumps(key, sort_keys=True) for key in mandatory]
    if len(required) != len(set(required)) or not set(required) <= set(serialized):
        raise ValueError("mandatory_keys must be a unique subset of eligible_keys")
    minimum = check.get("minimum_count")
    if type(minimum) is not int or not len(mandatory) <= minimum <= len(population):
        raise ValueError(
            "minimum_count must cover mandatory items and fit the source population"
        )
    comparisons = check.get("key_comparisons", {})
    _comparisons(comparisons, set(columns))
    _column_types(
        check.get("completion_types", {}),
        check["completion_columns"],
        "completion_types",
    )
    if (
        not isinstance(check.get("population_source"), str)
        or not check["population_source"].strip()
    ):
        raise ValueError("selection_coverage requires population_source provenance")


def validate_extract(check: dict) -> None:
    specs = check.get("extract")
    if (
        not isinstance(specs, list)
        or not 1 <= len(specs) <= MAX_EVIDENCE_CELLS
        or any(not isinstance(spec, dict) for spec in specs)
    ):
        raise ValueError("semantic checks require one to three explicit extracts")
    for spec in specs:
        _validate_extract_spec(spec)
    if any(spec.get("selector", {}).get("kind") == "table" for spec in specs):
        if len(specs) != 1:
            raise ValueError("full-table checks require exactly one table extract")
        return
    validate_cell_count(
        sum(len(spec.get("columns", spec.get("column_letters", []))) for spec in specs)
    )


def _validate_extract_spec(spec: dict) -> None:
    if not isinstance(spec.get("sheet"), str) or not spec["sheet"].strip():
        raise ValueError("semantic extract must name its sheet")
    permitted = {
        "sheet",
        "selector",
        "columns",
        "match",
        "column_letters",
        "column_labels",
    }
    if set(spec) - permitted:
        raise ValueError(
            "semantic extracts contain only a selector and named answer columns"
        )
    if ("selector" in spec) == ("match" in spec):
        raise ValueError(
            "semantic extract requires one keyed selector or fixed-field match"
        )
    if "selector" in spec:
        if "column_letters" in spec or "column_labels" in spec:
            raise ValueError("semantic table answers require named column headers")
        columns = _named_columns(spec.get("columns"), "columns")
        if not isinstance(spec["selector"], dict):
            raise ValueError("semantic selector must be an object")
        if spec["selector"].get("kind") == "table":
            selector = spec["selector"]
            validate_fields(selector, {"kind", "key", "required_headers"}, "full table")
            _keys(selector.get("key"))
            _named_columns(selector.get("required_headers"), "required_headers")
            # Table anchors locate the evidence region; keys and columns describe
            # the question for the judge. Equivalent answer labels must not make
            # a complete table disappear before semantic evaluation.
            return
        if not 1 <= len(columns) <= MAX_EVIDENCE_CELLS:
            raise ValueError(
                "semantic keyed extracts must select one to three answer columns"
            )
        selector = spec["selector"]
        if not isinstance(selector, dict) or selector.get("kind") not in {
            "row_value",
            "key_exists",
        }:
            raise ValueError("semantic table extraction requires a keyed row selector")
        if {
            "expected",
            "comparison",
            "numeric_absolute_tolerance",
            "date_endpoint_start",
        } & selector.keys():
            raise ValueError(
                "semantic selectors cannot include expected answers or value comparisons"
            )
    else:
        if "columns" in spec:
            raise ValueError("fixed fields use one column letter")
        letters = _named_columns(spec.get("column_letters"), "column_letters")
        if len(letters) != 1:
            raise ValueError("semantic checks must select exactly one answer column")
        labels = spec.get("column_labels", {})
        if not isinstance(labels, dict) or any(
            column not in letters or not isinstance(label, str) or not label.strip()
            for column, label in labels.items()
        ):
            raise ValueError(
                "fixed-field labels must describe the selected answer column"
            )
    validate_answer_selector(extract_selector(spec))


def validate_semantic_check(check: dict, sheet: str) -> None:
    validate_fields(
        check,
        {
            "id",
            "kind",
            "sheet",
            "description",
            "expected",
            "extract",
            "evidence_source",
        },
        "semantic check",
    )
    if not check["id"].startswith("llm."):
        raise ValueError("semantic check IDs must start with llm.")
    if check.get("sheet", sheet) != sheet:
        raise ValueError("semantic check sheet must match its worksheet")
    expected = check.get("expected")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError("semantic check requires an expected answer")
    full_table = any(
        spec.get("selector", {}).get("kind") == "table"
        for spec in check.get("extract", [])
    )
    if len(expected) > criterion_character_limit(full_table):
        raise ValueError("semantic expected answer is too long; split the check")
    validate_extract(check)


def load_rubric(task: Path, *, automatic_only: bool = False) -> dict:
    """Load the single task rubric and validate all checks before selecting a mode."""
    rubric = json.loads((task / "rubric.json").read_text())
    validate_fields(
        rubric, {"task_id", "output", "comparison", "tabs"}, "rubric"
    )
    filename = rubric.get("output")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(".xlsx")
    ):
        raise ValueError("rubric output must be a single .xlsx filename")
    comparison = rubric.get("comparison", {})
    validate_fields(
        comparison,
        {"numeric_absolute_tolerance", "text", "dates", "yes_no"},
        "comparison",
    )
    for field in ("text", "dates", "yes_no"):
        if field in comparison and not isinstance(comparison[field], str):
            raise ValueError(
                f"comparison.{field} is descriptive metadata, not a setting"
            )
    _tolerance(comparison.get("numeric_absolute_tolerance", 0.01))
    rubric["comparison"] = {
        "numeric_absolute_tolerance": comparison.get("numeric_absolute_tolerance", 0.01)
    }
    tabs = rubric.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise ValueError("rubric must contain tabs")
    tab_ids, sheet_names, check_ids = set(), set(), set()
    for tab in tabs:
        validate_fields(
            tab,
            {"id", "sheet", "checks", "header_row", "key_comparisons"},
            "rubric tab",
        )
        for field in ("id", "sheet"):
            if not isinstance(tab.get(field), str) or not tab[field].strip():
                raise ValueError(f"rubric tab requires {field}")
        if tab["id"] in tab_ids or tab["sheet"] in sheet_names:
            raise ValueError("rubric tabs require unique IDs and sheets")
        tab_ids.add(tab["id"])
        sheet_names.add(tab["sheet"])
        if not isinstance(tab.get("checks"), list):
            raise ValueError("rubric tab requires checks")
        for check in tab["checks"]:
            for field in ("id", "description"):
                if not isinstance(check.get(field), str) or not check[field].strip():
                    raise ValueError(f"check requires {field}")
            if check["id"] in check_ids:
                raise ValueError("rubric check IDs must be unique")
            check_ids.add(check["id"])
            if check.get("kind") == "llm":
                validate_semantic_check(check, tab["sheet"])
            else:
                if tab.get("key_comparisons") and "key" in check:
                    check["key_comparisons"] = {
                        **tab["key_comparisons"],
                        **check.get("key_comparisons", {}),
                    }
                validate_automatic_check(check)
    if not check_ids:
        raise ValueError("rubric must contain at least one check")
    if automatic_only:
        for tab in tabs:
            tab["checks"] = [c for c in tab["checks"] if c["kind"] != "llm"]
    return rubric


def compile_selector(spec: dict) -> AnswerSelector:
    """Compile only selection fields; expected values cannot influence lookup."""
    if spec.get("kind") == "table":
        return FullTableSelector(freeze(spec))
    if "match" in spec:
        return FieldSelector(
            spec["column_letter"],
            spec.get("column", spec["column_letter"]),
            freeze(spec["match"]),
        )
    options = {
        key: value
        for key, value in spec.items()
        if key in SELECTOR_FIELDS - {"key", "column"}
    }
    return TableSelector(spec["column"], freeze(spec["key"]), freeze(options))


def compile_check(
    rule: dict, sheet: str = "", *, tolerance: Decimal = Decimal(".01")
) -> Check:
    kind = rule.get("kind", "llm" if "extract" in rule else "row_value")
    if kind == "llm":
        spec = extract_selector(rule["extract"][0])
        sheet = rule["extract"][0]["sheet"]
    else:
        spec = rule
        sheet = rule.get("sheet", sheet)
    selector = compile_selector(spec) if kind in {"row_value", "llm"} else None
    return Check(
        rule["id"],
        sheet,
        kind,
        rule.get("description", rule["id"]),
        freeze(rule.get("expected")),
        selector,
        rule.get("comparison", "auto"),
        Decimal(str(rule.get("numeric_absolute_tolerance", tolerance))),
        freeze(rule),
    )


def compile_checks(rubric: dict) -> tuple[Check, ...]:
    tolerance = Decimal(str(rubric["comparison"]["numeric_absolute_tolerance"]))
    return tuple(
        compile_check(rule, tab["sheet"], tolerance=tolerance)
        for tab in rubric["tabs"]
        for rule in tab["checks"]
    )


def extract_checks(check: Check) -> tuple[Check, ...]:
    """Compile each explicit extract without changing the declared scored check."""
    if check.kind != "llm" or len(check.definition.get("extract", ())) <= 1:
        return (check,)
    definition = check.to_dict()
    return tuple(
        compile_check(
            {**definition, "sheet": spec["sheet"], "extract": [spec]},
            tolerance=check.tolerance,
        )
        for spec in definition["extract"]
    )
