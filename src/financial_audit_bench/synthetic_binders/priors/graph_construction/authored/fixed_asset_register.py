"""Construct the target opening fixed-asset registers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)

_POLICY = load_authored_policy("authored.fixed-assets.opening-register.v1").values[
    "policy"
]


def _fixed_asset_location(
    policy: Mapping[str, Any],
    description: str,
    asset_class: str,
) -> str:
    lowered = description.lower()
    for rule in policy["operating_location_rules"]:
        classes = tuple(str(item) for item in rule["classes"])
        tokens = tuple(str(item) for item in rule["tokens"])
        if (classes and asset_class in classes) or (
            tokens and any(token in lowered for token in tokens)
        ):
            return str(rule["value"])
        if not classes and not tokens:
            return str(rule["value"])
    raise ValueError("fixed-asset location policy has no fallback")


def _whole_month_accumulated_depreciation(
    cost: str,
    placed_in_service: date,
    useful_life_years: int,
    fiscal_year: int,
) -> str:
    """Apply the synthetic policy's zero-salvage, full-month book convention."""
    if useful_life_years <= 0:
        return "0.00"
    opening = date(int(fiscal_year), 1, 1)
    elapsed_months = max(
        0,
        (opening.year - placed_in_service.year) * 12
        + opening.month
        - placed_in_service.month,
    )
    life_months = useful_life_years * 12
    whole, separator, fraction = str(cost).partition(".")
    if (
        separator != "."
        or not whole.isdigit()
        or len(fraction) != 2
        or not fraction.isdigit()
    ):
        raise ValueError("fixed-asset cost must be a nonnegative cents string")
    cost_cents = int(whole) * 100 + int(fraction)
    numerator = cost_cents * min(elapsed_months, life_months)
    # Integer-only half-up rounding is independent of the ambient Decimal
    # context and cannot produce NaN/Infinity.
    accumulated_cents = (2 * numerator + life_months) // (2 * life_months)
    accumulated_cents = min(accumulated_cents, cost_cents)
    return f"{accumulated_cents // 100}.{accumulated_cents % 100:02d}"


def _fixed_asset_row(
    policy: Mapping[str, Any],
    number: int,
    asset_class: str,
    description: str,
    cost: str,
    placed_in_service: date,
    useful_life_years: int,
    fiscal_year: int,
    *,
    location: str,
    responsible_manager: str,
) -> dict[str, Any]:
    depreciable = useful_life_years > 0
    defaults = policy["row_defaults"]
    policy_id = str(policy["policy_id"])
    row = {
        "accumulated_depreciation_gl_account_id": (
            str(defaults["accumulated_depreciation_gl_account_id"])
            if depreciable
            else None
        ),
        "asset_class": asset_class,
        "company_id": str(defaults["company_id"]),
        "cost_gl_account_id": str(defaults["cost_gl_account_id"]),
        "depreciation_expense_gl_account_id": (
            str(defaults["depreciation_expense_gl_account_id"]) if depreciable else None
        ),
        "depreciation_method": "straight_line" if depreciable else "none",
        "description": description,
        "fixed_asset_id": f"FIXED-ASSET-{number:03d}",
        "location": location,
        "opening_cost": cost,
        "opening_accumulated_depreciation": (
            _whole_month_accumulated_depreciation(
                cost, placed_in_service, useful_life_years, fiscal_year
            )
            if depreciable
            else "0.00"
        ),
        "operating_status": str(defaults["operating_status"]),
        "placed_in_service_date": placed_in_service.isoformat(),
        "responsible_manager": responsible_manager,
        "serial_number": None,
        "serial_number_applicability": "not_applicable",
        "serial_number_source": "",
        "useful_life_years": str(useful_life_years),
        "useful_life_support_reference": policy_id,
        "cost_subaccount": None,
        "accumulated_depreciation_subaccount": None,
    }
    return row


# The operating register is built independently for each (archetype, business type,
# fiscal year). Relabeling or proportionally scaling a shared numeric register is
# deliberately not an allowed operation.


def _construct_fixed_asset_register(
    business_type: str,
    fiscal_year: int,
) -> list[dict[str, Any]]:
    """Construct the row set authorized by the fixed-asset policy."""
    policy = _POLICY
    business_type = str(business_type)
    year = int(fiscal_year)
    defaults = policy["row_defaults"]
    rows: list[dict[str, Any]] = []

    try:
        specifications = policy["specifications_by_business_type"][business_type]
    except KeyError as error:
        raise ValueError(
            f"fixed-asset policy does not admit business type {business_type!r}"
        ) from error
    for number, specification in enumerate(specifications, start=1):
        (
            asset_class,
            description,
            cost,
            years_before,
            service_month,
            life,
        ) = specification
        row = _fixed_asset_row(
            policy,
            int(number),
            str(asset_class),
            str(description),
            f"{cost}.00",
            date(year - int(years_before), int(service_month), 1),
            int(life),
            year,
            location=_fixed_asset_location(policy, str(description), str(asset_class)),
            responsible_manager=str(defaults["operating_manager"]),
        )
        row["serial_number_source"] = "synthetic_policy"
        rows.append(row)
    return rows


def build_fixed_asset_register(
    business_type: str,
    fiscal_year: int,
) -> list[dict[str, Any]]:
    """Build the target opening register."""
    return _construct_fixed_asset_register(
        business_type,
        int(fiscal_year),
    )
