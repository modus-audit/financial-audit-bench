"""January bank evidence for subsequent cash and AP clearing."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.monthly_bank_statement import (
    write_statement_body,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)


def _applicable(world: World) -> bool:
    return bool(world.get("bank_statement_month"))


def build_subsequent_statement_activity(
    world: World,
    audit_plan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    statements = world.get("bank_statement_month") or []
    if not statements:
        return None
    year = int(world["fiscal_calendar"]["fiscal_year"]) + 1
    window_start = f"{year}-01-01"
    window_end = f"{year}-01-31"
    if audit_plan:
        from financial_audit_bench.synthetic_binders.audit_planning.calendar import (
            get_window,
        )

        calendar = audit_plan.get("engagement", {}).get("calendar")
        if calendar:
            window = get_window(calendar, "WIN-CASH-CUTOFF")
            window_start = str(window["start_date"])
            window_end = str(window["end_date"])

    from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_ap_subsequent import (
        subsequent_open_invoice_settlements,
    )

    rows: list[dict[str, Any]] = []
    for settlement in subsequent_open_invoice_settlements(world):
        method = str(settlement["payment_method"])
        rows.append(
            {
                "date": settlement["clearing_date"],
                "description": (
                    f"CHECK #{settlement['payment_reference']}"
                    if method == "Check"
                    else f"ACH Payment {settlement['payee']}"
                ),
                "amount": -Decimal(str(settlement["amount"])),
                "is_check": method == "Check",
                "classification": "AP subsequent settlement",
                "source_family": "accounts_payable",
                "source_record_ids": [settlement["payment_id"]],
                "ap_population_id": settlement["payment_id"],
            }
        )

    for item in world.get("bank_reconciling_item") or []:
        clearing_date = str(item.get("subsequent_clearing_date") or "")
        if not window_start <= clearing_date <= window_end:
            continue
        item_kind = str(item["item_kind"])
        rows.append(
            {
                "date": clearing_date,
                "description": item_kind.replace("_", " ").title(),
                "amount": Decimal(str(item["amount"])),
                "is_check": item_kind == "outstanding_check",
                "classification": f"Subsequent clearing — {item_kind}",
                "source_family": "cash",
                "source_record_ids": [item["bank_reconciling_item_id"]],
            }
        )

    fiscal_end = str(world["fiscal_calendar"]["end_date"])
    customers = {
        row["customer_id"]: row["customer_name"] for row in world.get("customer") or []
    }
    for receipt in world.get("customer_cash_receipt") or []:
        receipt_date = str(receipt["receipt_date"])
        if not (fiscal_end < receipt_date <= window_end):
            continue
        rows.append(
            {
                "date": receipt_date,
                "description": (
                    f"Customer receipt {customers.get(receipt['customer_id'], 'Customer')}"
                ),
                "amount": Decimal(str(receipt["amount"])),
                "is_check": False,
                "classification": "Customer receipt",
                "source_family": "accounts_receivable",
                "source_record_ids": [receipt["customer_cash_receipt_id"]],
            }
        )

    rows = [row for row in rows if window_start <= row["date"] <= window_end]
    rows.sort(key=lambda row: (row["date"], row["amount"] < 0))
    opening = Decimal(str(statements[-1]["ending_balance"]))
    ending = opening + sum((row["amount"] for row in rows), Decimal("0"))
    return {
        "ending_balance": ending,
        "identity": package_identity(world),
        "opening_balance": opening,
        "period_end": window_end,
        "period_start": window_start,
        "rows": rows,
        "year": year,
    }


def render(
    world: World,
    output_dir: Path,
    audit_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    activity = build_subsequent_statement_activity(world, audit_plan)
    if activity is None:
        return []
    document = TextDoc()
    write_statement_body(
        document,
        world,
        activity["identity"],
        activity["period_start"],
        activity["period_end"],
        str(activity["opening_balance"]),
        str(activity["ending_balance"]),
        activity["rows"],
    )
    path = (
        output_dir
        / "bank_statements"
        / f"JAN{activity['year'] % 100} Operating Stmt.txt"
    )
    return [
        entry(
            f"DOC-BANK-STATEMENT-{activity['year']}-01-SUBSEQUENT",
            "cash",
            path,
            document.write(path),
        )
    ]
