"""Post-year-end payroll register, reversal, and remittance bridge."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
    titled_doc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)


def _applicable(world: World) -> bool:
    return bool(world.get("payroll_subsequent_event"))


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    events = sorted(
        world.get("payroll_subsequent_event") or [],
        key=lambda row: (
            str(row["settlement_date"]),
            str(row["payroll_subsequent_event_id"]),
        ),
    )
    if not events:
        return []
    client_ids = client_id_map(world)
    year = int(world["fiscal_calendar"]["fiscal_year"])
    payroll_event = next(
        row
        for row in events
        if row["event_type"] == "subsequent_regular_payroll_and_accrual_reversal"
    )
    document = titled_doc("SUBSEQUENT PAYROLL AND ACCRUAL REVERSAL REPORT", world)
    document.center(
        f"First payroll following {fmt_date(world['fiscal_calendar']['end_date'])}"
    )
    document.line()
    document.pair(
        "Source year-end payroll run",
        client_ids.get(
            payroll_event["source_payroll_run_id"],
            payroll_event["source_payroll_run_id"],
        ),
    )
    document.pair(
        "Subsequent payroll period",
        f"{fmt_date(payroll_event['period_start_date'])} through "
        f"{fmt_date(payroll_event['period_end_date'])}",
    )
    document.pair("Subsequent payment date", fmt_date(payroll_event["settlement_date"]))
    document.tie(
        "subsequent_payroll_gross",
        "Subsequent regular payroll gross",
        fmt_amount(payroll_event["gross_amount"]),
    )
    document.tie(
        "subsequent_payroll_net",
        "Subsequent payroll net payment",
        fmt_amount(payroll_event["settlement_amount"]),
    )
    document.tie(
        "year_end_wage_accrual_reversed",
        "Year-end earned-but-unpaid wage accrual reversed",
        fmt_amount(payroll_event["year_end_accrual_amount"]),
    )
    document.pair("Accrual reversal reference", payroll_event["reversal_reference"])
    document.pair("Payroll bank/EFT reference", payroll_event["bank_eft_reference"])
    document.pair(
        "Subsequent register reference", payroll_event["remittance_reference"]
    )
    document.line()
    settlements = [row for row in events if row is not payroll_event]
    document.line("YEAR-END LIABILITY SETTLEMENTS")
    if settlements:
        document.table(
            [
                "Source run",
                "Type",
                "Settlement",
                "Amount",
                "Bank / EFT reference",
                "Return / provider reference",
            ],
            [
                [
                    client_ids.get(
                        row["source_payroll_run_id"],
                        row["source_payroll_run_id"],
                    ),
                    str(row["event_type"]).replace("_", " ").title(),
                    fmt_date(row["settlement_date"]),
                    fmt_amount(row["settlement_amount"]),
                    row["bank_eft_reference"],
                    row["remittance_reference"],
                ]
                for row in settlements
            ],
            [15, 25, 12, 14, 24, 27],
        )
    else:
        document.line(
            "No payroll-tax or benefit liabilities remained unsettled at year end."
        )
    document.line()
    document.tie(
        "subsequent_liability_settlement_total",
        "Year-end tax and benefit liabilities settled subsequently",
        fmt_amount(
            sum(
                (Decimal(str(row["settlement_amount"])) for row in settlements),
                Decimal("0.00"),
            )
        ),
    )
    document.wrapped(
        "The subsequent payroll register identifies the wage-accrual reversal. "
        "The separately issued payroll-bank statement and settlement report "
        "provide independent amount and date evidence for net pay, taxes, and "
        "benefit remittances."
    )
    path = output_dir / "payroll" / f"Subsequent Payroll Report {year + 1}.txt"
    return [entry("PAY-05", "payroll", path, document.write(path))]
