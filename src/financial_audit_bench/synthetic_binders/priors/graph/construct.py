"""Build a synthetic financial world with the graph engine."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.prior_source import (
    ReleaseValueSource,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
    profile_for,
)
from financial_audit_bench.synthetic_binders.contracts.journal_risk import (
    refresh_journal_risk_contract,
)

# Importing domains registers their graph components. Contribution priority, not import
# order, controls merges.
from financial_audit_bench.synthetic_binders.priors.graph.domains import (  # noqa: F401
    ap,
    cash,
    core,
    families_for,
    fixed_assets,
    inventory,
    journal,
    optional,
    payroll,
    receivables,
    tb,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

_UNIVERSAL_ACCOUNTS = {
    "GL-CASH-001",
    "GL-REVENUE-001",
    "GL-EXPENSE-001",
    "GL-CAPITAL-001",
}
DEFAULT_MAX_ATTEMPTS = 32


def minimum_daily_balance(world: dict[str, Any]) -> Decimal:
    """Return the lowest end-of-day bank balance across the year."""
    opening = Decimal(world["prior_period_bank_balance"]["ending_balance"])
    by_day: dict[str, Decimal] = {}
    for row in world["cash_movement"]:
        by_day[row["bank_activity_date"]] = by_day.get(
            row["bank_activity_date"], Decimal("0")
        ) + Decimal(row["signed_amount"])
    balance = minimum = opening
    for day in sorted(by_day):
        balance += by_day[day]
        minimum = min(minimum, balance)
    return minimum


def _drop_dead_noncomposed_accounts(world: dict[str, Any]) -> None:
    """Remove chart accounts with no balance or journal-line activity."""
    balance_nodes = (
        "trial_balance_account",
        "final_adjusted_trial_balance_account",
        "prior_period_account_balance",
    )
    keep_ids: set[str] = set(_UNIVERSAL_ACCOUNTS)
    for node in balance_nodes:
        for row in world.get(node) or []:
            for field in (
                "closing_balance",
                "final_adjusted_closing_balance",
                "client_closing_balance",
            ):
                value = row.get(field)
                if value is not None and Decimal(str(value)) != 0:
                    keep_ids.add(row["gl_account_id"])
                    break
    for line in world.get("journal_entry_line") or []:
        if line.get("gl_account_id"):
            keep_ids.add(line["gl_account_id"])

    for node in (
        "general_ledger_account",
        "opening_account_balance",
        *balance_nodes,
    ):
        rows = world.get(node)
        if rows:
            world[node] = [row for row in rows if row["gl_account_id"] in keep_ids]


def construct_binder_world(
    seed: int,
    output_dir: Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    business_type: str = "manufacturing",
    engagement_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and optionally persist a validated financial world."""
    profile = profile_for(business_type)
    registry = REGISTRY.for_families(families_for(profile))
    context = {
        "feature_overrides": profile.feature_overrides,
        "business_type": business_type,
        "engagement_profile": engagement_profile or {},
    }
    if engagement_profile:
        if engagement_profile.get("business_type") != business_type:
            raise ValueError("engagement profile business type does not match request")
        if int(engagement_profile.get("seed", -1)) != seed:
            raise ValueError("engagement profile seed does not match request")
    attempts = 0
    if seed is None:
        raise ValueError("generation requires an explicit seed")
    from financial_audit_bench.synthetic_binders.priors.graph_construction.bootstrap import (
        load_release,
    )

    prior_release = load_release()
    # Reroll seeds that produce a negative daily cash balance.
    for attempts in range(1, max_attempts + 1):
        draw_seed = seed if attempts == 1 else seed + 100_000 * attempts
        prior_source = ReleaseValueSource(
            draw_seed,
            release=prior_release,
            business_type=business_type,
        )
        try:
            result = registry.run(prior_source, context=context)
        except ValueError as error:
            # Reroll scale failures; other validation errors are generator bugs.
            if "check_scale_" in str(error) and attempts < max_attempts:
                continue
            raise
        cash_is_feasible = minimum_daily_balance(result.world) >= 0
        if cash_is_feasible:
            break
    world = result.world
    # Projection needs the business type, but it is not a declared graph node.
    world["business_type"] = business_type
    _drop_dead_noncomposed_accounts(world)
    # Finalizers may change the journal, so refresh its dependent risk data.
    refresh_journal_risk_contract(world)
    minimum_bank_balance = min(
        (float(row["ending_balance"]) for row in world["bank_statement_month"]),
        default=0.0,
    )
    minimum_daily = minimum_daily_balance(world)
    summary = {
        "seed": seed,
        "business_type": business_type,
        "attempts": attempts,
        "checks": result.checks,
        "feasibility": {
            "minimum_bank_balance": f"{minimum_bank_balance:.2f}",
            "minimum_daily_balance": str(minimum_daily),
            "minimum_required_balance": "0.00",
            "cash_feasible": (minimum_bank_balance >= 0 and minimum_daily >= 0),
        },
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        serializable = {
            key: value for key, value in world.items() if not key.startswith("__")
        }
        (output_dir / "sampled_world.json").write_text(
            json.dumps(serializable, indent=1, sort_keys=True, default=str) + "\n"
        )
    return summary


if __name__ == "__main__":
    import argparse

    from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
        PUBLIC_BUSINESS_TYPES,
    )

    parser = argparse.ArgumentParser(
        description="Construct a binder world from the graph engine."
    )
    parser.add_argument("--seed", type=int, default=0, help="draw seed")
    parser.add_argument(
        "--business-type",
        choices=sorted(PUBLIC_BUSINESS_TYPES),
        default="manufacturing",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the sampled world here",
    )
    args = parser.parse_args()
    summary = construct_binder_world(
        seed=args.seed,
        output_dir=args.output,
        business_type=args.business_type,
    )
    feasibility = summary["feasibility"]
    print(
        f"seed={summary['seed']} {summary['business_type']}: "
        f"checks {summary['checks']['status']}, "
        f"cash_feasible={feasibility['cash_feasible']} "
        f"(min bank balance {feasibility['minimum_bank_balance']}, "
        f"attempts {summary['attempts']})"
    )
    if args.output:
        print(f"wrote {args.output}")
