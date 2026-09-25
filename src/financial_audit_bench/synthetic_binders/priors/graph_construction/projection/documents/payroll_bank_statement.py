"""Payroll bank account statements: one per month with payroll activity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.monthly_bank_statement import (
    _business_day,
    write_statement_body,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)
from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    month_end,
)


def _applicable(world: World) -> bool:
    return bool(world.get("payroll_bank_account"))


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    accounts = world.get("payroll_bank_account") or []
    runs = world.get("payroll_run") or []
    if not accounts or not runs:
        return []
    account = accounts[0]
    identity = package_identity(world)
    # The payroll account's own number, masked per the bank's persona
    # (same convention its operating statement uses).
    last_four = account["masked_account_number"][-4:]
    full_number = f"000000{last_four}"
    mask = identity.bank["mask"]
    if mask == "full":
        display = full_number
    elif mask == "x6":
        display = "XXXXXX" + last_four
    else:
        display = "****" + last_four
    operating = world.get("bank_account") or {}
    operating = operating[0] if isinstance(operating, list) else operating
    funding_source = (
        f"Transfer from Operating Chk ****{operating.get('last_four_digits', '')}"
    )

    by_month: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_month.setdefault(run["pay_date"][:7], []).append(run)
    entries = []
    # One year-end statement plus the subsequent-period statement below is
    # sufficient support for the streamlined public binder.
    current_months = sorted(by_month.items())[-1:]
    for month_key, month_runs in current_months:
        year, month = (int(part) for part in month_key.split("-"))
        period_start = date(year, month, 1)
        period_end = month_end(period_start)
        rows = []
        for run in month_runs:
            net = Decimal(run["gross_wages"]) - Decimal(
                run.get("employee_withholding", "0.00")
            )
            when = _business_day(run["pay_date"])
            rows.append(
                {
                    "date": when,
                    "description": funding_source,
                    "amount": net,
                    "is_check": False,
                }
            )
            rows.append(
                {
                    "date": when,
                    "description": "ACH Batch - Payroll Net Pay",
                    "amount": -net,
                    "is_check": False,
                }
            )
        doc = TextDoc()
        write_statement_body(
            doc,
            world,
            identity,
            period_start.isoformat(),
            period_end.isoformat(),
            "0.00",
            "0.00",
            rows,
            product="Payroll Checking",
            account_display=display,
        )
        stamp = period_start.strftime("%b%y").upper()
        path = output_dir / "bank_statements" / f"{stamp} Payroll Stmt.txt"
        entries.append(
            entry(
                f"DOC-PAYROLL-STATEMENT-{month_key}",
                "payroll",
                path,
                doc.write(path),
            )
        )

    subsequent = next(
        (
            row
            for row in world.get("payroll_subsequent_event") or []
            if row.get("event_type")
            == "subsequent_regular_payroll_and_accrual_reversal"
        ),
        None,
    )
    if subsequent:
        settlement = date.fromisoformat(str(subsequent["settlement_date"]))
        period_start = date(settlement.year, settlement.month, 1)
        period_end = month_end(period_start)
        amount = Decimal(str(subsequent["settlement_amount"]))
        rows = [
            {
                "date": settlement.isoformat(),
                "description": funding_source,
                "amount": amount,
                "is_check": False,
            },
            {
                "date": settlement.isoformat(),
                "description": (
                    "ACH Batch - Subsequent Payroll Net Pay "
                    f"{subsequent['bank_eft_reference']}"
                ),
                "amount": -amount,
                "is_check": False,
            },
        ]
        doc = TextDoc()
        write_statement_body(
            doc,
            world,
            identity,
            period_start.isoformat(),
            period_end.isoformat(),
            "0.00",
            "0.00",
            rows,
            product="Payroll Checking",
            account_display=display,
        )
        stamp = period_start.strftime("%b%y").upper()
        path = output_dir / "bank_statements" / f"{stamp} Payroll Stmt.txt"
        entries.append(
            entry(
                "PAY-05",
                "payroll",
                path,
                doc.write(path),
            )
        )
    return entries
