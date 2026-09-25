"""Register the retained fixed-asset capitalization policy."""

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


REGISTRY.sample(
    "capitalization_policy",
    inputs=("fixed_asset", "fiscal_calendar"),
    gate="has_fixed_assets",
    rule_name="derive_capitalization_policy",
)
