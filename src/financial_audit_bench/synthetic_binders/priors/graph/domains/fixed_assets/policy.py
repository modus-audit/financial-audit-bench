"""Register the opening fixed-asset population and target class mappings."""

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


REGISTRY.sample(
    "fixed_asset",
    inputs=("company_context", "fiscal_calendar"),
    gate="has_fixed_assets",
    rule_name="fixed_asset_population",
)


def _class_subaccounts(asset_class: str) -> tuple[str, str]:
    return {
        "machinery": (
            "1090-10 - Machinery",
            "1100-10 - Accumulated depreciation - machinery",
        ),
        "equipment": (
            "1090-20 - Equipment",
            "1100-20 - Accumulated depreciation - equipment",
        ),
        "vehicle": (
            "1090-30 - Vehicles",
            "1100-30 - Accumulated depreciation - vehicles",
        ),
        "leasehold_improvement": (
            "1090-40 - Leasehold improvements",
            "1100-40 - Accumulated depreciation - leasehold improvements",
        ),
        "furnishings_fixtures": (
            "1090-70 - Furnishings and fixtures",
            "1100-70 - Accumulated depreciation - furnishings and fixtures",
        ),
    }[asset_class]
