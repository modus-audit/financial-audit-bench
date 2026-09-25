"""Scale-coherence checks shared by graph execution and seed sweeps."""

from __future__ import annotations

import sys
from decimal import Decimal

# Keep annual populations within broad ratios of revenue.
SCALE_AP_SPEND_TO_REVENUE_BAND = (Decimal("0.001"), Decimal("4.0"))
SCALE_WAGES_TO_REVENUE_BAND = (Decimal("0.02"), Decimal("2.5"))
SCALE_REVENUE_ENGINE_FLAGS = ("has_accounts_receivable",)
# Wide order-of-magnitude bounds catch scale errors without narrowing legitimate
# business variation.
SCALE_MIN_REVENUE_ANCHOR = Decimal("750000")
SCALE_AR_TO_REVENUE_BAND = (Decimal("0.005"), Decimal("0.60"))
SCALE_INVENTORY_DAYS_BAND = (Decimal("5"), Decimal("420"))
SCALE_PPE_TO_REVENUE_BAND = (Decimal("0.02"), Decimal("40"))
# Asset-heavy archetypes whose audited FS cannot plausibly carry zero PP&E.
SCALE_PPE_ARCHETYPES = frozenset({"manufacturer"})
# Minimum-population backstops complement the primary floors at draw sites.
SCALE_MIN_AR_AGING_ITEMS = 12
SCALE_MIN_INVENTORY_ITEMS = 20
# Prevent service businesses from inheriting asset-heavy profiles.
SCALE_SERVICES_PPE_TO_REVENUE_CEILING = Decimal("1.5")

# Reroll worlds that fall outside these bounds.
SCALE_GATE_ENFORCE = True


def _scale_coherence(
    trial_balance, accounts, invoices, profile
) -> tuple[dict[str, str], list[str]]:
    classes = {row["gl_account_id"]: row["account_class"] for row in accounts}
    closing = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    tb_revenue = -sum(
        (
            value
            for account, value in closing.items()
            if classes.get(account) == "revenue"
        ),
        Decimal("0.00"),
    )
    revenue = tb_revenue
    ratios: dict[str, str] = {"revenue_anchor": str(revenue)}
    violations: list[str] = []
    has_engine = any(profile.get(flag) for flag in SCALE_REVENUE_ENGINE_FLAGS)
    archetype = str(profile.get("archetype") or "")
    if not has_engine:
        if archetype:
            violations.append(
                f"no revenue engine flag set for operating archetype {archetype}"
            )
        return ratios, violations
    if revenue <= 0:
        violations.append(f"revenue anchor {revenue} with a revenue engine on")
        return ratios, violations
    if revenue < SCALE_MIN_REVENUE_ANCHOR and archetype:
        if SCALE_GATE_ENFORCE:
            violations.append(
                f"revenue anchor {revenue} below the operating floor "
                f"{SCALE_MIN_REVENUE_ANCHOR}"
            )
        else:
            print(
                f"WARN scale gate: revenue anchor {revenue} below "
                f"{SCALE_MIN_REVENUE_ANCHOR}",
                file=sys.stderr,
            )

    def band(name: str, value: Decimal, bounds: tuple[Decimal, Decimal]) -> None:
        ratios[name] = str(value.quantize(Decimal("0.000001")))
        low, high = bounds
        if not low <= value <= high:
            violations.append(f"{name} {ratios[name]} outside [{low}, {high}]")

    ap_spend = sum((Decimal(row["amount"]) for row in invoices), Decimal("0.00"))
    if ap_spend > 0:
        band("ap_spend_to_revenue", ap_spend / revenue, SCALE_AP_SPEND_TO_REVENUE_BAND)
    wage_row = next(
        (row for row in trial_balance if row["gl_account_id"] == "GL-WAGES-001"),
        None,
    )
    # Gross payroll is the relevant scale measure. Construction worlds legitimately
    # credit wages when direct labor is reclassified into job costs, so the net closing
    # expense can be near zero even though a full payroll population exists.
    wages = (
        Decimal(wage_row["period_debit"])
        if wage_row is not None
        else closing.get("GL-WAGES-001", Decimal("0.00"))
    )
    if wages > 0:
        band("wages_to_revenue", wages / revenue, SCALE_WAGES_TO_REVENUE_BAND)
    # Service-company PP&E must remain proportionate to revenue.
    if archetype == "services":
        ppe = closing.get("GL-FIXED-ASSET-001", Decimal("0.00"))
        if ppe > 0:
            band(
                "services_ppe_to_revenue",
                ppe / revenue,
                (Decimal("0"), SCALE_SERVICES_PPE_TO_REVENUE_CEILING),
            )
    return ratios, violations


