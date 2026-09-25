"""Journal samples and account, user, and entry finalizers."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.contracts.journal_risk import (
    apply_accounting_user_access_rights,
    apply_journal_risk_flags,
    assign_stable_public_journal_ids,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.presentation import (
    enrich_general_ledger_accounts,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

REGISTRY.sample(
    "accounting_user",
    inputs=("company_context",),
    rule_name="derive_accounting_users",
)


@REGISTRY.finalize("accounting_user")
def finalize_accounting_user_access_rights(world: dict[str, Any]) -> None:
    apply_accounting_user_access_rights(world.get("accounting_user") or [])


@REGISTRY.finalize("journal_entry")
def classify_journal_entry_risk_strata(world: dict[str, Any]) -> None:
    """Store mutually intelligible JE risk flags instead of one adjustment flag."""
    assign_stable_public_journal_ids(world)
    apply_journal_risk_flags(
        world.get("journal_entry") or [], str(world["fiscal_calendar"]["end_date"])
    )


@REGISTRY.finalize("general_ledger_account")
def finalize_general_ledger_accounts(world: dict[str, Any]) -> None:
    enrich_general_ledger_accounts(world["general_ledger_account"])
