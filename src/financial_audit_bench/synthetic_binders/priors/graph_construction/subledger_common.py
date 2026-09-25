"""Stateless deterministic primitives shared by subledger lifecycles."""

from __future__ import annotations

import random
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any


CENT = Decimal("0.01")


def _terms_days(terms: str) -> int:
    return int(terms.split()[1])


class _SyntheticDraws:
    """Small deterministic PRNG adapter used by inventory generation."""

    __slots__ = ("_random",)

    def __init__(
        self,
        company: dict[str, Any],
        calendar: dict[str, Any],
        domain: str,
    ) -> None:
        seed = "|".join(
            (
                "inventory-random-v1",
                domain,
                str(company["legal_name"]),
                str(calendar["end_date"]),
            )
        )
        self._random = random.Random(seed)

    def unit(self, label: str, subcounter: int = 0) -> Decimal:
        del label, subcounter
        return Decimal(str(self._random.random()))

    def uniform(self, label: str, low: Any, high: Any) -> Decimal:
        low_decimal = Decimal(str(low))
        high_decimal = Decimal(str(high))
        if high_decimal < low_decimal:
            raise ValueError("synthetic uniform interval is reversed")
        return low_decimal + (high_decimal - low_decimal) * self.unit(label)

    def randint(self, label: str, low: int, high: int) -> int:
        if type(low) is not int or type(high) is not int or high < low:
            raise ValueError("synthetic integer interval is invalid")
        del label
        return self._random.randint(low, high)

    def choice(self, label: str, values: tuple[Any, ...] | list[Any]) -> Any:
        if not values:
            raise ValueError("synthetic choice requires a nonempty sequence")
        del label
        return self._random.choice(values)

    def sample(
        self,
        label: str,
        values: range | tuple[Any, ...] | list[Any],
        count: int,
    ) -> list[Any]:
        del label
        population = list(values)
        if count < 0 or count > len(population):
            raise ValueError("synthetic sample size is invalid")
        return self._random.sample(population, count)


def _synthetic_draws(
    company: dict[str, Any],
    calendar: dict[str, Any],
    domain: str,
) -> _SyntheticDraws:
    return _SyntheticDraws(company, calendar, domain)


def _rng(
    company: dict[str, str],
    calendar: dict[str, Any],
    label: str,
) -> random.Random:
    seed = f"{label}|{company['legal_name']}|{calendar['end_date']}"
    return random.Random(seed)


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _allocate_amount(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Allocate a cent-scale total with a stable largest-remainder method."""
    if not weights:
        return []
    total = Decimal(str(total))
    if not total.is_finite() or total < 0 or total != total.quantize(CENT):
        raise ValueError("allocation total must be a nonnegative cent amount")
    clean_weights = [Decimal(str(weight)) for weight in weights]
    if any(not weight.is_finite() or weight < 0 for weight in clean_weights):
        raise ValueError("allocation weights must be finite and nonnegative")
    if not any(clean_weights):
        clean_weights = [Decimal("1") for _ in clean_weights]
    weight_total = sum(clean_weights, Decimal("0"))
    total_minor = int(total * 100)
    exact_minor = [
        Decimal(total_minor) * weight / weight_total for weight in clean_weights
    ]
    allocated_minor = [
        int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact_minor
    ]
    residual = total_minor - sum(allocated_minor)
    order = sorted(
        range(len(clean_weights)),
        key=lambda index: (
            -(exact_minor[index] - Decimal(allocated_minor[index])),
            index,
        ),
    )
    for index in order[:residual]:
        allocated_minor[index] += 1
    amounts = [Decimal(value) / 100 for value in allocated_minor]
    if sum(amounts, Decimal("0")) != total or any(value < 0 for value in amounts):
        raise ValueError("allocation failed to preserve its exact total")
    return amounts


def _allocate_units(
    total: int, weights: list[Decimal], capacities: list[int] | None = None
) -> list[int]:
    """Allocate whole physical units without exceeding optional capacities."""
    if not weights:
        return []
    if total < 0:
        raise ValueError("physical-unit allocation cannot be negative")
    limits = capacities or [total for _ in weights]
    if len(limits) != len(weights) or total > sum(limits):
        raise ValueError("physical-unit allocation exceeds available capacity")
    positive_total = sum(
        (max(weight, Decimal("0")) for weight in weights), Decimal("0")
    )
    normalized = (
        [max(weight, Decimal("0")) for weight in weights]
        if positive_total
        else [Decimal("1") for _ in weights]
    )
    denominator = sum(normalized, Decimal("0"))
    exact = [Decimal(total) * weight / denominator for weight in normalized]
    allocated = [min(int(value), limits[index]) for index, value in enumerate(exact)]
    remaining = total - sum(allocated)
    order = sorted(
        range(len(weights)),
        key=lambda index: (exact[index] - int(exact[index]), normalized[index]),
        reverse=True,
    )
    while remaining:
        progressed = False
        for index in order:
            if allocated[index] >= limits[index]:
                continue
            allocated[index] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            raise ValueError("unable to allocate physical units within capacity")
    return allocated


def _is_business_day(day: date) -> bool:
    from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
        is_business_day,
    )

    return is_business_day(day)


def _receipt_business_day(day: date) -> date:
    """Customer receipts credit on business days (a Sunday ACH deposit was implausible): snap to the nearest prior business day, walking forward instead when the snap would leave the month."""
    from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
        prior_business_day,
    )

    return prior_business_day(day)
