"""Render Core PBC artifacts and field lineage from a generated world."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import load_registry
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection import (
    op_ap_cutoff as _op_ap_cutoff,
    op_ap_subsequent as _op_ap_subsequent,
    op_common as _op_common,
    op_fixed_assets as _op_fixed_assets,
    op_inventory as _op_inventory,
    op_payroll as _op_payroll,
    op_receivables as _op_receivables,
    op_receivables_subsequent as _op_receivables_subsequent,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    MANUFACTURING_SPECIALIZED_WORKBOOK_REQUESTS,
    SPECIALIZED_WORKBOOK_REQUESTS,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.request_resolution import (
    NAMED_EVIDENCE_PROVIDERS,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    OPS,
)

_LOADED_OP_MODULES = (
    _op_ap_cutoff,
    _op_ap_subsequent,
    _op_common,
    _op_fixed_assets,
    _op_inventory,
    _op_payroll,
    _op_receivables,
    _op_receivables_subsequent,
)
del _LOADED_OP_MODULES

_VALUES = load_registry("registry.pbc.requests.v1").values
_ROWS = _VALUES["projection_fields"]
FIELDLESS_REQUESTS = (
    set(_VALUES["request_document_kinds"])
    | set(NAMED_EVIDENCE_PROVIDERS)
    | set(SPECIALIZED_WORKBOOK_REQUESTS)
)
FIELD_SPECS = {
    (request_id, expected_field): (
        source_node,
        source_field,
        operation[0] if operation else "direct",
    )
    for request_id, expected_field, source_node, source_field, *operation in _ROWS
}
FIELDS_BY_REQUEST: dict[str, list[str]] = {}
for request_id, expected_field, *_ in _ROWS:
    FIELDS_BY_REQUEST.setdefault(request_id, []).append(expected_field)
if len(FIELD_SPECS) != len(_ROWS):
    raise ValueError("duplicate projection field mapping")

World = dict[str, Any]

FAMILY_FLAGS: dict[str, str | None] = {
    "accounts_receivable": "has_accounts_receivable",
    "ap_and_accruals": "has_ap_activity",
    "cash": None,
    "fixed_assets": "has_fixed_assets",
    "inventory": "has_inventory",
    "journal_entries": None,
    "payroll": "has_payroll",
    "revenue": None,
}
EVIDENCE_STATUSES = frozenset(
    {
        "generated",
        "known_missing_or_direct_to_auditor",
        "no_reportable_activity",
        "not_supported_by_archetype",
        "unknown",
    }
)

# Goods-flavored AR requests exist only where product ships on account.
AR_GOODS_REQUESTS = frozenset({"AR-04", "AR-06"})

MANUFACTURING_REVENUE_REQUESTS = frozenset({"REV-01", "REV-02", "REV-03"})

# Fail at import time if a spec names an op that does not exist.
_UNKNOWN_OPS = {op for _, _, op in FIELD_SPECS.values()} - set(OPS)
if _UNKNOWN_OPS:
    raise ImportError(f"field specs reference unknown ops: {sorted(_UNKNOWN_OPS)}")


def source_value(world: World, node_id: str, field: str) -> Any:
    """Extract a source column without losing rows; None when unpopulated."""
    value = world.get(node_id)
    if isinstance(value, dict):
        return value.get(field)
    if isinstance(value, list):
        values = [row[field] for row in value if row.get(field) is not None]
        if not values:
            return None
        return values[0] if len(values) == 1 else values
    return None


def expected_fields_for_request(request_id: str, world: World) -> list[str]:
    """Select the industry field contract without inspecting population state."""
    if request_id in FIELDLESS_REQUESTS:
        return []
    if (
        world["business_type"] == "manufacturing"
        and request_id in MANUFACTURING_SPECIALIZED_WORKBOOK_REQUESTS
    ):
        return []
    return FIELDS_BY_REQUEST[request_id]


def render_core_pbcs(
    registry: dict[str, Any], world: World
) -> dict[str, list[dict[str, Any]]]:
    """Render all Core request artifacts and one lineage row per expected field."""
    profile = world["company_feature_profile"]
    business_type = str(world["business_type"])
    if profile.get("has_inventory"):
        from financial_audit_bench.synthetic_binders.contracts.inventory_costing import (
            validate_inventory_costing_sources,
        )

        validate_inventory_costing_sources(world)
    artifacts = []
    lineage = []
    for request_id, request in registry["pbc_requests"].items():
        family = request["family"]
        flag = FAMILY_FLAGS[family]
        applicable = flag is None or bool(profile.get(flag))
        if family == "revenue":
            applicable = business_type == "staffing_services"
            if business_type == "manufacturing":
                applicable = bool(
                    request_id in MANUFACTURING_REVENUE_REQUESTS
                    and world.get("customer_invoice")
                    and world.get("customer_invoice_line")
                )
        if applicable and request_id in AR_GOODS_REQUESTS:
            applicable = business_type == "manufacturing"
        if applicable and request_id == "INV-06":
            item_categories = {
                row["inventory_item_id"]: row["category"]
                for row in world.get("inventory_item") or []
            }
            applicable = any(
                row.get("movement_kind") == "purchase_receipt"
                and item_categories.get(row.get("inventory_item_id"))
                != "work_in_process"
                for row in world.get("inventory_movement") or []
            )
        if applicable and request_id == "INV-07":
            item_categories = {
                row["inventory_item_id"]: row["category"]
                for row in world.get("inventory_item") or []
            }
            applicable = any(
                row.get("movement_kind") == "production_completion"
                and item_categories.get(row.get("inventory_item_id"))
                in {"work_in_process", "finished_goods"}
                for row in world.get("inventory_movement") or []
            )
        expected_fields = expected_fields_for_request(request_id, world)
        for expected_field in expected_fields:
            source_node, source_field, op_name = FIELD_SPECS[
                (request_id, expected_field)
            ]
            value = source_value(world, source_node, source_field)
            # A reduced research graph intentionally omits whole optional node families.
            # Their projection fields are out of scope and must not invoke operations
            # that assume those populations exist.
            if source_node in world:
                value = OPS[op_name](world, source_node, source_field, value)
            if not applicable:
                value = "out_of_scope"
                status = "out_of_scope"
            elif value is None:
                value = "not_applicable"
                status = "not_applicable"
            else:
                status = "generated"
            row = {
                "expected_field": expected_field,
                "generated_value": value,
                "request_id": request_id,
                "source_field": source_field,
                "source_node": source_node,
                "validation_status": status,
            }
            lineage.append(row)
        evidence_status = world.get("core_pbc_evidence_status", {}).get(
            request_id,
            "generated" if applicable else "not_supported_by_archetype",
        )
        if evidence_status not in EVIDENCE_STATUSES:
            raise ValueError(f"invalid Core PBC evidence status: {evidence_status}")
        artifacts.append(
            {
                "applicability_status": "applicable" if applicable else "out_of_scope",
                "evidence_status": evidence_status,
                "family": family,
                "request_id": request_id,
                "request_name": request["request"],
            }
        )
    validate_core_pbc_projection(registry, artifacts, lineage, world)
    return {"core_pbc_artifacts": artifacts, "core_pbc_field_lineage": lineage}


def validate_core_pbc_projection(
    registry: dict[str, Any],
    artifacts: list[dict[str, Any]],
    lineage: list[dict[str, Any]],
    world: World,
) -> None:
    """Require exact Core coverage and graph-declared lineage sources."""
    requests = registry["pbc_requests"]
    expected_pairs = {
        (request_id, field)
        for request_id in requests
        for field in expected_fields_for_request(request_id, world)
    }
    actual_pairs = {(row["request_id"], row["expected_field"]) for row in lineage}
    if len(artifacts) != len(requests) or actual_pairs != expected_pairs:
        raise ValueError("Core PBC projection does not match the registry")

    def has_source_field(node_id: str, field: str) -> bool:
        if node_id not in REGISTRY.nodes:
            return False
        value = world.get(node_id)
        rows = [value] if isinstance(value, dict) else value or []
        if not rows:
            return True
        return any(isinstance(source, dict) and field in source for source in rows)

    invalid = [
        (row["source_node"], row["source_field"])
        for row in lineage
        if row["validation_status"] == "generated"
        and not has_source_field(row["source_node"], row["source_field"])
    ]
    if invalid:
        raise ValueError(f"Core PBC lineage contains invalid graph sources: {invalid}")
