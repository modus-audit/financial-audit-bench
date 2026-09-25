"""Wire target receivables and inventory populations into the graph."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_staffing import (
    _build_staffing_time_entries,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledgers import (
    build_subledger_records,
)


@REGISTRY.finalize("trial_balance_account")
def finalize_staffing_time_entries(world: dict[str, Any]) -> None:
    """Create staffing time after billing and payroll populations are complete."""
    if world.get("business_type") != "staffing_services":
        return
    records = {
        "customers": world.get("customer") or [],
        "customer_invoices": world.get("customer_invoice") or [],
        "customer_invoice_lines": world.get("customer_invoice_line") or [],
        "staffing_time_entries": [],
    }
    _build_staffing_time_entries(
        records,
        world["fiscal_calendar"],
        world.get("employee") or [],
    )
    world["staffing_time_entry"] = records["staffing_time_entries"]


# Node id -> record key returned by the subledger engine.
OPTIONAL_FAMILY_NODES = {
    "customer": "customers",
    "customer_contract": "customer_contracts",
    "customer_invoice": "customer_invoices",
    "customer_invoice_line": "customer_invoice_lines",
    "fulfillment_event": "fulfillment_events",
    "customer_cash_receipt": "customer_cash_receipts",
    "ar_receipt_application": "ar_receipt_applications",
    "customer_credit_adjustment": "customer_credit_adjustments",
    "ar_allowance_policy": "ar_allowance_policies",
    "ar_allowance_estimate": "ar_allowance_estimates",
    "accounts_receivable_rollforward": "accounts_receivable_rollforwards",
    "accounts_receivable_aging": "accounts_receivable_aging",
    "ar_cutoff_item": "ar_cutoff_items",
    "inventory_item": "inventory_items",
    "inventory_location": "inventory_locations",
    "inventory_lot_balance": "inventory_lot_balances",
    "inventory_movement": "inventory_movements",
    "inventory_count": "inventory_counts",
    "inventory_count_line": "inventory_count_lines",
    "inventory_rollforward": "inventory_rollforwards",
    "inventory_gl_reconciliation": "inventory_gl_reconciliations",
    "steel_production_order": "steel_production_orders",
}


@REGISTRY.rule(
    "build_optional_core_populations",
    inputs=(
        "company_context",
        "company_feature_profile",
        "fiscal_calendar",
        "bank_account",
        "operating_scale",
        "vendor",
    ),
    outputs=tuple(OPTIONAL_FAMILY_NODES),
)
def build_optional_core_populations(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    bank_account: dict[str, Any],
    operating_scale: dict[str, str],
    vendors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], ...]:
    records = build_subledger_records(
        company,
        feature_profile,
        calendar,
        bank_account,
        operating_scale,
        vendor_master=vendors,
    )
    return tuple(records[key] for key in OPTIONAL_FAMILY_NODES.values())


def _allowance_total(estimates: list[dict[str, Any]]) -> Decimal:
    return sum(
        (Decimal(str(row["total_reserve"])) for row in estimates),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))


@REGISTRY.rule(
    "post_credit_loss_provision",
    inputs=("ar_allowance_estimate", "fiscal_calendar"),
    outputs=("journal_entry", "journal_entry_line"),
    contribution_priority=180,
)
def post_credit_loss_provision(
    estimates: list[dict[str, Any]], calendar: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowance = _allowance_total(estimates)
    if not allowance:
        return [], []
    entry_id = "JOURNAL-CECL-PROVISION"
    return [
        {
            "journal_entry_id": entry_id,
            "journal_type": "credit_loss_provision",
            "posting_date": str(calendar["end_date"]),
            "voucher_id": f"CECL-{calendar['fiscal_year']}",
        }
    ], [
        {
            "gl_account_id": "GL-BAD-DEBT-001",
            "journal_entry_id": entry_id,
            "posting_type": "ledger",
            "signed_amount": str(allowance),
        },
        {
            "gl_account_id": "GL-ALLOWANCE-001",
            "journal_entry_id": entry_id,
            "posting_type": "ledger",
            "signed_amount": str(-allowance),
        },
    ]


@REGISTRY.check(
    "validate_recorded_credit_loss_allowance",
    inputs=("ar_allowance_estimate", "trial_balance_account"),
)
def validate_recorded_credit_loss_allowance(
    estimates: list[dict[str, Any]], trial_balance: list[dict[str, Any]]
) -> None:
    expected = _allowance_total(estimates)
    recorded = next(
        (
            -Decimal(str(row["closing_balance"]))
            for row in trial_balance
            if row["gl_account_id"] == "GL-ALLOWANCE-001"
        ),
        Decimal("0.00"),
    )
    if recorded != expected:
        raise ValueError(
            f"recorded allowance {recorded} does not equal estimate {expected}"
        )
