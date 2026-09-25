"""Payroll domain: importing registers its nodes, rules, and feature wiring."""

# Registration order is intentional: producers precede downstream consumers.
# ruff: noqa: I001

from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll import (  # noqa: F401
    policy,
    runs,
    registers,
    remittances,
    subsequent_events,
    postings,
    evidence,
    banking,
)
