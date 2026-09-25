"""Admitted payroll policy and deterministic roster allocation."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
    to_mutable_json,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.common import (
    CENT,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

REGISTRY.sample(
    "employee",
    # The operating-scale node owns the accounted payroll ratio; the revenue schedules
    # provide its denominator. None authorizes a fixed employee population, employee-
    # level compensation template, or missing-ratio fallback.
    inputs=(
        "operating_scale",
        # The calendar dates explicit or separately accounted roster events.
        "fiscal_calendar",
    ),
    gate="has_payroll",
    rule_name="sample_employees",
)

_WORKFORCE = to_mutable_json(
    load_authored_policy("authored.business-types.workforce.v1").values
)
_ROSTER_DEFAULTS = _WORKFORCE["roster_defaults"]


def admitted_payroll_roster_policy(business_type: str) -> dict[str, Any]:
    """Return the admitted fictional roster policy for ``business_type``.

    Both supported business types have exact rows in every required table.
    """
    record = _ROSTER_DEFAULTS
    from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
        PUBLIC_BUSINESS_TYPES,
    )

    if business_type not in PUBLIC_BUSINESS_TYPES:
        raise ValueError(f"no exact admitted payroll policy for {business_type!r}")

    missing = [
        table
        for table, values in (
            ("employee roles", _WORKFORCE["employee_roles"]),
            ("headcount band", _WORKFORCE["headcount_band"]),
            ("payroll share band", _WORKFORCE["payroll_share_band"]),
            ("pay cadence", _WORKFORCE["pay_periods"]),
        )
        if business_type not in values
    ]
    if missing:
        raise ValueError(
            f"no exact admitted payroll policy for {business_type!r}: "
            + ", ".join(missing)
        )

    roles = tuple(_WORKFORCE["employee_roles"][business_type])
    headcount_low, headcount_high = _WORKFORCE["headcount_band"][business_type]
    payroll_share_low, payroll_share_high = _WORKFORCE["payroll_share_band"][
        business_type
    ]
    periods = int(_WORKFORCE["pay_periods"][business_type])
    return {
        **record,
        "roles": roles,
        "headcount_band": (int(headcount_low), int(headcount_high)),
        "payroll_share_band": (payroll_share_low, payroll_share_high),
        "pay_periods_per_year": periods,
    }


def allocate_payroll_envelope(
    total: Decimal, weighted_ids: list[tuple[str, Decimal]]
) -> dict[str, Decimal]:
    """Allocate an aggregate payroll envelope exactly, without salary jitter."""
    if not total.is_finite() or total <= 0:
        raise ValueError("payroll envelope must be a positive whole-cent amount")
    total_minor_units = Fraction(total) / Fraction(CENT)
    if total_minor_units.denominator != 1:
        raise ValueError("payroll envelope must be a positive whole-cent amount")
    identifiers = [str(identifier) for identifier, _weight in weighted_ids]
    parsed_weights: list[Fraction] = []
    for _identifier, weight in weighted_ids:
        try:
            parsed = Decimal(str(weight))
        except ArithmeticError as error:
            raise ValueError("payroll allocation received an invalid weight") from error
        if not parsed.is_finite() or parsed <= 0:
            raise ValueError("payroll allocation requires positive finite weights")
        parsed_weights.append(Fraction(parsed))
    if (
        not identifiers
        or len(set(identifiers)) != len(identifiers)
        or any(not identifier for identifier in identifiers)
    ):
        raise ValueError("payroll allocation requires unique IDs and positive weights")

    total_cents = total_minor_units.numerator
    total_weight = sum(parsed_weights, Fraction(0))
    raw_cents = {
        identifier: Fraction(total_cents) * weight / total_weight
        for identifier, weight in zip(identifiers, parsed_weights, strict=True)
    }
    allocated_cents = {
        identifier: amount.numerator // amount.denominator
        for identifier, amount in raw_cents.items()
    }
    residual = total_cents - sum(allocated_cents.values())
    order = sorted(
        identifiers,
        key=lambda identifier: (
            -(raw_cents[identifier] - allocated_cents[identifier]),
            identifier,
        ),
    )
    for identifier in order[:residual]:
        allocated_cents[identifier] += 1
    if sum(allocated_cents.values()) != total_cents:
        raise ValueError("payroll envelope allocation does not reconcile")
    result = {
        identifier: Decimal(f"{cents // 100}.{cents % 100:02d}")
        for identifier, cents in allocated_cents.items()
    }
    return result


def _as_finite_fraction(value: Any, label: str) -> Fraction:
    """Parse one finite decimal without consulting ambient precision."""
    try:
        decimal_value = Decimal(str(value))
    except (ArithmeticError, ValueError) as error:
        raise ValueError(f"{label} is not a valid decimal") from error
    if not decimal_value.is_finite():
        raise ValueError(f"{label} must be finite")
    return Fraction(decimal_value)


def _minor_units_decimal(minor_units: int) -> Decimal:
    """Construct an exact two-place Decimal without context arithmetic."""
    if minor_units < 0:
        raise ValueError("minor-unit amount must not be negative")
    return Decimal(f"{minor_units // 100}.{minor_units % 100:02d}")


def _round_fraction_to_cent_half_even(amount: Fraction) -> Decimal:
    """Round a nonnegative exact rational to cents with ties-to-even."""
    if amount < 0:
        raise ValueError("monetary amount must not be negative")
    minor_units = amount / Fraction(CENT)
    quotient, remainder = divmod(minor_units.numerator, minor_units.denominator)
    comparison = remainder * 2 - minor_units.denominator
    if comparison > 0 or (comparison == 0 and quotient % 2):
        quotient += 1
    return _minor_units_decimal(quotient)


def payroll_revenue_denominator(
    operating_revenue: Any | None,
) -> Decimal:
    """Convert the target company's operating-revenue scale to exact cents."""
    if operating_revenue in (None, ""):
        return Decimal("0.00")
    annual = _as_finite_fraction(operating_revenue, "operating revenue")
    return _round_fraction_to_cent_half_even(annual)


