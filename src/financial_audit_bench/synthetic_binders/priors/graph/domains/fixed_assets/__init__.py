"""Fixed-assets domain: importing registers its nodes, rules, and feature wiring."""

# Registration order is intentional.
# ruff: noqa: I001

from financial_audit_bench.synthetic_binders.priors.graph.domains.fixed_assets import (  # noqa: F401
    policy,
    capitalization,
    depreciation,
    rollforwards,
)
