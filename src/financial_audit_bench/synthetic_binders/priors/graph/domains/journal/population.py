"""Final journal-entry and journal-line population enrichment."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.contracts.journal_risk import (
    apply_journal_risk_flags,
    assign_stable_journal_line_ids,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.journal.enrichment import (
    enrich_journal_population,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


@REGISTRY.finalize("journal_entry_line")
def finalize_journal_population(world: dict[str, Any]) -> None:
    enrich_journal_population(
        world["journal_entry"], world["journal_entry_line"], world
    )
    # Description enrichment can refine an estimate classification. Reapply the
    # independent flags, then assign immutable line identities before any control,
    # selection, or projection consumes this population.
    apply_journal_risk_flags(
        world["journal_entry"], str(world["fiscal_calendar"]["end_date"])
    )
    assign_stable_journal_line_ids(world)
