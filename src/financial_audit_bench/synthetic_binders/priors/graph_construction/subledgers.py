"""Subledger engine for target A/R and inventory populations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.profiles import (
    is_manufacturing,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_inventory_setup import (
    INVENTORY_KEYS,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction import (
    subledger_admission as _subledger_admission,
    subledger_ar_controls as _subledger_ar_controls,
    subledger_common as _subledger_common,
    subledger_inventory_builder as _subledger_inventory_builder,
    subledger_inventory_planning as _subledger_inventory_planning,
    subledger_inventory_validation as _subledger_inventory_validation,
    subledger_scale as _subledger_scale,
    subledger_steel_sales as _subledger_steel_sales,
    subledger_trade_ar as _subledger_trade_ar,
)

_money = _subledger_common._money

_build_trade_ar = _subledger_trade_ar._build_trade_ar
trade_opening_ar_plan = _subledger_scale.trade_opening_ar_plan
is_trade = _subledger_scale.is_trade
subledger_scale = _subledger_scale.subledger_scale
opening_inventory_cost = _subledger_inventory_planning.opening_inventory_cost
_inventory_usage = _subledger_inventory_planning._inventory_usage
_build_inventory = _subledger_inventory_builder._build_inventory
validate_inventory = _subledger_inventory_validation.validate_inventory
_align_steel_sales_to_shipments = _subledger_steel_sales._align_steel_sales_to_shipments
validate_accounts_receivable = _subledger_ar_controls.validate_accounts_receivable


_TRADE_AR_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES["policy.trade_ar"]

# The DP release can contain category-noisy A/R ratios (including values above one). The
# generated open-item book is part of annual sales, so allowing an unbounded ratio makes
# open invoices alone exceed the entire revenue anchor and, for staffing, creates more
# billed hours than the payroll population can supply.
_TRADE_AR_TO_REVENUE_FLOOR = Decimal("0.005")
_TRADE_AR_TO_REVENUE_CEILING = Decimal("0.60")


AR_KEYS = (
    "customers",
    "customer_contracts",
    "customer_invoices",
    "customer_invoice_lines",
    "fulfillment_events",
    "customer_cash_receipts",
    "ar_receipt_applications",
    "customer_credit_adjustments",
    "ar_allowance_policies",
    "ar_allowance_estimates",
    "accounts_receivable_rollforwards",
    "accounts_receivable_aging",
    "ar_cutoff_items",
)


def build_subledger_records(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    bank_account: dict[str, Any],
    operating_scale: dict[str, str],
    vendor_master: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build the optional-family populations; empty collections when gated."""
    if not is_trade(feature_profile):
        raise ValueError("subledgers support only manufacturing and staffing")
    records: dict[str, list[dict[str, Any]]] = {
        key: [] for key in (*AR_KEYS, *INVENTORY_KEYS)
    }
    scale = subledger_scale(company, feature_profile, calendar, operating_scale)
    year_end = date.fromisoformat(str(calendar["end_date"]))

    if feature_profile.get("has_accounts_receivable"):
        ar_ratio = operating_scale.get("accounts_receivable_to_revenue") or ""
        if is_trade(feature_profile):
            revenue_base = scale["annual_sales"]
            ar_target_ratio = (
                min(
                    max(
                        Decimal(ar_ratio),
                        _TRADE_AR_TO_REVENUE_FLOOR,
                    ),
                    _TRADE_AR_TO_REVENUE_CEILING,
                )
                if ar_ratio
                else scale["ar_days"] / 365
            )
            ar_target = _money(revenue_base * ar_target_ratio)
            # Manufacturing bills industrial buyers; staffing bills service
            # clients.
            goods_pool = None
            if feature_profile.get("has_inventory"):
                unit, goods_label = "Industrial Sales", None
                # Grammar worlds sell their own finished goods.
                grammar = load_authored_policy("authored.inventory.rules.v1").values[
                    "grammar"
                ]
                if not grammar:
                    raise ValueError("manufacturing inventory grammar is missing")
                goods_pool = tuple(
                    description
                    for category, _, templates in grammar["categories"]
                    if category == "finished_goods"
                    for description in templates
                )
            else:
                unit, goods_label = "Client Services", "professional services"
            # Staffing register: weekly invoices realize as
            # hours x a per-client bill rate from the declared band.
            staffing_book = (
                str(feature_profile.get("business_type") or "") == "staffing_services"
            )
            if staffing_book:
                goods_label = "contract staffing services"
            _build_trade_ar(
                records,
                company,
                calendar,
                bank_account,
                year_end,
                annual_sales=revenue_base,
                ar_target=ar_target,
                business_unit=unit,
                goods_label=goods_label,
                goods_pool=goods_pool,
                sales_tax_enabled=(
                    str(feature_profile.get("business_type") or "") == "manufacturing"
                ),
                staffing=staffing_book,
                # Opening AR: the prior TB pins the same plan total (prior_source), so
                # the rollforward opens where the prior year closed.
                opening_plan=trade_opening_ar_plan(
                    company, feature_profile, calendar, operating_scale
                ),
            )
        validate_accounts_receivable(records, year_end)
        # The builders append the open (aged) population, then the paid months: a
        # shipment log opening with December then jumping back to January is a
        # generated-file tell. One chronological register, as the client's system would
        # print it.
        records["fulfillment_events"].sort(
            key=lambda row: (row["fulfillment_date"], row["fulfillment_event_id"])
        )

    if feature_profile.get("has_inventory"):
        inventory_ratio = operating_scale.get("inventory_to_revenue") or ""
        ending_target = (
            _money(Decimal(inventory_ratio) * scale["annual_sales"])
            if inventory_ratio
            else scale["opening_inventory"]
        )
        # Apply the same authored inventory-days guardrail regardless of
        # which released ratio channel supplied the tentative target.
        days_low, days_high = (
            Decimal(str(edge)) for edge in _TRADE_AR_POLICY["inventory_days_guardrail"]
        )
        ending_target = min(
            max(ending_target, _money(scale["cogs"] * days_low / 365)),
            _money(scale["cogs"] * days_high / 365),
        )
        _build_inventory(
            records,
            company,
            calendar,
            year_end,
            annual_cogs=_inventory_usage(scale, feature_profile),
            opening_cost=scale["opening_inventory"],
            ending_target=ending_target,
            business_type=str(feature_profile.get("business_type") or ""),
            vendor_master=vendor_master,
        )
        if is_manufacturing(str(feature_profile.get("business_type") or "")):
            _align_steel_sales_to_shipments(records, year_end)
        validate_inventory(records)

    pricing_fields = (
        "quote_reference",
        "quote_date",
        "base_metal_price_per_unit",
        "grade_dimension_adder_per_unit",
        "processing_charge_per_unit",
        "freight_per_unit",
        "discount_per_unit",
        "pricing_approved_by",
    )
    for line in records["customer_invoice_lines"]:
        for field in pricing_fields:
            line.setdefault(field, None)
        line.setdefault("sales_tax_status", "not_applicable")
        line.setdefault("sales_tax_rate", "0.0000")
        line.setdefault("tax_exemption_certificate", None)
    return records
