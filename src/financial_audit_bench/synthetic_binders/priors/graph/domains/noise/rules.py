"""Month-end expense accrual rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.rule_utils import (
    month_end,
)
from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    add_business_days,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

# Month-end expense accruals derive from modeled vendor obligations. A recurring service
# vendor whose bill for a month has not posted by that month end is accrued at the
# vendor's own run rate and reversed on the first business day of the following month.

EXPENSE_GL = "GL-EXPENSE-001"
PAYABLE_GL = "GL-AP-001"

RECURRING_MIN_MONTHS = 6

# These controls can receive repeated vendor documents, but they represent asset
# acquisitions rather than recurring services received. Treating a stock-receipt run as
# an unbilled service accrual duplicates inventory that is already posted by the
# purchase/AP chain.
NON_SERVICE_ACQUISITION_ACCOUNTS = frozenset(
    {
        "GL-FIXED-ASSET-001",
        "GL-INVENTORY-001",
    }
)


def recurring_vendor_obligations(
    vendor_invoices: list[dict[str, Any]],
    fiscal_year: int,
) -> dict[str, dict[str, Any]]:
    """vendor_id -> the vendor's recurring monthly service obligation."""
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for row in vendor_invoices:
        if row["expense_gl_account_id"] in NON_SERVICE_ACQUISITION_ACCOUNTS:
            continue
        by_vendor.setdefault(row["vendor_id"], []).append(row)
    obligations: dict[str, dict[str, Any]] = {}
    for vendor_id, rows in sorted(by_vendor.items()):
        documented = {
            documented_on.month
            for row in rows
            if (documented_on := date.fromisoformat(row["document_date"])).year
            == fiscal_year
        }
        if len(documented) < RECURRING_MIN_MONTHS:
            continue
        covered = set()
        for row in rows:
            documented_on = date.fromisoformat(row["document_date"])
            if documented_on.year != fiscal_year:
                continue
            posted_on = date.fromisoformat(
                row.get("posting_date") or row["document_date"]
            )
            if posted_on <= month_end(documented_on):
                covered.add(documented_on.month)
        obligations[vendor_id] = {
            "vendor_id": vendor_id,
            "expense_class": rows[0]["description"],
            "expense_gl_account_id": rows[0]["expense_gl_account_id"],
            "accrual_gl_account_id": rows[0]["payable_gl_account_id"],
            "covered_months": covered,
            "rows": sorted(rows, key=lambda row: row["document_date"]),
        }
    return obligations


def run_rate_estimate(obligation: dict[str, Any], as_of: date) -> Decimal:
    """The vendor's monthly run rate from invoices documented by ``as_of``."""
    amounts = sorted(
        Decimal(row["amount"])
        for row in obligation["rows"]
        if date.fromisoformat(row["document_date"]) <= as_of
    )
    if not amounts:
        return Decimal("0.00")
    middle = len(amounts) // 2
    if len(amounts) % 2:
        return amounts[middle]
    return ((amounts[middle - 1] + amounts[middle]) / 2).quantize(Decimal("0.01"))


@REGISTRY.rule(
    "post_month_end_accruals",
    inputs=(
        "vendor",
        "vendor_invoice",
        "fiscal_calendar",
        "general_ledger_account",
    ),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=200,
)
def post_month_end_accruals(
    vendors: list[dict[str, Any]],
    vendor_invoices: list[dict[str, Any]],
    calendar: dict[str, Any],
    general_ledger_accounts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One AP-cutoff accrual batch per month for services received but not invoiced: a debit line per open vendor obligation at the vendor's run rate, reversed on the first business day of the following month with a reference back to the originating entry. December is excluded — its open obligations post from the ``accrued_expense`` population and stay open at 12/31."""
    account_ids = {row["gl_account_id"] for row in general_ledger_accounts}
    if EXPENSE_GL not in account_ids or PAYABLE_GL not in account_ids:
        return [], []
    year = int(calendar["fiscal_year"])
    vendor_names = {row["vendor_id"]: row["name"] for row in vendors}
    obligations = recurring_vendor_obligations(vendor_invoices, year)
    entries: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []

    def add_batch(
        entry_id: str,
        when: date,
        description: str,
        open_items: list[tuple[dict[str, Any], Decimal]],
        sign: int,
        reverses: str | None = None,
    ) -> None:
        entry = {
            "journal_entry_id": entry_id,
            "journal_type": "accrued_expense",
            "posting_date": when.isoformat(),
            "voucher_id": entry_id.replace("JOURNAL-", "V"),
            "description": description,
        }
        if reverses is not None:
            entry["reverses_journal_entry_id"] = reverses
        entries.append(entry)
        verb = "Accrue" if sign > 0 else "Reverse accrual"
        total = Decimal("0.00")
        for obligation, estimate in open_items:
            total += sign * estimate
            vendor = vendor_names.get(obligation["vendor_id"], "")
            service = obligation["expense_class"].replace("_", " ")
            lines.append(
                {
                    "gl_account_id": obligation["expense_gl_account_id"],
                    "journal_entry_id": entry_id,
                    "posting_type": "ledger",
                    "signed_amount": str(sign * estimate),
                    "description": f"{verb} {vendor} - {service}".strip(),
                }
            )
        lines.append(
            {
                "gl_account_id": PAYABLE_GL,
                "journal_entry_id": entry_id,
                "posting_type": "ledger",
                "signed_amount": str(-total),
                "description": "Services rec'd not invoiced",
            }
        )

    for month in range(1, 12):
        period_end = month_end(date(year, month, 1))
        open_items = []
        for obligation in obligations.values():
            if month in obligation["covered_months"]:
                continue
            estimate = run_rate_estimate(obligation, period_end)
            if estimate <= 0:
                continue
            open_items.append((obligation, estimate))
        if not open_items:
            continue
        accrue_id = f"JOURNAL-ACCRUAL-ME-{month:02d}"
        add_batch(
            accrue_id,
            period_end,
            "Month end accrual per AP cutoff",
            open_items,
            1,
        )
        add_batch(
            f"{accrue_id}-REV",
            add_business_days(period_end, 1),
            "Reverse prior month accrual per AP cutoff",
            open_items,
            -1,
            reverses=accrue_id,
        )
    return entries, lines
