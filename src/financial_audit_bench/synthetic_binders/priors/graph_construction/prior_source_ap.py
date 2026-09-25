"""Accounted AP-book vendor sampler."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.bootstrap import (
    bootstrap_book,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    vendor_name,
)

AP_MODULE_COVERED_CLASSES = frozenset({"mortgage_or_escrow", "owner_related"})


def _ap_activity_book(self) -> list[dict[str, Any]] | None:
    """Return only the accounted DP AP book, cached by this value source."""
    if not hasattr(self, "_ap_book"):
        blocked = self.AP_MODULE_COVERED_CLASSES
        book = bootstrap_book(
            self.release,
            "ap_activity",
            self._rng("block.ap_activity.book"),
            predicate=lambda unit: unit["expense_class"] not in blocked,
            business_types={self.business_type},
            segment_only=True,
        )
        self._ap_book = book or None
    return self._ap_book


def _sample_vendor(self, world: World) -> list[dict[str, Any]]:
    """Derive opaque fictional vendor labels from an accounted DP book."""
    book = self._ap_activity_book()
    if not book:
        return []
    company_id = world["company_context"]["company_id"]
    class_counts: dict[str, int] = {}
    vendors: list[dict[str, Any]] = []
    for index, unit in enumerate(book, start=1):
        expense_class = str(unit["expense_class"])
        occurrence = class_counts.get(expense_class, 0)
        class_counts[expense_class] = occurrence + 1
        vendors.append(
            {
                "company_id": company_id,
                "name": vendor_name(expense_class, occurrence + 1),
                "vendor_id": f"VENDOR-{index:03d}",
            }
        )
    return vendors
