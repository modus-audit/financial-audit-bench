"""Target-only manufacturing and staffing profile resolution."""

from __future__ import annotations

from dataclasses import dataclass

from financial_audit_bench.synthetic_binders.models import SUPPORTED_BUSINESS_TYPES

PUBLIC_BUSINESS_TYPES = SUPPORTED_BUSINESS_TYPES

_BASE_FAMILIES = frozenset(
    {
        "ap",
        "cash",
        "fixed_assets",
        "journal",
        "noise",
        "optional",
        "payroll",
        "receivables",
        "tb",
    }
)
_ARCHETYPES = {
    "manufacturing": "manufacturer",
    "staffing_services": "services",
}


@dataclass(frozen=True)
class BusinessTypeProfile:
    families: frozenset[str]
    feature_overrides: tuple[tuple[str, bool], ...]


def archetype_for(business_type: str) -> str:
    return _ARCHETYPES[business_type]


def is_manufacturing(business_type: str) -> bool:
    return business_type == "manufacturing"


def profile_for(business_type: str) -> BusinessTypeProfile:
    if business_type not in SUPPORTED_BUSINESS_TYPES:
        raise ValueError(
            f"unsupported business type {business_type!r}; "
            f"supported: {sorted(SUPPORTED_BUSINESS_TYPES)}"
        )
    families = set(_BASE_FAMILIES)
    if is_manufacturing(business_type):
        families.add("inventory")
    overrides = {
        "has_ap_activity": True,
        "has_fixed_assets": True,
        "has_inventory": "inventory" in families,
        "has_payroll": True,
        "has_accounts_receivable": True,
    }
    return BusinessTypeProfile(
        families=frozenset(families),
        feature_overrides=tuple(overrides.items()),
    )
