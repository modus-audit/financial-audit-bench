"""Vendor-invoice posting and AP-payment settlement date chains."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    add_business_days,
    prior_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


def _chain_lag(key: str, low: int, high: int) -> int:
    """Deterministic per-record lag in [low, high], keyed to world values."""
    return random.Random(f"date-chain|{key}").randint(low, high)


@REGISTRY.finalize("vendor_invoice")
def assign_vendor_invoice_posting_dates(world: dict[str, Any]) -> None:
    """Post each vendor bill a few business days after its invoice date."""
    invoices = world["vendor_invoice"]
    if not invoices:
        return
    year_end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    for row in invoices:
        document_date = date.fromisoformat(row["document_date"])
        lag = _chain_lag(f"{row['vendor_invoice_id']}|{row['amount']}", 1, 4)
        posting = min(add_business_days(document_date, lag), year_end)
        row["posting_date"] = posting.isoformat()


@REGISTRY.finalize("ap_payment")
def assign_ap_payment_date_chain(world: dict[str, Any]) -> None:
    """Order each settlement's dates: posting <= check issue <= clearing."""
    payments = world["ap_payment"]
    if not payments:
        return
    invoices = {row["vendor_invoice_id"]: row for row in world["vendor_invoice"]}
    year_end = date.fromisoformat(str(world["fiscal_calendar"]["end_date"]))
    salt = world["prior_period_bank_balance"]["ending_balance"]
    # A check run cuts once, inside the ISO week its id names: when the batching feature
    # grouped payments into runs, every member shares the latest date its own chain
    # allows within that week. A member whose invoice posted after the week (a calendar-
    # shaped posting) leaves the run and pays off-cycle on its own chain.
    run_floor: dict[str, date] = {}
    for row in payments:
        if not row.get("check_run_id"):
            continue
        initiated = date.fromisoformat(row["payment_initiated_date"])
        monday = initiated - timedelta(days=initiated.weekday())
        week_last = prior_business_day(monday + timedelta(days=6))
        floor = max(
            prior_business_day(initiated),
            date.fromisoformat(invoices[row["vendor_invoice_id"]]["posting_date"]),
        )
        if floor > week_last:
            row["check_run_id"] = None  # posted after the cut: off-cycle
            continue
        run_id = row["check_run_id"]
        run_floor[run_id] = max(run_floor.get(run_id, floor), floor)
    for row in payments:
        invoice = invoices[row["vendor_invoice_id"]]
        issue = prior_business_day(date.fromisoformat(row["payment_initiated_date"]))
        issue = max(issue, date.fromisoformat(invoice["posting_date"]))
        if row.get("check_run_id"):
            issue = run_floor[row["check_run_id"]]
        issue = min(issue, year_end)
        if row["payment_method"] == "check":
            # Checks clear after 2-6 business days of mail and presentment.
            lag = _chain_lag(f"{salt}|{invoice['vendor_id']}|{issue}", 2, 6)
        else:
            lag = _chain_lag(f"{salt}|{row['ap_payment_id']}|{row['amount']}", 0, 1)
        cleared = add_business_days(issue, lag)
        row["payment_initiated_date"] = issue.isoformat()
        row["posting_date"] = issue.isoformat()
        row["cleared_date"] = cleared.isoformat()
        row["bank_activity_date"] = cleared.isoformat()
