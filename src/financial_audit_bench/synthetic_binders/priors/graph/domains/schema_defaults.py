"""Nullable schema defaults for fields without active DP hooks."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

NULLABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "ap_payment": ("check_run_id",),
    "company_context": ("dp_gl_annual_line_volume",),
    "customer": (
        "billing_cycle_class",
        "sales_tax_status",
        "sales_tax_rate",
        "tax_exemption_certificate",
    ),
    "employee": ("employee_class", "employment_basis", "termination_reason"),
    "journal_entry": ("entered_timestamp",),
    "vendor": ("cadence_class", "payee_class"),
    "vendor_invoice": ("inventory_movement_id", "inventory_item_id"),
}


def _register_nullable_fields(node_id: str, fields: tuple[str, ...]) -> None:
    @REGISTRY.finalize(node_id)
    def fill_nullable_fields(world: dict[str, Any]) -> None:
        value = world.get(node_id)
        rows = [value] if isinstance(value, dict) else value or []
        for row in rows:
            for field in fields:
                row.setdefault(field, None)


for _node_id, _fields in NULLABLE_FIELDS.items():
    _register_nullable_fields(_node_id, _fields)
