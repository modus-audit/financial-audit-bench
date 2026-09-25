"""Validate retained inventory package coverage."""

from __future__ import annotations


def run_inventory_ecc_package_checks(
    file_map: list[dict], world: dict
) -> list[dict[str, str]]:
    """Require the count file and selected procurement evidence triads."""
    if world.get("business_type") != "manufacturing":
        return []

    failures: list[dict[str, str]] = []
    by_request: dict[str, list[dict]] = {}
    for entry in file_map:
        by_request.setdefault(str(entry.get("request_id") or ""), []).append(entry)
    for request_id in ("INV-02", "INV-06", "INV-10"):
        if request_id not in by_request:
            failures.append({"error": f"inventory package omits {request_id}"})

    observations = by_request.get("DOC-INVENTORY-COUNT-OBSERVATION", [])
    if len(observations) != 1:
        failures.append(
            {"error": "inventory package must contain one auditor count observation"}
        )

    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.inventory_procurement_documents import (
        selected_inventory_procurement_movement_ids,
    )

    expected = set(selected_inventory_procurement_movement_ids(world))
    evidence_sets: dict[str, list[dict]] = {}
    for entry in by_request.get("INV-06", []):
        if entry.get("support_type") in {
            "purchase_order",
            "goods_receipt",
            "vendor_invoice",
        }:
            evidence_sets.setdefault(
                str(entry.get("evidence_set_id") or ""), []
            ).append(entry)
    actual: set[str] = set()
    required_types = {"purchase_order", "goods_receipt", "vendor_invoice"}
    for evidence_set_id, entries in evidence_sets.items():
        movement_ids = {
            str(
                (entry.get("source_record_ids") or {}).get("inventory_movement_id")
                or ""
            )
            for entry in entries
        }
        if (
            not evidence_set_id
            or {entry.get("support_type") for entry in entries} != required_types
            or len(movement_ids) != 1
            or "" in movement_ids
        ):
            failures.append(
                {"error": f"INV-06 evidence set is incomplete: {evidence_set_id!r}"}
            )
        else:
            actual.update(movement_ids)
    if actual != expected:
        failures.append(
            {
                "error": "INV-06 evidence does not cover selected movements",
                "detail": str(sorted(actual ^ expected)),
            }
        )
    return failures
