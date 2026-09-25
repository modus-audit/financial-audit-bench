"""Payroll liability allocation and settlement evidence."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    add_business_days,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    ROLE_ORGANIZATIONS,
)


def _deposit_date(pay_date: str) -> date:
    """Return the semiweekly EFTPS deposit date for a pay date."""
    when = date.fromisoformat(pay_date)
    target = 2 if when.weekday() in (2, 3, 4) else 4
    due = when + timedelta(days=(target - when.weekday()) % 7 or 7)
    return add_business_days(due - timedelta(days=1), 1)


@REGISTRY.rule(
    "derive_payroll_remittances",
    inputs=(
        "payroll_run",
        "fiscal_calendar",
    ),
    outputs="payroll_remittance",
    gate="has_payroll",
)
def derive_payroll_remittances(
    runs: list[dict[str, Any]],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    year_end = date.fromisoformat(str(calendar["end_date"]))
    rows: list[dict[str, Any]] = []
    ordered_runs = sorted(runs, key=lambda row: row["period_end_date"])
    for run in ordered_runs:
        run_id = str(run["payroll_run_id"])
        pay_date = date.fromisoformat(str(run["pay_date"]))
        deposit_date = _deposit_date(str(run["pay_date"]))
        quarter = (int(str(run["period_end_date"])[5:7]) - 1) // 3 + 1

        def add(
            suffix: str,
            remittance_type: str,
            amount: Decimal,
            provider: str,
            settlement_date: date,
            reference: str,
            *,
            bank_reference: str | None = None,
        ) -> None:
            if amount <= 0:
                return
            rows.append(
                {
                    "payroll_remittance_id": f"REMIT-{run_id}-{suffix}",
                    "payroll_run_id": run_id,
                    "remittance_type": remittance_type,
                    "liability_period_end": run["period_end_date"],
                    "due_date": settlement_date.isoformat(),
                    "settlement_date": settlement_date.isoformat(),
                    "amount": str(amount),
                    "bank_eft_reference": bank_reference or f"EFT-{run_id}-{suffix}",
                    "return_or_provider_reference": reference,
                    "provider": provider,
                    "settlement_status": (
                        "settled_subsequent"
                        if settlement_date > year_end
                        else "settled"
                    ),
                }
            )

        gross = Decimal(str(run["gross_wages"]))
        withheld = Decimal(str(run.get("employee_withholding") or "0"))
        voluntary = Decimal("0.00")
        statutory = withheld
        add(
            "NET",
            "employee_net_pay",
            gross - withheld,
            ROLE_ORGANIZATIONS["payroll_clearing_bank"],
            pay_date,
            f"ACH-BATCH-{run_id}",
        )
        add(
            "WH",
            "employee_tax_withholding",
            statutory,
            "Federal and State Revenue Authorities",
            deposit_date,
            f"PAYROLL-TAX-RETURN-Q{quarter}-{year_end.year}",
        )
        add(
            "DED",
            "employee_voluntary_deduction",
            voluntary,
            "Employee Benefit and Garnishment Payees",
            deposit_date,
            f"PAYROLL-DEDUCTION-REMIT-{run_id}",
            bank_reference=f"EFT-{run_id}-DED",
        )
        add(
            "TAX",
            "employer_payroll_tax",
            Decimal(str(run.get("employer_payroll_tax") or "0")),
            "Federal and State Revenue Authorities",
            deposit_date,
            f"PAYROLL-TAX-RETURN-Q{quarter}-{year_end.year}",
        )
    return rows