def payroll_envelope_from_ratio(
    annual_revenue: Decimal, payroll_ratio: Decimal
) -> Decimal:
    """Apply the accounted payroll ratio with exact half-even cent rounding."""
    revenue = _as_finite_fraction(annual_revenue, "annual revenue")
    ratio = _as_finite_fraction(payroll_ratio, "payroll-to-revenue ratio")
    if revenue <= 0 or ratio <= 0:
        raise ValueError("payroll envelope inputs must be positive")
    envelope = _round_fraction_to_cent_half_even(revenue * ratio)
    if envelope <= 0:
        raise ValueError("payroll envelope rounds below one minor unit")
    return envelope


def allocate_roster_roles(
    roles: tuple[tuple[str, str, str], ...], count: int
) -> list[tuple[str, str]]:
    """Expand admitted role rules into exactly ``count`` deterministic rows."""
    if count < 1 or not roles:
        raise ValueError("roster role allocation requires roles and headcount")
    fixed: list[tuple[str, str]] = []
    shares: list[tuple[int, str, str, Fraction]] = []
    for index, (title, weight, rule) in enumerate(roles):
        role_title = str(title)
        role_weight = str(weight)
        token = str(rule)
        try:
            parsed_weight = Decimal(role_weight)
        except ArithmeticError as error:
            raise ValueError(
                "roster role allocation received an invalid role"
            ) from error
        if not role_title or not parsed_weight.is_finite() or parsed_weight <= 0:
            raise ValueError("roster role allocation received an invalid role")
        if token == "singleton":
            fixed.append((role_title, role_weight))
        elif token.startswith("few:"):
            try:
                quantity = int(token.removeprefix("few:"))
            except ValueError as error:
                raise ValueError("invalid fixed roster role rule") from error
            if quantity < 1:
                raise ValueError("invalid fixed roster role quantity")
            fixed.extend([(role_title, role_weight)] * quantity)
        elif token.startswith("share:"):
            try:
                share_value = Decimal(token.removeprefix("share:"))
            except ArithmeticError as error:
                raise ValueError("invalid roster role share") from error
            if not share_value.is_finite() or share_value <= 0:
                raise ValueError("invalid roster role share")
            shares.append((index, role_title, role_weight, Fraction(share_value)))
        else:
            raise ValueError("unknown roster role allocation rule")

    if len(fixed) > count:
        raise ValueError("headcount is smaller than the required fixed roles")
    remaining = count - len(fixed)
    if remaining == 0:
        return fixed
    if not shares:
        raise ValueError("roster policy has no share roles for remaining headcount")

    total_share = sum((share for _index, _title, _weight, share in shares), Fraction(0))
    raw = [Fraction(remaining) * share / total_share for *_prefix, share in shares]
    quantities = [value.numerator // value.denominator for value in raw]
    residual = remaining - sum(quantities)
    order = sorted(
        range(len(shares)),
        key=lambda item: (-(raw[item] - quantities[item]), shares[item][0]),
    )
    for item in order[:residual]:
        quantities[item] += 1

    result = list(fixed)
    for (_index, title, weight, _share), quantity in zip(
        shares, quantities, strict=True
    ):
        result.extend([(title, weight)] * quantity)
    if len(result) != count:
        raise ValueError("roster role allocation does not reconcile to headcount")
    return result
