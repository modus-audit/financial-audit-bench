"""Customer, invoice-line, and fulfillment construction for trade A/R."""

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

CENT = _subledger_common.CENT
_money = _subledger_common._money
_terms_days = _subledger_common._terms_days
_synthetic_customer_row = _subledger_admission._synthetic_customer_row

# Per-customer payment terms print on the synthetic customer master together
# with a computed due date per invoice.
CUSTOMER_TERMS = tuple(_TRADE_AR_POLICY["terms"])


def _customer_pool(
    company: dict[str, str],
    count: int,
    business_unit: str = "Industrial Sales",
    customer_archetype: str | None = None,
    sales_tax_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Build opaque identities from the retained customer policy."""
    if any(
        type(company.get(field)) is not str for field in ("company_id", "currency_code")
    ):
        raise TypeError("customer company fields must be strings")
    archetype = customer_archetype or "trade"
    customers: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        payment_terms = CUSTOMER_TERMS[(index - 1) % len(CUSTOMER_TERMS)]
        customers.append(
            _synthetic_customer_row(
                archetype=archetype,
                ordinal=index,
                company_id=company["company_id"],
                currency_code=company["currency_code"],
                business_unit=business_unit,
                payment_terms=payment_terms,
                customer_class="goods" if sales_tax_enabled else "services",
            )
        )
    return customers


def _add_invoice(
    records: dict[str, list[dict[str, Any]]],
    rng: random.Random,
    company: dict[str, str],
    customer: dict[str, Any],
    contract_id: str,
    sequence: int,
    display_number: str,
    invoice_date: date,
    amount: Decimal,
    goods: str,
    service_book: bool = False,
    exact_quantity: int | Decimal | None = None,
    exact_unit_price: Decimal | None = None,
    bill_rate: Decimal | None = None,
    rate_rng: random.Random | None = None,
    apply_sales_tax: bool = True,
) -> dict[str, Any]:
    """One invoice with its line and fulfillment event; amount realizes as quantity x a cents-bearing unit price (natural cents, no round dollars). ``service_book`` swaps the shipping-flavored fulfillment fields for rendered-service wording."""
    service_date = invoice_date
    if exact_quantity is not None and exact_unit_price is not None:
        quantity = exact_quantity
        unit = exact_unit_price
        gross = _money(unit * quantity)
        freight = Decimal("0.00")
        discount = Decimal("0.00")
    elif bill_rate is not None:
        quantity = (Decimal(max(8, round(amount / bill_rate * 4))) / 4).quantize(
            Decimal("0.01")
        )
        unit = bill_rate
        gross = _money(unit * quantity)
        freight = Decimal("0.00")
        discount = Decimal("0.00")
    else:
        quantity = rng.randint(2, 48)
        unit = (amount / quantity).quantize(CENT)
        gross = _money(unit * quantity)
        # Optional freight and invoice discounts follow the independent trade
        # policy; service books carry neither merchandise adjustment.
        freight = (
            _money(gross * Decimal(str(_TRADE_AR_POLICY["freight_fraction"])))
            if not service_book
            and rng.random() < float(_TRADE_AR_POLICY["freight_invoice_share"])
            else Decimal("0.00")
        )
        discount = (
            _money(gross * Decimal(str(_TRADE_AR_POLICY["discount_fraction"])))
            if not service_book
            and rng.random() < float(_TRADE_AR_POLICY["discount_invoice_share"])
            else Decimal("0.00")
        )
    if bill_rate is not None and rate_rng is not None:
        # Worker-count and period-ending texture: staffing clients are
        # billed per week for the associates placed there.
        week_ending = invoice_date - timedelta(days=invoice_date.weekday() + 1)
        service_date = week_ending
        goods = (
            f"{goods} - approved assignment hours, "
            f"week ending {week_ending.strftime('%m/%d/%Y')}"
        )
    subtotal = gross + freight - discount
    tax_rate = Decimal(str(customer.get("sales_tax_rate") or "0"))
    taxable = bool(tax_rate and apply_sales_tax and not service_book)
    tax = _money(subtotal * tax_rate) if taxable else Decimal("0.00")
    net = subtotal + tax
    if taxable:
        line_tax_status = "taxable"
        line_tax_rate = str(tax_rate)
        exemption_reference = None
    elif not service_book and customer.get("sales_tax_status") == "resale_exempt":
        line_tax_status = "resale_exempt"
        line_tax_rate = "0.0000"
        exemption_reference = customer.get("tax_exemption_certificate")
        if not exemption_reference:
            raise ValueError("resale-exempt customer has no synthetic certificate")
    elif not service_book:
        line_tax_status = str(customer.get("sales_tax_status") or "taxable")
        line_tax_rate = str(customer.get("sales_tax_rate") or "0.0000")
        exemption_reference = None
    else:
        line_tax_status = "not_applicable"
        line_tax_rate = "0.0000"
        exemption_reference = None
    invoice_id = f"CI-{sequence:04d}"
    goods_book = not service_book
    purchase_order_reference = f"CPO-{sequence:04d}" if goods_book else None
    sales_order_reference = f"SO-{sequence:04d}" if goods_book else None
    quote_reference = f"Q-{sequence:04d}" if goods_book else None
    support_reference = (
        f"SVC-{rng.randint(40000, 99999)}"
        if service_book
        else f"BOL-{rng.randint(40000, 99999)}"
    )
    invoice = {
        "approval_status": "approved",
        "ar_gl_account_id": "GL-AR-001",
        "billing_batch_timestamp": None,
        "billing_cadence": None,
        "billing_deadline": None,
        "business_unit": customer["business_unit"],
        "currency_code": company["currency_code"],
        "customer_contract_id": contract_id,
        "customer_id": customer["customer_id"],
        "customer_invoice_id": invoice_id,
        "due_date": (
            invoice_date + timedelta(days=_terms_days(customer["payment_terms"]))
        ).isoformat(),
        "invoice_date": invoice_date.isoformat(),
        "invoice_number": display_number,
        "invoice_status": "open",
        "original_amount": str(net),
        "posting_date": invoice_date.isoformat(),
        "revenue_gl_account_id": "GL-SERVICE-REVENUE-001",
        "service_period_end": None,
        "service_period_start": None,
    }
    records["customer_invoices"].append(invoice)
    records["customer_invoice_lines"].append(
        {
            "approval_status": "approved",
            "customer_invoice_id": invoice_id,
            "customer_invoice_line_id": f"CIL-{sequence:04d}",
            "discount_amount": str(discount),
            "freight_amount": str(freight),
            "goods_services": goods,
            "gross_amount": str(gross),
            "line_number": "1",
            "net_amount": str(net),
            "quantity": str(quantity),
            "rebate_amount": "0.00",
            "tax_amount": str(tax),
            "sales_tax_status": line_tax_status,
            "sales_tax_rate": line_tax_rate,
            "tax_exemption_certificate": exemption_reference,
            "unit_price": str(unit),
            "customer_purchase_order_reference": purchase_order_reference,
            "sales_order_reference": sales_order_reference,
            "quote_reference": quote_reference,
            "quote_date": (
                (invoice_date - timedelta(days=14)).isoformat() if goods_book else None
            ),
            "base_metal_price_per_unit": None,
            "grade_dimension_adder_per_unit": None,
            "processing_charge_per_unit": None,
            "freight_per_unit": None,
            "discount_per_unit": None,
            "pricing_approved_by": None,
            "uom": "hours" if service_book else "each",
        }
    )
    records["fulfillment_events"].append(
        {
            "acceptance_status": "accepted",
            "customer_acknowledgment": (
                "customer-approved service period"
                if service_book
                else "signed delivery receipt"
            ),
            "customer_invoice_id": invoice_id,
            "customer_invoice_line_id": f"CIL-{sequence:04d}",
            "delivery_terms": (
                "services rendered under customer service order"
                if service_book
                else "FOB shipping point"
            ),
            "destination": "Client site" if service_book else "Customer warehouse",
            "fulfillment_date": service_date.isoformat(),
            "fulfillment_event_id": f"FUL-{sequence:04d}",
            "fulfillment_kind": "service_period" if service_book else "delivery",
            "quantity_fulfilled": str(quantity),
            "support_reference": support_reference,
            "delivery_document_reference": support_reference,
            "acceptance_reference": (
                f"POD-{sequence:04d}" if goods_book else support_reference
            ),
            "acceptance_date": service_date.isoformat(),
            "performance_evidence_status": "complete",
        }
    )
    return invoice
