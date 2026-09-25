"""Compact operating-bank statement renderer shared by cash evidence."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_amount,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)


def _mmdd(value: str) -> str:
    return f"{int(value[5:7]):02d}/{int(value[8:10]):02d}"


def _business_day(value: str) -> str:
    return str(value)[:10]


def _bank_ref(world: World, record_id: str, digits: int = 10) -> str:
    seed = str(world["prior_period_bank_balance"]["ending_balance"])
    value = sum((index + 1) * ord(char) for index, char in enumerate(seed + record_id))
    return str(value % 10**digits).zfill(digits)


def _description(world: World, identity: Any, movement: dict[str, Any]) -> str:
    check_number = identity.check_numbers.get(
        movement.get("ap_payment_id") or movement["cash_movement_id"]
    )
    if check_number:
        return f"CHECK #{check_number}"
    counterparty = str(movement.get("counterparty") or "").strip()
    label = str(movement["transaction_class"]).replace("_", " ").title()
    reference = _bank_ref(world, str(movement["cash_movement_id"]), 8)
    return f"{label} {counterparty} {reference}".strip()


def build_display_rows(
    world: World, identity: Any, movements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    checks: dict[str, dict[str, Any]] = {}
    for movement in sorted(
        movements,
        key=lambda row: (row["bank_activity_date"], row["cash_movement_id"]),
    ):
        amount = Decimal(str(movement["signed_amount"]))
        check_number = identity.check_numbers.get(
            movement.get("ap_payment_id") or movement["cash_movement_id"]
        )
        if check_number:
            row = checks.get(check_number)
            if row is None:
                row = {
                    "date": _business_day(movement["bank_activity_date"]),
                    "description": f"CHECK #{check_number}",
                    "amount": Decimal("0"),
                    "is_check": True,
                    "fee": False,
                }
                checks[check_number] = row
                rows.append(row)
            row["amount"] += amount
        else:
            rows.append(
                {
                    "date": _business_day(movement["bank_activity_date"]),
                    "description": _description(world, identity, movement),
                    "amount": amount,
                    "is_check": False,
                    "fee": movement["transaction_class"] == "bank_fee",
                }
            )
    return sorted(rows, key=lambda row: (row["date"], row["amount"] < 0))


def write_statement_body(
    doc: TextDoc,
    world: World,
    identity: Any,
    period_start: str,
    period_end: str,
    opening_balance: str,
    ending_balance: str,
    rows: list[dict[str, Any]],
    product: str | None = None,
    account_display: str | None = None,
) -> None:
    bank = identity.bank
    doc.center(bank["name"])
    doc.center(bank["address"])
    doc.pair("Account holder", company_name(world))
    doc.pair(product or bank["product"], account_display or identity.masked_account())
    doc.pair("Statement period", f"{period_start} through {period_end}")
    doc.rule("=")
    deposits = [row for row in rows if row["amount"] > 0]
    withdrawals = [row for row in rows if row["amount"] < 0]
    doc.tie("opening_balance", "Beginning balance", fmt_amount(opening_balance))
    doc.pair(
        f"Deposits and additions ({len(deposits)})",
        fmt_amount(sum((row["amount"] for row in deposits), Decimal("0"))),
    )
    doc.pair(
        f"Checks and withdrawals ({len(withdrawals)})",
        fmt_amount(sum((abs(row["amount"]) for row in withdrawals), Decimal("0"))),
    )
    doc.tie("ending_balance", "Ending balance", fmt_amount(ending_balance))
    doc.line()
    doc.line(f"{'DATE':<8}{'DESCRIPTION':<48}{'AMOUNT':>16}")
    doc.rule()
    for row in rows:
        doc.line(
            f"{_mmdd(row['date']):<8}"
            f"{str(row['description'])[:48]:<48}"
            f"{fmt_amount(row['amount']):>16}"
        )
    doc.line()
    doc.line("DAILY ENDING BALANCE")
    balance = Decimal(str(opening_balance))
    daily: dict[str, Decimal] = {}
    for row in rows:
        balance += Decimal(str(row["amount"]))
        daily[str(row["date"])] = balance
    prior_day = (date.fromisoformat(period_start) - timedelta(days=1)).isoformat()
    doc.line(f"{_mmdd(prior_day):<10}{fmt_amount(opening_balance):>20}")
    for day in sorted(daily):
        doc.line(f"{_mmdd(day):<10}{fmt_amount(daily[day]):>20}")
    doc.line()
    doc.center(f"Customer Service {bank['phone']} | Member FDIC")


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    identity = package_identity(world)
    movements_by_month: dict[str, list[dict[str, Any]]] = {}
    for row in world["cash_movement"]:
        movements_by_month.setdefault(row["bank_activity_date"][:7], []).append(row)
    entries: list[dict[str, Any]] = []
    for statement in sorted(
        world["bank_statement_month"],
        key=lambda row: row["statement_period_end"],
    )[-1:]:
        month = statement["statement_period_end"][:7]
        rows = build_display_rows(world, identity, movements_by_month.get(month, []))
        walked = Decimal(statement["opening_balance"]) + sum(
            (row["amount"] for row in rows), Decimal("0")
        )
        if walked != Decimal(statement["ending_balance"]):
            raise ValueError(f"statement {month} transaction detail does not foot")
        document = TextDoc()
        write_statement_body(
            document,
            world,
            identity,
            statement["statement_period_start"],
            statement["statement_period_end"],
            statement["opening_balance"],
            statement["ending_balance"],
            rows,
        )
        stamp = (
            date.fromisoformat(statement["statement_period_end"])
            .strftime("%b%y")
            .upper()
        )
        path = output_dir / "bank_statements" / f"{stamp} Operating Stmt.txt"
        entries.append(
            entry(f"DOC-BANK-STATEMENT-{month}", "cash", path, document.write(path))
        )
    return entries
