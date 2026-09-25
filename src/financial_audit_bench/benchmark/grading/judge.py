"""Configure and execute fixed-answer judge requests with validated cached verdicts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable

from financial_audit_bench.llms import complete

from .judge_request import (
    JUDGE_MODEL,
    JUDGE_REASONING_EFFORT,
    JudgeRequest,
    build_judge_request,
    validate_verdicts,
)
from .results import (
    check_result,
    write_json,
)
from .rubric import (
    Check,
    lookup_details,
)
from .workbook import (
    WorkbookEvidence,
    extract_check,
)

JUDGE_WORKERS = 20
JUDGE_REQUEST_TIMEOUT_SECONDS = 120


def execute_judge_request(
    prepared: JudgeRequest, output_dir: Path, complete_fn: Callable
) -> dict:
    """Execute or reuse one request and persist its validated verdict or error."""
    request = json.loads(prepared.evidence_json)
    model, api_base = prepared.model, prepared.api_base
    reasoning_effort, fingerprint = prepared.reasoning_effort, prepared.fingerprint
    messages, schema = prepared.messages, prepared.schema
    path = output_dir / f"request_{fingerprint}.json"
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            if (
                isinstance(cached, dict)
                and cached.get("fingerprint") == fingerprint
                and "response" in cached
            ):
                verdicts = validate_verdicts(cached["response"], [request])
                return {**cached, "verdicts": verdicts, "reused": True}
        except (ValueError, TypeError):
            pass  # An invalid cache is not a verdict about the submission.
    record = {
        "fingerprint": fingerprint,
        "check_id": prepared.check_id,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "messages": messages,
    }
    try:
        limits = (
            {"max_output_tokens": 8000}
            if model.startswith("gpt-") and api_base is None
            else {}
        )
        response = complete_fn(
            model=model,
            api_base=api_base,
            messages=messages,
            reasoning_effort=reasoning_effort,
            response_format=schema,
            request_timeout_seconds=JUDGE_REQUEST_TIMEOUT_SECONDS,
            **limits,
        )
        record.update(
            {
                "returned_model": response.model,
                "usage": response.usage,
                "cost_usd": response.cost,
                "latency_seconds": response.latency_seconds,
                "raw_response": response.content,
            }
        )
        parsed = json.loads(response.content)
        verdicts = validate_verdicts(parsed, [request])
        record.update({"response": parsed, "verdicts": verdicts, "reused": False})
    except Exception as error:
        record["error"] = {"type": type(error).__name__, "message": str(error)}
    write_json(path, record)
    return record


def judge_checks(requests: list[dict], output_dir: Path) -> dict:
    """Schedule independent judgments and summarize their persisted outcomes."""
    ids = [request["id"] for request in requests]
    if len(ids) != len(set(ids)):
        raise ValueError("semantic check ids must be unique")
    prepared = [
        build_judge_request(
            request, model=JUDGE_MODEL, reasoning_effort=JUDGE_REASONING_EFFORT
        )
        for request in requests
    ]
    output_dir = Path(output_dir)
    execute = partial(
        execute_judge_request,
        output_dir=output_dir,
        complete_fn=complete,
    )
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as pool:
        ordered = list(pool.map(execute, prepared))
    verdicts = [v for result in ordered for v in result.get("verdicts", [])]
    errors = [
        {"check_id": requests[i]["id"], **r["error"]}
        for i, r in enumerate(ordered)
        if "error" in r
    ]
    costs = [r.get("cost_usd") for r in ordered if not r.get("reused") and "usage" in r]
    result = {
        "model": JUDGE_MODEL,
        "status": "error" if errors else "evaluated",
        "checks": verdicts,
        "errors": errors,
        "requests": len(requests),
        "request_files": {
            request["id"]: f"request_{record['fingerprint']}.json"
            for request, record in zip(requests, ordered)
        },
        "reused_requests": sum(bool(r.get("reused")) for r in ordered),
        "cost_usd": sum(costs) if all(c is not None for c in costs) else None,
    }
    write_json(output_dir / "summary.json", result)
    return result


def _semantic_result(
    check: Check,
    request: dict,
    *,
    passed: bool | None,
    message: str,
    evidence_cells: list[str] | None = None,
    status: str = "evaluated",
) -> dict:
    cells = [c for c in request["actual"] if not c.get("context_only", False)]
    if evidence_cells:
        cells = [c for c in cells if c["id"] in evidence_cells]
    locations = [{"sheet": c["sheet"], "cell": c["cell"]} for c in cells]
    return check_result(
        {**check.to_dict(), "kind": "llm", "expected": request["expected"]},
        check.sheet,
        passed,
        message,
        locations=locations,
        status=status,
        selector=lookup_details(check.to_dict()),
        grading_method=(
            "llm_record"
            if request.get("record") or request.get("records")
            else "llm_table"
            if request.get("table") or request.get("full_table")
            else "llm_cell"
        ),
        declared_kind=check.kind,
        evidence_cells=evidence_cells or [],
        extraction_errors=request["extraction_errors"],
    )


def evaluate_checks(
    rules: list[Check],
    actual: Any,
    evidence: WorkbookEvidence,
    output: Path,
) -> tuple[list[dict], dict]:
    requests, extracted, checks = [], [], {}
    for check in rules:
        if check.kind != "llm":
            raise ValueError("Only declared semantic checks may be sent to the judge")
        request = extract_check(
            check,
            actual,
            evidence.formulas,
            evidence.template,
            unchanged=evidence.unchanged,
            criterion=check.expected,
        )
        extracted.append(request)
        if evidence.unchanged:
            checks[check.id] = _semantic_result(
                check,
                request,
                passed=False,
                message="template contains no submitted answers",
            )
        elif request["extraction_errors"]:
            checks[check.id] = _semantic_result(
                check,
                request,
                passed=False,
                message="Required answer is missing or not uniquely documented: "
                + "; ".join(request["extraction_errors"]),
            )
        elif not request["has_answer"]:
            checks[check.id] = _semantic_result(
                check,
                request,
                passed=False,
                message="required answer missing from extracted cells",
            )
        else:
            requests.append(request)
    directory = output.parent / "llm_judge"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "extracted_checks.json").write_text(
        json.dumps(extracted, indent=2, default=str) + "\n"
    )
    batch = judge_checks(requests, directory)
    verdicts = {c["id"]: c for c in batch["checks"]}
    by_id = {c.id: c for c in rules}
    for request in requests:
        verdict = verdicts.get(request["id"])
        checks[request["id"]] = _semantic_result(
            by_id[request["id"]],
            request,
            passed=verdict["passed"] if verdict else None,
            message=verdict["reason"]
            if verdict
            else "judge request failed; not a model deduction",
            evidence_cells=verdict["evidence_cells"] if verdict else [],
            status="evaluated" if verdict else "error",
        )
    return [checks[c.id] for c in rules], {
        k: v for k, v in batch.items() if k != "checks"
    }
