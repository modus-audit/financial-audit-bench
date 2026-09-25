"""Journal domain: importing registers its nodes, rules, and feature wiring."""

# Registration order is intentional: producers precede downstream consumers.
# ruff: noqa: I001

from financial_audit_bench.synthetic_binders.priors.graph.domains.journal import (  # noqa: F401
    presentation,
    enrichment,
    admission,
    postings,
    population,
    controls,
    adjustments,
    accruals,
)
