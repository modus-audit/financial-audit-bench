"""Employee and structural-profile samplers."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    person_name,
)


def _sample_employee(self, world: World) -> list[dict[str, Any]]:
    """Allocate an accounted payroll envelope under the admitted policy."""
    from financial_audit_bench.synthetic_binders.priors.graph.domains.payroll.policy import (
        admitted_payroll_roster_policy,
        allocate_payroll_envelope,
        allocate_roster_roles,
        payroll_envelope_from_ratio,
        payroll_revenue_denominator,
    )

    policy = admitted_payroll_roster_policy(self.business_type)
    raw_p2r = (world.get("operating_scale") or {}).get("payroll_to_revenue")
    p2r = raw_p2r
    if raw_p2r not in (None, ""):
        raw_ratio = Decimal(str(raw_p2r))
        low, high = (Decimal(str(value)) for value in policy["payroll_share_band"])
        p2r = str(min(max(raw_ratio, low), high))
    tax = self._ratio("ratio.payroll_tax_to_wages")
    from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_scale import (
        subledger_scale,
    )

    operating_revenue = subledger_scale(
        world["company_context"],
        world["company_feature_profile"],
        world["fiscal_calendar"],
        world.get("operating_scale"),
    )["annual_sales"]
    annual_revenue = payroll_revenue_denominator(operating_revenue)
    if p2r in (None, "") or tax is None or annual_revenue <= 0:
        return []
    try:
        payroll_ratio = Decimal(str(p2r))
        tax_ratio = Decimal(str(tax))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise ValueError("employee payroll inputs are not valid decimals") from exc
    if (
        not payroll_ratio.is_finite()
        or not tax_ratio.is_finite()
        or payroll_ratio <= 0
        or tax_ratio < 0
        or tax_ratio > 1
    ):
        raise ValueError("employee payroll inputs violate the accounted contract")

    target_payroll = payroll_envelope_from_ratio(annual_revenue, payroll_ratio)
    low, high = policy["headcount_band"]
    count = self._rng("employee.roster.policy").randint(int(low), int(high))
    plan = allocate_roster_roles(tuple(policy["roles"]), count)
    weighted_ids = [
        (f"EMP-{index:03d}", Decimal(str(weight)))
        for index, (_title, weight) in enumerate(plan, start=1)
    ]
    allocations = allocate_payroll_envelope(target_payroll, weighted_ids)
    fiscal_start = str(world["fiscal_calendar"]["start_date"])
    rows: list[dict[str, Any]] = []
    staffing_worker_roles = {
        "warehouse_associate",
        "production_associate",
        "administrative_associate",
        "customer_service_representative",
        "forklift_operator",
    }
    for index, (title, _weight) in enumerate(plan, start=1):
        employee_id = f"EMP-{index:03d}"
        role = title.lower().replace(" ", "_")
        employee_class = (
            "assigned_worker"
            if self.business_type == "staffing_services"
            and role in staffing_worker_roles
            else (
                "internal_staff"
                if self.business_type == "staffing_services"
                else str(policy["employee_class"])
            )
        )
        direct_staffing_cost = employee_class == "assigned_worker"
        rows.append(
            {
                "employee_id": employee_id,
                "full_name": person_name(
                    f"{self.seed}|{self.business_type}|employee", index
                ),
                "role": role,
                "employment_status": "active",
                "department": policy["department"],
                "pay_basis": policy["employment_basis"],
                "annual_salary": str(allocations[employee_id]),
                "authorized_rate": str(allocations[employee_id]),
                "compensation_effective_date": fiscal_start,
                "authorization_reference": f"HR-AUTH-{employee_id}-HIRE",
                "hire_date": fiscal_start,
                "termination_date": None,
                "raise_effective_date": None,
                "raise_pct": None,
                "pay_periods_per_year": str(policy["pay_periods_per_year"]),
                "employer_tax_rate": str(tax_ratio),
                "wage_gl_account_id": (
                    "GL-COGS-001" if direct_staffing_cost else "GL-WAGES-001"
                ),
                "payroll_tax_expense_gl_account_id": (
                    "GL-COGS-001" if direct_staffing_cost else "GL-PAYROLL-TAX-001"
                ),
                "employee_class": employee_class,
                "termination_reason": policy["termination_reason"],
                "employment_basis": policy["employment_basis"],
            }
        )
    salary_total = sum(
        (Fraction(Decimal(row["annual_salary"])) for row in rows),
        Fraction(0),
    )
    if salary_total != Fraction(target_payroll):
        raise ValueError("employee roster does not reconcile to payroll envelope")
    return rows


def _sample_company_feature_profile(self, world: World) -> dict[str, Any]:
    """Return the deterministic structural profile for this graph type."""
    from financial_audit_bench.synthetic_binders.priors.graph_construction.authored.core_company_cash import (
        company_feature_profile,
    )

    profile = dict(company_feature_profile(world))
    return profile
