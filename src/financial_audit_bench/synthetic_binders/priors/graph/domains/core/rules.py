"""Privacy-boundary registrations for the retained core graph."""

from __future__ import annotations

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.finalize("fiscal_calendar")
def finalize_inventory_observation_date(world: dict) -> None:
    calendar = world["fiscal_calendar"]
    engagement_calendar = (world.get("engagement_profile") or {}).get(
        "engagement_calendar"
    ) or {}
    calendar["inventory_observation_date"] = (
        engagement_calendar.get("milestones") or {}
    ).get("inventory_observation_date") or calendar["end_date"]


REGISTRY.sample(
    "company_context",
)
REGISTRY.sample(
    "company_feature_profile",
    inputs=("company_context",),
    rule_name="sample_company_feature_profile",
)
REGISTRY.sample(
    "general_ledger_account",
    inputs=("company_context",),
    rule_name="general_ledger_account_population",
)
REGISTRY.sample(
    "operating_scale",
    inputs=("company_feature_profile",),
    rule_name="sample_operating_scale",
)
