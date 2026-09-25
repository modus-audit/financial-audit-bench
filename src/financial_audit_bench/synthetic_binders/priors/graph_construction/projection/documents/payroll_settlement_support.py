"""Bank-originated payroll settlement and remittance report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize_identity import (
    client_id_map,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)


def _applicable(world: World) -> bool:
    return bool(world.get("payroll_run"))


def render(world: World, output_dir: Path) -> list[dict[str, object]]:
    remittances = sorted(
        world.get("payroll_remittance") or [],
        key=lambda row: (
            str(row["settlement_date"]),
            str(row["payroll_run_id"]),
            str(row["remittance_type"]),
        ),
    )
    if not remittances:
        return []

    year = int(world["fiscal_calendar"]["fiscal_year"])
    client_ids = client_id_map(world)
    document = TextDoc()
    document.center("PAYROLL BANK AND REMITTANCE SETTLEMENT REPORT")
    document.center(f"For payroll liabilities arising in {year}")
    document.rule("=")
    document.table(
        [
            "Run",
            "Period end",
            "Settlement",
            "Type",
            "Provider",
            "Amount",
            "EFT reference",
        ],
        [
            [
                client_ids.get(row["payroll_run_id"], row["payroll_run_id"]),
                fmt_date(row["liability_period_end"]),
                fmt_date(row["settlement_date"]),
                str(row["remittance_type"]).replace("_", " ").title(),
                row["provider"],
                fmt_amount(row["amount"]),
                row["bank_eft_reference"],
            ]
            for row in remittances
        ],
        [13, 12, 12, 22, 28, 14, 24],
    )
    document.line()
    for kind in sorted({str(row["remittance_type"]) for row in remittances}):
        total = sum(
            (
                Decimal(str(row["amount"]))
                for row in remittances
                if row["remittance_type"] == kind
            ),
            Decimal("0.00"),
        )
        document.tie(
            f"settlement_total.{kind}",
            kind.replace("_", " ").title(),
            fmt_amount(total),
        )
    document.wrapped(
        "Each row is a bank-settled payroll batch. Employee-level wage and "
        "net-pay detail is supplied in the payroll register."
    )
    path = output_dir / "payroll" / f"Payroll Settlement Report {year}.txt"
    return [
        entry(
            "PAY-04",
            "payroll",
            path,
            document.write(path),
        )
    ]
