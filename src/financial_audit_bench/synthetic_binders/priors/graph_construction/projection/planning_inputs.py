"""Compact audit plan for the manufacturing and staffing packages."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Any


def _financial_bases(world: dict[str, Any]) -> tuple[Decimal, Decimal]:
    classes = {
        str(row["gl_account_id"]): str(row.get("account_class") or "").lower()
        for row in world.get("general_ledger_account") or []
    }
    revenue = assets = Decimal("0")
    for row in world.get("final_adjusted_trial_balance_account") or []:
        amount = abs(Decimal(str(row.get("final_adjusted_closing_balance") or "0")))
        account_class = classes.get(str(row.get("gl_account_id")), "")
        if account_class in {"revenue", "gains"}:
            revenue += amount
        elif account_class == "asset":
            assets += amount
    return revenue, assets


def _rounded_materiality(value: Decimal) -> Decimal:
    increment = Decimal("1000") if value >= Decimal("100000") else Decimal("100")
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def _revenue_policy(business_type: str) -> str:
    if business_type == "manufacturing":
        return "Recognize product revenue when control transfers on shipment."
    if business_type == "staffing_services":
        return "Recognize staffing revenue as approved employee hours are worked."
    raise ValueError(f"unsupported public-release business type: {business_type}")


def build_audit_plan(
    world: dict[str, Any], engagement_profile: dict[str, Any]
) -> dict[str, Any]:
    """Persist only planning facts consumed by the retained package."""
    company = world["company_context"]
    calendar = engagement_profile["engagement_calendar"]
    milestones = dict(calendar["milestones"])
    revenue, assets = _financial_bases(world)
    basis, benchmark = ("revenue", revenue) if revenue > 0 else ("total assets", assets)
    overall = _rounded_materiality(benchmark * Decimal("0.01"))
    performance = _rounded_materiality(overall * Decimal("0.75"))
    business_type = str(world["business_type"])
    risks = ["Revenue recognition", "Management override"]
    if business_type == "manufacturing":
        risks.append("Inventory existence and valuation")
    else:
        risks.append("Approved time, unbilled revenue, and payroll cutoff")
    return {
        "engagement": {
            "entity_name": company["legal_name"],
            "fiscal_year_end": world["fiscal_calendar"]["end_date"],
            "reporting_currency": "USD",
            "milestones": milestones,
            "calendar": calendar,
            "audit_team": {"preparer": "Audit Senior", "reviewer": "Audit Manager"},
        },
        "materiality": {
            "basis": basis,
            "benchmark_amount": f"{benchmark:.2f}",
            "overall": f"{overall:.2f}",
            "performance": f"{performance:.2f}",
        },
        "risk": {
            "audit_strategy": "fully_substantive",
            "significant_risks": risks,
        },
        "accounting_policies": {
            "revenue": _revenue_policy(business_type),
            "inventory": (
                "Weighted-average costing with year-end physical observation."
                if business_type == "manufacturing"
                else "Not applicable."
            ),
            "payroll": "Employee-level payroll records and year-end cutoff support.",
        },
        "opening_balances": {
            "prior_year_available": bool(world.get("prior_period_account_balance"))
        },
    }


def _row(
    category: str,
    input_name: str,
    value: Any,
    owner: str = "management",
    applicability: str = "applicable",
) -> dict[str, Any]:
    return {
        "category": category,
        "input": input_name,
        "value": str(value),
        "owner": owner,
        "applicability": applicability,
        "availability": "available",
        "application": "applied" if applicability == "applicable" else "informational",
    }


def planning_input_rows(
    world: dict[str, Any], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    engagement = plan["engagement"]
    materiality = plan["materiality"]
    milestones = "; ".join(
        f"{name} {value}" for name, value in engagement["milestones"].items()
    )
    policies = plan["accounting_policies"]
    auditor = "auditor"
    rows = [
        (
            "Engagement",
            "Entity and fiscal year end",
            f"{engagement['entity_name']}; {engagement['fiscal_year_end']}",
        ),
        ("Engagement", "Reporting currency", engagement["reporting_currency"]),
        ("Engagement", "Audit team", "Audit Senior; Audit Manager", auditor),
        ("Engagement", "Confirmation and testing calendar", milestones, auditor),
        (
            "Materiality",
            "Materiality benchmark",
            f"{materiality['basis']}: USD {materiality['benchmark_amount']}",
            auditor,
        ),
        (
            "Materiality",
            "Overall materiality",
            f"USD {materiality['overall']}",
            auditor,
        ),
        (
            "Materiality",
            "Performance materiality",
            f"USD {materiality['performance']}",
            auditor,
        ),
        ("Risk", "Audit strategy", plan["risk"]["audit_strategy"], auditor),
        (
            "Risk",
            "Significant risks",
            "; ".join(plan["risk"]["significant_risks"]),
            auditor,
        ),
        ("Accounting policy", "Revenue recognition", policies["revenue"]),
        ("Accounting policy", "Payroll", policies["payroll"]),
        (
            "Opening balances",
            "Prior-year balances",
            "Available"
            if plan["opening_balances"]["prior_year_available"]
            else "Not available",
        ),
        ("Scope", "Known scope limitations", "None identified", auditor),
    ]
    inventory = _row(
        "Accounting policy",
        "Inventory",
        policies["inventory"],
        applicability=(
            "applicable"
            if world["business_type"] == "manufacturing"
            else "not_applicable"
        ),
    )
    return [
        *(_row(*row) for row in rows[:10]),
        inventory,
        *(_row(*row) for row in rows[10:]),
    ]


def public_planning_input_rows(
    world: dict[str, Any], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {"planning_input_number": index, **row}
        for index, row in enumerate(planning_input_rows(world, plan), 1)
    ]