def check_scale_coherence(trial_balance, accounts, invoices, profile):
    """Every monetary family's annual total is scale-coherent with the
    world's revenue anchor."""
    _, violations = _scale_coherence(trial_balance, accounts, invoices, profile)
    if not violations:
        return
    message = "world scale coherence: " + "; ".join(violations)
    if SCALE_GATE_ENFORCE:
        raise ValueError(message)
    print(f"WARN {message}", file=sys.stderr)


# -- Per-family scale and population checks -----------------------------------
# Registered per family so composition drops each check exactly when its
# family is absent. All start in WARN mode (SCALE_GATE_ENFORCE) and flip


def _scale_violation(message: str) -> None:
    if SCALE_GATE_ENFORCE:
        raise ValueError(message)
    print(f"WARN scale gate: {message}", file=sys.stderr)


def _anchor(trial_balance, accounts) -> Decimal:
    classes = {row["gl_account_id"]: row["account_class"] for row in accounts}
    tb_revenue = -sum(
        (
            Decimal(row["closing_balance"])
            for row in trial_balance
            if classes.get(row["gl_account_id"]) == "revenue"
        ),
        Decimal("0.00"),
    )
    return tb_revenue


def check_scale_ar_population(aging, trial_balance, accounts, profile):
    """Trade A/R is scale-coherent in dollars and thick enough in rows to
    audit: open aging total inside the AR-to-revenue band,
    open items above the population floor."""
    if not aging or not profile.get("has_accounts_receivable"):
        return
    revenue = _anchor(trial_balance, accounts)
    open_total = sum((Decimal(row["open_amount"]) for row in aging), Decimal("0.00"))
    if revenue > 0 and open_total > 0:
        ratio = open_total / revenue
        low, high = SCALE_AR_TO_REVENUE_BAND
        if not low <= ratio <= high:
            _scale_violation(
                f"ar_to_revenue {ratio.quantize(Decimal('0.000001'))} "
                f"outside [{low}, {high}]"
            )
    if len(aging) < SCALE_MIN_AR_AGING_ITEMS:
        _scale_violation(
            f"trade AR aging carries {len(aging)} open items "
            f"(floor {SCALE_MIN_AR_AGING_ITEMS})"
        )


def check_scale_inventory_population(items, trial_balance, accounts, profile):
    """Require an auditable SKU population and plausible inventory days."""
    if not items or not profile.get("has_inventory"):
        return
    if len(items) < SCALE_MIN_INVENTORY_ITEMS:
        _scale_violation(
            f"inventory carries {len(items)} SKUs (floor {SCALE_MIN_INVENTORY_ITEMS})"
        )
    closing = {
        row["gl_account_id"]: Decimal(row["closing_balance"]) for row in trial_balance
    }
    inventory = closing.get("GL-INVENTORY-001", Decimal("0"))
    cogs = closing.get("GL-COGS-001", Decimal("0"))
    if inventory > 0 and cogs > 0:
        days = inventory / cogs * 365
        low, high = SCALE_INVENTORY_DAYS_BAND
        if not low <= days <= high:
            _scale_violation(
                f"inventory_days {days.quantize(Decimal('0.01'))} "
                f"outside [{low}, {high}]"
            )


def check_scale_ppe(fixed_assets, trial_balance, accounts, profile):
    """Asset-heavy archetypes carry PP&E scale-coherent with the anchor —
    a wire manufacturer with zero PP&E on audited statements is implausible."""
    archetype = str(profile.get("archetype") or "")
    if archetype not in SCALE_PPE_ARCHETYPES or not profile.get("has_fixed_assets"):
        return
    revenue = _anchor(trial_balance, accounts)
    cost = sum((Decimal(row["opening_cost"]) for row in fixed_assets), Decimal("0.00"))
    if revenue <= 0:
        return
    if not fixed_assets or cost <= 0:
        _scale_violation(f"{archetype} world with has_fixed_assets on carries no PP&E")
        return
    ratio = cost / revenue
    low, high = SCALE_PPE_TO_REVENUE_BAND
    if not low <= ratio <= high:
        _scale_violation(
            f"ppe_to_revenue {ratio.quantize(Decimal('0.000001'))} "
            f"outside [{low}, {high}]"
        )
