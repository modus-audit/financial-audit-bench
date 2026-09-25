"""Shared allowance, aging, rollforward, and validation controls for A/R."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction import (
    subledger_admission as _subledger_admission,
    subledger_common as _subledger_common,
)

_TRADE_AR_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES["policy.trade_ar"]

_money = _subledger_common._money

# Aging bucket -> (share band of open A/R, days-past-due band, allowance
# loss rate). Buckets run off the selected policy date basis.
AGING_LADDER = tuple(
    (
        bucket,
        (float(share), float(share)),
        (int(day_low), int(day_high)),
        Decimal(str(loss_rate)),
    )
    for bucket, share, day_low, day_high, loss_rate in _TRADE_AR_POLICY["aging_ladder"]
)
_POOL_RATES = {bucket: rate for bucket, _, _, rate in AGING_LADDER}


def _sensitivity_rate(company: dict[str, str], calendar: dict[str, Any]) -> Decimal:
    """Return the cataloged allowance sensitivity rate."""
    del company, calendar
    return Decimal(str(_TRADE_AR_POLICY["allowance_sensitivity_rate"]))


def _policy(
    company: dict[str, str],
    calendar: dict[str, Any],
    year_end: date,
    aging_basis: str = "due_date",
) -> dict[str, Any]:
    if aging_basis not in {"due_date", "invoice_date"}:
        raise ValueError(f"unsupported A/R aging basis {aging_basis!r}")
    ladder_text = ", ".join(
        f"{bucket} {rate * 100:.1f}%" for bucket, _, _, rate in AGING_LADDER
    )
    weighted_reference_rate = sum(
        (
            Decimal(str(share)) * Decimal(str(loss_rate))
            for _, share, _, _, loss_rate in _TRADE_AR_POLICY["aging_ladder"]
        ),
        Decimal("0"),
    ).quantize(Decimal("0.0001"))
    return {
        "aging_pools": "current,1-30,31-60,61-90,over-90",
        "ar_allowance_policy_id": "AR-POLICY-001",
        "company_id": company["company_id"],
        "effective_date": year_end.replace(month=1, day=1).isoformat(),
        "loss_rates": ladder_text,
        "methodology": "independent synthetic aging-pool policy rates; specific reserves when identified",
        "forward_looking_adjustment": "0.0000",
        "forward_looking_basis": "Synthetic exercise specifies a zero overlay",
        "aging_basis": aging_basis,
        "aging_reference_field": aging_basis,
        "aging_date_label": (
            "Due date" if aging_basis == "due_date" else "Invoice date"
        ),
        "prior_year_rate": str(weighted_reference_rate),
        "sensitivity_rate": str(_sensitivity_rate(company, calendar)),
        "specific_reserve_rule": "synthetic exercise specifies no named-account override",
    }


def _finish_ar(
    records: dict[str, list[dict[str, Any]]],
    rng: random.Random,
    company: dict[str, str],
    calendar: dict[str, Any],
    year_end: date,
    open_invoices: list[dict[str, Any]],
    aging_basis: str = "due_date",
) -> None:
    """Aging, allowance estimates, and the rollforward derived by summation
    over the built populations — the ties hold by construction.

    """
    records["ar_allowance_policies"].append(
        _policy(company, calendar, year_end, aging_basis)
    )
    # Invoice numbers issue in date order with occasional gaps.
    number = rng.randint(20000, 68000)
    for invoice in sorted(
        records["customer_invoices"],
        key=lambda row: (row["invoice_date"], row["customer_invoice_id"]),
    ):
        number += rng.choice((1, 1, 1, 1, 2, 3))
        invoice["invoice_number"] = f"INV-{number}"
    credits_by_invoice: dict[str, Decimal] = {}
    for credit in records["customer_credit_adjustments"]:
        if str(credit["posting_date"]) > year_end.isoformat():
            continue
        credits_by_invoice[credit["related_invoice_id"]] = credits_by_invoice.get(
            credit["related_invoice_id"], Decimal("0")
        ) + Decimal(credit["amount"])
    # In-year partial collections reduce the open exposure (the opening-AR
    # aging rows, exactly what the validator recomputes.
    year_end_iso = year_end.isoformat()
    applied_in_year: dict[str, Decimal] = {}
    for application in records["ar_receipt_applications"]:
        if application["application_date"] <= year_end_iso:
            applied_in_year[application["customer_invoice_id"]] = applied_in_year.get(
                application["customer_invoice_id"], Decimal("0")
            ) + Decimal(application["applied_amount"])

    # Invoice lifecycle follows the same credits and cash applications that build the
    # aging. Paid invoices no longer remain marked open after their balance has been
    # fully settled.
    for invoice in records["customer_invoices"]:
        invoice_id = invoice["customer_invoice_id"]
        credits = credits_by_invoice.get(invoice_id, Decimal("0"))
        receipts = applied_in_year.get(invoice_id, Decimal("0"))
        remaining = Decimal(invoice["original_amount"]) - credits - receipts
        if remaining <= Decimal("0.005"):
            invoice["invoice_status"] = "paid"
        elif credits or receipts:
            invoice["invoice_status"] = "partially_paid"
        else:
            invoice["invoice_status"] = "open"

    # Pools key (customer class, bucket) — allowance pools for census books;
    # trade/tenant/grant books carry no class and keep the plain bucket pools.
    pool_exposures: dict[tuple[str | None, str], Decimal] = {}
    for index, invoice in enumerate(open_invoices, 1):
        exposure = (
            Decimal(invoice["original_amount"])
            - credits_by_invoice.get(invoice["customer_invoice_id"], Decimal("0"))
            - applied_in_year.get(invoice["customer_invoice_id"], Decimal("0"))
        )
        # The selected basis is a source parameter, not template prose. It drives days,
        # bucket, reserve rate, report headers, and the CECL memo.
        invoice.pop("_bucket")
        invoice.pop("_loss_rate")
        pool_class = invoice.pop("_pool_class", None)
        if exposure <= 0:
            continue
        reference_date = date.fromisoformat(str(invoice[aging_basis]))
        days_past_due = max((year_end - reference_date).days, 0)
        bucket = (
            "current"
            if days_past_due == 0
            else "1-30"
            if days_past_due <= 30
            else "31-60"
            if days_past_due <= 60
            else "61-90"
            if days_past_due <= 90
            else "over-90"
        )
        loss_rate = _POOL_RATES[bucket]
        reserve = _money(exposure * loss_rate)
        pool_exposures[(pool_class, bucket)] = (
            pool_exposures.get((pool_class, bucket), Decimal("0")) + exposure
        )
        customer = next(
            row
            for row in records["customers"]
            if row["customer_id"] == invoice["customer_id"]
        )
        records["accounts_receivable_aging"].append(
            {
                "aging_basis": aging_basis,
                "aging_bucket": bucket,
                "allowance_amount": str(reserve),
                "ar_aging_item_id": f"AGE-{index:03d}",
                "ar_gl_account_id": "GL-AR-001",
                "as_of_date": year_end.isoformat(),
                "business_unit": customer["business_unit"],
                "currency_code": invoice["currency_code"],
                "customer_id": invoice["customer_id"],
                "customer_invoice_id": invoice["customer_invoice_id"],
                "customer_name": customer["customer_name"],
                "days_past_due": str(days_past_due),
                "due_date": invoice["due_date"],
                "invoice_date": invoice["invoice_date"],
                "invoice_number": invoice["invoice_number"],
                "invoice_status": "open",
                "open_amount": str(exposure),
                "original_amount": invoice["original_amount"],
                "payment_terms": customer["payment_terms"],
                "salesperson": customer["salesperson"],
            }
        )

    # Invoices posted before the fiscal year are the opening receivable (prior-year AR
    # continuity); the year's billing excludes them so the rollforward reads opening +
    # billings - credits - receipts = ending.
    fiscal_start = date.fromisoformat(str(calendar["start_date"]))
    opening_balance = sum(
        (
            Decimal(row["original_amount"])
            for row in records["customer_invoices"]
            if date.fromisoformat(row["posting_date"]) < fiscal_start
        ),
        Decimal("0"),
    )
    invoice_total = sum(
        (
            Decimal(row["original_amount"])
            for row in records["customer_invoices"]
            if date.fromisoformat(row["posting_date"]) >= fiscal_start
        ),
        Decimal("0"),
    )
    credit_total = sum(
        (
            Decimal(row["amount"])
            for row in records["customer_credit_adjustments"]
            if fiscal_start <= date.fromisoformat(str(row["posting_date"])) <= year_end
        ),
        Decimal("0"),
    )
    receipt_in_year = sum(
        (
            Decimal(application["applied_amount"])
            for application in records["ar_receipt_applications"]
            if date.fromisoformat(application["application_date"]) <= year_end
        ),
        Decimal("0"),
    )
    ending_gross = opening_balance + invoice_total - credit_total - receipt_in_year
    allowance_total = Decimal("0")
    open_total = sum(pool_exposures.values(), Decimal("0"))
    bucket_rank = {bucket: i for i, (bucket, _, _, _) in enumerate(AGING_LADDER)}
    pools = sorted(
        pool_exposures,
        key=lambda key: (key[0] or "", bucket_rank[key[1]]),
    )
    for pool_index, (pool_class, bucket) in enumerate(pools, 1):
        exposure = pool_exposures[(pool_class, bucket)]
        rate = _POOL_RATES[bucket]
        reserve = _money(exposure * rate)
        allowance_total += reserve
        prior_exposure = (
            _money(opening_balance * exposure / open_total)
            if open_total
            else Decimal("0.00")
        )
        pool_name = f"{pool_class} - {bucket}" if pool_class else bucket
        records["ar_allowance_estimates"].append(
            {
                "aging_pool": pool_name,
                "ar_allowance_estimate_id": f"ALW-{pool_index:03d}",
                "as_of_date": year_end.isoformat(),
                "exposure_amount": str(exposure),
                "loss_rate": str(rate),
                "pool_reserve": str(reserve),
                "prior_year_reserve": str(_money(prior_exposure * rate)),
                "sensitivity_amount": str(
                    _money(exposure * _sensitivity_rate(company, calendar))
                ),
                "total_reserve": str(reserve),
            }
        )
    records["accounts_receivable_rollforwards"].append(
        {
            "allowance_balance": str(allowance_total),
            "ar_gl_account_id": "GL-AR-001",
            "credit_amount": str(credit_total),
            "difference": "0.00",
            "ending_gross_receivable": str(ending_gross),
            "ending_net_receivable": str(ending_gross - allowance_total),
            "fiscal_calendar_id": str(calendar["fiscal_calendar_id"]),
            "gl_balance": str(ending_gross),
            "invoice_amount": str(invoice_total),
            "opening_balance": str(opening_balance),
            "receipt_amount": str(receipt_in_year),
            "reconciliation_status": "tied",
            "reconciliation_resolution": "not required",
            "review_date": (
                year_end
                + timedelta(days=int(_TRADE_AR_POLICY["allowance_review_lag_days"]))
            ).isoformat(),
            "writeoff_amount": "0.00",
        }
    )


def validate_accounts_receivable(
    records: dict[str, list[dict[str, Any]]], year_end: date
) -> None:
    """Require invoice exposure, aging, rollforward, and GL to agree."""
    rollforwards = records["accounts_receivable_rollforwards"]
    if len(rollforwards) != 1:
        raise ValueError("A/R requires one rollforward")

    credits: dict[str, Decimal] = {}
    for row in records["customer_credit_adjustments"]:
        if date.fromisoformat(str(row["posting_date"])) <= year_end:
            invoice_id = str(row["related_invoice_id"])
            credits[invoice_id] = credits.get(invoice_id, Decimal("0")) + Decimal(
                row["amount"]
            )
    receipts: dict[str, Decimal] = {}
    for row in records["ar_receipt_applications"]:
        if date.fromisoformat(str(row["application_date"])) <= year_end:
            invoice_id = str(row["customer_invoice_id"])
            receipts[invoice_id] = receipts.get(invoice_id, Decimal("0")) + Decimal(
                row["applied_amount"]
            )

    aging = {
        str(row["customer_invoice_id"]): Decimal(row["open_amount"])
        for row in records["accounts_receivable_aging"]
    }
    expected: dict[str, Decimal] = {}
    for invoice in records["customer_invoices"]:
        if date.fromisoformat(str(invoice["posting_date"])) > year_end:
            continue
        invoice_id = str(invoice["customer_invoice_id"])
        exposure = (
            Decimal(invoice["original_amount"])
            - credits.get(invoice_id, Decimal("0"))
            - receipts.get(invoice_id, Decimal("0"))
        )
        if exposure < 0:
            raise ValueError(f"A/R invoice {invoice_id} is over-applied")
        if exposure:
            expected[invoice_id] = exposure
    if aging != expected:
        raise ValueError("A/R aging does not agree with invoice activity")

    rollforward = rollforwards[0]
    if Decimal(rollforward["ending_gross_receivable"]) != sum(
        expected.values(), Decimal("0")
    ):
        raise ValueError("A/R rollforward does not tie")
    if Decimal(rollforward["difference"]):
        raise ValueError("A/R subledger does not tie to its generated GL control")
