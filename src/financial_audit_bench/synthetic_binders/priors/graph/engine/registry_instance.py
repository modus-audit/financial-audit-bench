"""The shared RuleRegistry instance; declarations and rules attach to it."""

from __future__ import annotations

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    RuleRegistry,
)

REGISTRY = RuleRegistry()
