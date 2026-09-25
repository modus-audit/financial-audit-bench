"""AP domain: importing registers its nodes, rules, and feature wiring."""

# Registration order is intentional: producers precede downstream consumers.
# ruff: noqa: I001

from financial_audit_bench.synthetic_binders.priors.graph.domains.ap import (  # noqa: F401
    invoice_identifiers,
    date_chain,
    payments,
    aging,
)
