"""Explicit Core-PBC-to-deliverable resolution."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Mapping

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)


# Target request -> authoritative request IDs already present in the file map.
# Wildcards cover item-level documents whose stable IDs include a source key.
_planning = load_authored_policy("authored.global.operating-policy.v1").values[
    "planning_and_evidence"
]
NAMED_EVIDENCE_PROVIDERS: Mapping[str, tuple[str, ...]] = {
    key: tuple(value) for key, value in _planning["named_evidence_providers"].items()
}
NO_ACTIVITY_STATUSES = frozenset(_planning["no_activity_statuses"])


def resolve_core_request_deliveries(
    projection: dict[str, list[dict[str, Any]]],
    file_map: list[dict[str, Any]],
) -> None:
    """Bind every applicable Core request to a direct or named artifact."""
    rows_by_request: dict[str, list[dict[str, Any]]] = {}
    for row in file_map:
        rows_by_request.setdefault(str(row.get("request_id") or ""), []).append(row)

    unresolved: list[str] = []
    for artifact in projection["core_pbc_artifacts"]:
        request_id = str(artifact["request_id"])
        if artifact.get("applicability_status") != "applicable":
            artifact["delivery_resolution"] = "fact_driven_out_of_scope"
            continue
        direct = rows_by_request.get(request_id) or []
        if direct:
            artifact["delivery_resolution"] = "direct_request_id"
            artifact["provider_request_ids"] = [request_id]
            continue
        if artifact.get("evidence_status") in NO_ACTIVITY_STATUSES:
            artifact["delivery_resolution"] = "fact_driven_no_activity"
            artifact["provider_request_ids"] = []
            continue

        patterns = NAMED_EVIDENCE_PROVIDERS.get(request_id, ())
        providers = [
            row
            for row in file_map
            if any(
                fnmatchcase(str(row.get("request_id") or ""), pattern)
                for pattern in patterns
            )
        ]
        if not providers:
            unresolved.append(request_id)
            continue
        provider_ids = sorted({str(row["request_id"]) for row in providers})
        for provider in providers:
            alias = dict(provider)
            alias["request_id"] = request_id
            alias["satisfied_by_request_id"] = str(provider["request_id"])
            alias["resolution_kind"] = "named_artifact"
            file_map.append(alias)
            rows_by_request.setdefault(request_id, []).append(alias)
        artifact["delivery_resolution"] = "named_artifact"
        artifact["provider_request_ids"] = provider_ids

    if unresolved:
        raise ValueError(
            "applicable Core PBC requests have no direct or named deliverable: "
            f"{sorted(unresolved)}"
        )
