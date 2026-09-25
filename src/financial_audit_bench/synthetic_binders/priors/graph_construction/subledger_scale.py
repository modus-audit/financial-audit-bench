"""Entity-scale planning and prior-period A/R continuity."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction import (
    subledger_admission as _subledger_admission,
    subledger_common as _subledger_common,
)

_TRADE_AR_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES["policy.trade_ar"]
_STAFFING_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES[
    "policy.staffing_billing"
]
_GROSS_MARGIN_BANDS = load_authored_policy(
    "authored.global.operating-policy.v1"
).values["policies"]["policy.type_flavors.margin_bands.v1"]["bands_by_business_type"]

CENT = _subledger_common.CENT
TRADE_OPENING_DSO_BAND = tuple(_TRADE_AR_POLICY["opening_dso_band"])

_allocate_amount = _subledger_common._allocate_amount
_money = _subledger_common._money
_rng = _subledger_common._rng


def trade_opening_ar_plan(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    operating_scale: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Opening (prior 12/31) trade/service AR plan ."""
    if not feature_profile.get("has_accounts_receivable"):
        return []
    if not is_trade(feature_profile):
        return []
    scale = subledger_scale(company, feature_profile, calendar, operating_scale)
    prior_sales = scale["prior_sales"]
    if prior_sales <= 0:
        return []
    rng = _rng(company, calendar, "opening-ar-trade")
    dso = rng.randint(*TRADE_OPENING_DSO_BAND)
    target = _money(prior_sales * dso / 365)
    if target <= 0:
        return []
    chunk_count = int(_TRADE_AR_POLICY["opening_invoice_count"])
    weights = (
        [Decimal("1")] * chunk_count
        if rng.random()
        < float(_TRADE_AR_POLICY["opening_equal_allocation_probability"])
        else [
            Decimal(str(rng.uniform(*_TRADE_AR_POLICY["opening_weight_band"])))
            for _ in range(chunk_count)
        ]
    )
    requests = _allocate_amount(target, weights)
    items: list[dict[str, Any]] = []
    for request in requests:
        if request <= 0:
            continue
        if str(feature_profile.get("business_type") or "") == "staffing_services":
            # Staffing invoices realize as quarter-hours x a band bill rate on the
            # opening rows too, so the pinned prior TB recomputes the identical hours-
            # based total.
            unit_price = Decimal(
                str(rng.choice(_STAFFING_POLICY["bill_rates"]))
            ).quantize(CENT)
            quantity: int | Decimal = (
                Decimal(max(8, round(request / unit_price * 4))) / 4
            ).quantize(Decimal("0.01"))
        else:
            quantity = 1
            unit_price = request
        amount = _money(unit_price * quantity)
        items.append(
            {
                "amount": amount,
                "quantity": quantity,
                "unit_price": unit_price,
                "invoice_offset_days": rng.randint(*TRADE_OPENING_DSO_BAND),
                "receipt_offset_days": int(
                    _TRADE_AR_POLICY["partial_payment_lag_days"]
                ),
                "fraction": Decimal(
                    str(_TRADE_AR_POLICY["partial_payment_fractions"][0])
                ),
                "straggler": False,
            }
        )
    straggler_count = min(
        len(items),
        round(len(items) * float(_TRADE_AR_POLICY["partial_payment_share"])),
    )
    for item in items[:straggler_count]:
        item["straggler"] = True
    return items


def trade_opening_ar(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    operating_scale: dict[str, str] | None,
) -> Decimal:
    """The prior 12/31 trade AR balance the TB pins: the plan's exact sum."""
    return sum(
        (
            item["amount"]
            for item in trade_opening_ar_plan(
                company, feature_profile, calendar, operating_scale
            )
        ),
        Decimal("0.00"),
    )


def is_trade(feature_profile: dict[str, bool]) -> bool:
    """Whether the retained manufacturing/staffing profile bills on account."""
    return bool(feature_profile.get("has_accounts_receivable"))


def sells_goods(feature_profile: dict[str, bool]) -> bool:
    """Whether the retained profile relieves inventory through product sales."""
    return bool(feature_profile.get("has_inventory"))


def subledger_scale(
    company: dict[str, str],
    feature_profile: dict[str, bool],
    calendar: dict[str, Any],
    operating_scale: dict[str, str],
) -> dict[str, Decimal]:
    """The deterministic entity-scale plan. Shared by this engine and the prior-period balance vector so the opening TB carries the same inventory and prior-year operating history the subledgers continue."""
    if not is_trade(feature_profile):
        raise ValueError("scale supports only manufacturing and staffing")
    trade_policy = _TRADE_AR_POLICY
    rng = _rng(company, calendar, "scale")
    annual_sales = _money(rng.uniform(*trade_policy["annual_sales_band"]))
    gross_margin_band = _GROSS_MARGIN_BANDS[str(feature_profile["business_type"])]
    gm_band = tuple(float(edge) for edge in gross_margin_band)
    gross_margin = Decimal(str(round(rng.uniform(*gm_band), 4)))
    inventory_days = Decimal(rng.randint(*trade_policy["inventory_days_band"]))
    prior_factor = (
        Decimal("1") / Decimal(str(trade_policy["prior_year_divisor"]))
    ).quantize(Decimal("0.0001"))
    cogs_ratio = operating_scale.get("cogs_to_revenue") or ""
    if cogs_ratio:
        # DP-released COGS share of revenue; clamped so a TB-categorization
        # outlier cannot produce a negative or implausible margin.
        cogs_low, cogs_high = (
            Decimal(str(edge)) for edge in trade_policy["cogs_ratio_clamp"]
        )
        clamped_cogs = min(max(Decimal(cogs_ratio), cogs_low), cogs_high)
        gross_margin = (Decimal("1") - clamped_cogs).quantize(Decimal("0.0001"))
        industry_low, industry_high = (Decimal(edge) for edge in gross_margin_band)
        gross_margin = min(max(gross_margin, industry_low), industry_high).quantize(
            Decimal("0.0001")
        )
    days_ratio = operating_scale.get("inventory_to_cogs") or ""
    if days_ratio:
        # DP-released inventory-to-COGS (turns proxy) as days on hand; clamped
        # because a near-zero COGS ratio would imply years of stock.
        days_low, days_high = (
            Decimal(str(edge)) for edge in trade_policy["inventory_days_guardrail"]
        )
        inventory_days = min(
            max(Decimal(days_ratio) * 365, days_low), days_high
        ).quantize(Decimal("0.01"))
    cogs = _money(annual_sales * (1 - gross_margin))
    return {
        "annual_sales": annual_sales,
        "cogs": cogs,
        "gross_margin": gross_margin,
        "opening_inventory": _money(cogs * inventory_days / 365),
        "prior_sales": _money(annual_sales * prior_factor),
        "prior_cogs": _money(cogs * prior_factor),
        "ar_days": Decimal(rng.randint(*trade_policy["ar_days_band"])),
    }
