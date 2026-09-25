"""Trade receivables: contracts, billing, collections, cutoff, and credits."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction import (
    subledger_admission as _subledger_admission,
    subledger_ar_controls as _subledger_ar_controls,
    subledger_common as _subledger_common,
    subledger_trade_documents as _subledger_trade_documents,
)

_TRADE_AR_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES["policy.trade_ar"]
_STAFFING_POLICY = _subledger_admission.SUBLEDGER_SYNTHETIC_POLICIES[
    "policy.staffing_billing"
]
AGING_LADDER = _subledger_ar_controls.AGING_LADDER
AR_OPEN_ITEM_FLOOR = int(_TRADE_AR_POLICY["open_item_floor"])
CENT = _subledger_common.CENT
CREDIT_REASONS = tuple(_TRADE_AR_POLICY["credit_reasons"])
SERVICE_CREDIT_REASONS = tuple(_TRADE_AR_POLICY["service_credit_reasons"])

_allocate_amount = _subledger_common._allocate_amount
_is_business_day = _subledger_common._is_business_day
_money = _subledger_common._money
_receipt_business_day = _subledger_common._receipt_business_day
_rng = _subledger_common._rng
_terms_days = _subledger_common._terms_days
_finish_ar = _subledger_ar_controls._finish_ar
_add_invoice = _subledger_trade_documents._add_invoice
_customer_pool = _subledger_trade_documents._customer_pool


def _build_trade_ar(
    records: dict[str, list[dict[str, Any]]],
    company: dict[str, str],
    calendar: dict[str, Any],
    bank_account: dict[str, Any],
    year_end: date,
    annual_sales: Decimal,
    ar_target: Decimal,
    business_unit: str = "Industrial Sales",
    goods_label: str | None = None,
    goods_pool: tuple[str, ...] | None = None,
    sales_tax_enabled: bool = False,
    opening_plan: list[dict[str, Any]] | None = None,
    staffing: bool = False,
) -> None:
    """A year of billing on account: paid invoices cover the collected sales, open invoices realize the drawn aging mix summing to the A/R target. ``goods_label`` switches the line/contract/fulfillment wording from shipped product (the default) to rendered services."""
    rng = _rng(company, calendar, "trade-ar")
    service_book = goods_label is not None
    customer_count = int(
        _STAFFING_POLICY["active_customer_count"]
        if staffing
        else _TRADE_AR_POLICY["active_customer_count"]
    )
    customers = _customer_pool(
        company,
        customer_count,
        business_unit=business_unit,
        customer_archetype="staffing_service" if staffing else "trade",
        sales_tax_enabled=sales_tax_enabled and not service_book,
    )
    records["customers"].extend(customers)
    # Staffing billing uses the configured discrete rate card. A
    # separate RNG stream leaves the general trade-A/R sequence untouched.
    bill_rates: dict[str, Decimal] = {}
    rate_rng = None
    if staffing:
        rate_rng = _rng(company, calendar, "staffing-rates")
        authored_rates = [
            Decimal(str(value)) for value in _STAFFING_POLICY["bill_rates"]
        ]
        bill_rates = {
            customer["customer_id"]: rate_rng.choice(authored_rates).quantize(CENT)
            for customer in customers
        }
    # Fine types with an inventory grammar sell their own finished goods
    #: each customer contracts for a drawn product, so the
    # invoice register carries the maker's catalog, not one generic
    # "components" string. Other goods books keep the default draw.
    if goods_pool and not service_book:
        goods = None
        product_cycle = list(goods_pool)
        rng.shuffle(product_cycle)
        goods_by_customer = {
            customer["customer_id"]: product_cycle[index % len(product_cycle)]
            for index, customer in enumerate(customers)
        }
    else:
        goods = goods_label or "arbitrary synthetic goods"
        goods_by_customer = {}
    for index, customer in enumerate(customers, 1):
        contract_price = _money(annual_sales / len(customers))
        contract_quantity = (
            max(1, int(contract_price / bill_rates[customer["customer_id"]]))
            if bill_rates
            else rng.randint(100, 900)
        )
        records["customer_contracts"].append(
            {
                "acceptance_terms": (
                    "services accepted as rendered"
                    if service_book
                    else (
                        "signed proof of delivery acknowledges quantity and condition; "
                        "control transfers under the stated shipping terms"
                        if sales_tax_enabled
                        else "customer acceptance on delivery"
                    )
                ),
                "cancellation_terms": "cancelable on 30 days' notice",
                "contract_end_date": year_end.isoformat(),
                "contract_price": str(contract_price),
                "contract_quantity": str(contract_quantity),
                "contract_start_date": year_end.replace(month=1, day=1).isoformat(),
                "customer_contract_id": f"CC-{index:03d}",
                "customer_id": customer["customer_id"],
                "delivery_terms": (
                    "services rendered under customer service order"
                    if service_book
                    else "FOB shipping point"
                ),
                "goods_services": (
                    goods_by_customer.get(customer["customer_id"]) or goods
                ),
                "payment_terms": customer["payment_terms"],
                "presentation": (
                    "service revenue" if service_book else "product revenue"
                ),
                "required_evidence": (
                    "approved time records and customer invoice"
                    if service_book
                    else (
                        "approved quote, customer PO, sales order, bill of lading, "
                        "signed proof of delivery, and invoice"
                        if sales_tax_enabled
                        else "invoice and signed delivery receipt"
                    )
                ),
                "return_terms": (
                    "not applicable" if service_book else "returns require approval"
                ),
                "transfer_of_control_point": (
                    "service period" if service_book else "shipment"
                ),
            }
        )
    contract_by_customer = {
        row["customer_id"]: row["customer_contract_id"]
        for row in records["customer_contracts"]
    }
    invoice_base = rng.randint(20000, 68000)
    sequence = 0

    def next_invoice(
        customer,
        invoice_date,
        amount,
        exact_quantity=None,
        exact_unit_price=None,
        apply_sales_tax=True,
    ):
        nonlocal sequence
        sequence += 1
        return _add_invoice(
            records,
            rng,
            company,
            customer,
            contract_by_customer[customer["customer_id"]],
            sequence,
            f"INV-{invoice_base + sequence}",
            invoice_date,
            amount,
            goods_by_customer.get(customer["customer_id"]) or goods,
            service_book=service_book,
            exact_quantity=exact_quantity,
            exact_unit_price=exact_unit_price,
            bill_rate=bill_rates.get(customer["customer_id"]),
            rate_rng=rate_rng,
            apply_sales_tax=apply_sales_tax,
        )

    # Open population first: bucket mix summing to the A/R target. The per-item cap keys
    # off the target so the aging carries an auditable one-job schedule audits
    # nothing"), not two invoices covering it.
    open_invoices: list[dict[str, Any]] = []
    remaining = ar_target
    invoice_low, invoice_high = (
        Decimal(str(value)) for value in _TRADE_AR_POLICY["invoice_amount_band"]
    )
    item_cap_amount = min(
        invoice_high,
        max(invoice_low, ar_target / AR_OPEN_ITEM_FLOOR),
    )
    item_cap = float(item_cap_amount)
    ladder = list(AGING_LADDER)
    shares = [rng.uniform(*band) for _, band, _, _ in ladder]
    total_share = sum(shares)
    for (bucket, _, past_due_band, loss_rate), share in zip(ladder, shares):
        bucket_total = _money(ar_target * Decimal(str(share / total_share)))
        bucket_total = min(bucket_total, remaining)
        while bucket_total > 0:
            amount = _money(
                min(
                    bucket_total,
                    Decimal(str(rng.uniform(float(invoice_low), item_cap))),
                )
            )
            if bucket_total - amount < invoice_low:
                amount = bucket_total
            customer = rng.choices(customers)[0]
            # Invoice date backs out of the bucket's days-past-due band through the
            # customer's terms, so due date, days past due, and bucket stay consistent
            # whatever terms the customer has.
            invoice_date = min(
                year_end,
                year_end
                - timedelta(
                    days=_terms_days(customer["payment_terms"])
                    + rng.randint(*past_due_band)
                ),
            )
            invoice = next_invoice(customer, invoice_date, amount)
            realized_amount = Decimal(invoice["original_amount"])
            if realized_amount == 0:
                # A sub-cent residual can round its generated line to zero. Discard that
                # non-economic artifact and consume the planned residual so this bounded
                # allocation cannot stall forever.
                records["customer_invoices"].pop()
                records["customer_invoice_lines"].pop()
                records["fulfillment_events"].pop()
                bucket_total -= amount
                remaining -= amount
                continue
            invoice["_bucket"] = bucket
            invoice["_loss_rate"] = loss_rate
            open_invoices.append(invoice)
            bucket_total -= realized_amount
            remaining -= realized_amount

    # Paid population: the collected remainder of the year's sales,
    # spread over the twelve months with jitter.
    open_total = sum(
        (Decimal(i["original_amount"]) for i in open_invoices), Decimal("0")
    )
    collected_plan = max(annual_sales - open_total, Decimal("0"))
    month_weights = [
        rng.uniform(*_TRADE_AR_POLICY["monthly_jitter"]) for _ in range(12)
    ]
    weight_total = sum(month_weights)
    if bill_rates:
        open_by_month = dict.fromkeys(range(1, 13), Decimal("0"))
        for invoice in open_invoices:
            invoice_date = date.fromisoformat(invoice["invoice_date"])
            if invoice_date.year == year_end.year:
                open_by_month[invoice_date.month] += Decimal(invoice["original_amount"])
        # Open AR is part of annual revenue, not extra December volume. Reserve each
        # month's randomized sales target for its already-dated open invoices, then
        # distribute collected sales across the remaining monthly capacity.
        paid_weights = [
            max(
                annual_sales * Decimal(str(month_weights[month - 1] / weight_total))
                - open_by_month[month],
                Decimal("0"),
            )
            for month in range(1, 13)
        ]
        if not any(paid_weights):
            paid_weights = [Decimal(str(value)) for value in month_weights]
        paid_month_totals = _allocate_amount(collected_plan, paid_weights)
    else:
        paid_month_totals = [
            _money(
                collected_plan * Decimal(str(month_weights[month - 1] / weight_total))
            )
            for month in range(1, 13)
        ]
    paid_invoices: list[dict[str, Any]] = []
    for month in range(1, 13):
        month_total = paid_month_totals[month - 1]
        while month_total > 0:
            invoice_cap = float(invoice_high)
            amount = _money(
                min(
                    month_total,
                    Decimal(str(rng.uniform(float(invoice_low), invoice_cap))),
                )
            )
            if month_total - amount < invoice_low:
                amount = month_total
            last_day = 20 if month == 12 else 28
            if bill_rates and month < 12:
                # Staffing clients receive the month's approved-time batch after the
                # service weeks close. Dating a full monthly volume on January 11 made
                # the invoice demand four weeks of labor when only one completed week
                # existed.
                invoice_date = date(year_end.year, month + 1, 1) - timedelta(days=1)
                while not _is_business_day(invoice_date):
                    invoice_date -= timedelta(days=1)
            else:
                invoice_date = date(year_end.year, month, rng.randint(1, last_day))
            if invoice_date > year_end - timedelta(days=25):
                invoice_date = year_end - timedelta(days=rng.randint(25, 40))
            customer = rng.choices(customers)[0]
            paid_invoices.append(next_invoice(customer, invoice_date, amount))
            # ``amount`` is the allocated monthly revenue target. The line's
            # independently modeled freight, discount, and tax can make its receivable
            # differ by a few cents; subtracting that receivable here created a second
            # near-zero invoice to chase the rounding residual.
            month_total -= amount

    # Credit memos follow the independent synthetic share and fraction.
    credits_by_invoice: dict[str, Decimal] = {}
    credit_count = min(
        len(paid_invoices),
        round(len(paid_invoices) * float(_TRADE_AR_POLICY["credit_invoice_share"])),
    )
    for index, invoice in enumerate(rng.sample(paid_invoices, credit_count), 1):
        amount = _money(
            Decimal(invoice["original_amount"])
            * Decimal(str(_TRADE_AR_POLICY["credit_fraction"]))
        )
        memo_date = date.fromisoformat(invoice["invoice_date"]) + timedelta(
            days=int(_TRADE_AR_POLICY["partial_payment_lag_days"])
        )
        records["customer_credit_adjustments"].append(
            {
                "accounting_period": memo_date.strftime("%Y-%m"),
                "adjustment_date": memo_date.isoformat(),
                "adjustment_kind": "credit_memo",
                "amount": str(amount),
                "approval_status": "approved",
                "customer_credit_adjustment_id": f"CM{year_end.year}-{index:04d}",
                "customer_id": invoice["customer_id"],
                "posting_date": memo_date.isoformat(),
                "reason": rng.choice(
                    SERVICE_CREDIT_REASONS if service_book else CREDIT_REASONS
                ),
                "related_invoice_id": invoice["customer_invoice_id"],
                "settlement_status": "applied",
            }
        )
        credits_by_invoice[invoice["customer_invoice_id"]] = amount

    # In-year receipts settle every paid invoice net of its credits.
    receipt_index = 0

    settled: dict[str, Decimal] = {}

    def add_receipt(invoice, receipt_date, fraction=None, remainder=False):
        nonlocal receipt_index
        receipt_index += 1
        due = Decimal(invoice["original_amount"]) - credits_by_invoice.get(
            invoice["customer_invoice_id"], Decimal("0")
        )
        if remainder:
            net = due - settled.get(invoice["customer_invoice_id"], Decimal("0"))
        elif fraction is not None:
            net = _money(due * fraction)
        else:
            net = due
        settled[invoice["customer_invoice_id"]] = (
            settled.get(invoice["customer_invoice_id"], Decimal("0")) + net
        )
        receipt_id = f"CR-{receipt_index:04d}"
        records["customer_cash_receipts"].append(
            {
                "amount": str(net),
                "bank_account_id": bank_account["bank_account_id"],
                "bank_reference": f"ACH-{rng.randint(1000000, 9999999)}",
                "currency_code": company["currency_code"],
                "customer_cash_receipt_id": receipt_id,
                "customer_id": invoice["customer_id"],
                "deduction_amount": "0.00",
                "discount_amount": "0.00",
                "posting_date": receipt_date.isoformat(),
                "receipt_date": receipt_date.isoformat(),
                "unapplied_amount": "0.00",
            }
        )
        records["ar_receipt_applications"].append(
            {
                "applied_amount": str(net),
                "application_date": receipt_date.isoformat(),
                "ar_receipt_application_id": f"CRA-{receipt_index:04d}",
                "customer_cash_receipt_id": receipt_id,
                "customer_invoice_id": invoice["customer_invoice_id"],
                "deduction_amount": "0.00",
                "discount_amount": "0.00",
                "unapplied_amount": "0.00",
            }
        )

    for invoice in paid_invoices:
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        customer = next(
            row for row in customers if row["customer_id"] == invoice["customer_id"]
        )
        receipt_date = _receipt_business_day(
            min(
                invoice_date + timedelta(days=_terms_days(customer["payment_terms"])),
                year_end,
            )
        )
        if rng.random() < float(_TRADE_AR_POLICY["partial_payment_share"]):
            first_fraction, _ = _TRADE_AR_POLICY["partial_payment_fractions"]
            add_receipt(invoice, receipt_date, fraction=Decimal(str(first_fraction)))
            add_receipt(
                invoice,
                _receipt_business_day(
                    min(
                        receipt_date
                        + timedelta(
                            days=int(_TRADE_AR_POLICY["partial_payment_lag_days"])
                        ),
                        year_end,
                    )
                ),
                remainder=True,
            )
        else:
            add_receipt(invoice, receipt_date)

    # Opening-AR relief: the prior 12/31 balance exists as prior-December billings whose
    # EXACT amounts come from the shared plan the prior-TB pin recomputes. Most collect
    # in the first weeks of the year; the plan's stragglers stay partially collected and
    # open at 12/31 as the over-90 aging tail.
    if opening_plan:
        fiscal_start = year_end.replace(month=1, day=1)
        opening_customers = [
            customer
            for customer in customers
            if customer.get("sales_tax_status") == "resale_exempt"
            and customer.get("tax_exemption_certificate")
        ] or customers
        for item in opening_plan:
            customer = rng.choice(opening_customers)
            invoice_date = fiscal_start - timedelta(days=item["invoice_offset_days"])
            invoice = next_invoice(
                customer,
                invoice_date,
                item["amount"],
                exact_quantity=item["quantity"],
                exact_unit_price=item["unit_price"],
                apply_sales_tax=False,
            )
            receipt_date = _receipt_business_day(
                fiscal_start + timedelta(days=item["receipt_offset_days"])
            )
            if item["straggler"]:
                # Aged straggler: a partial collection leaves an over-90
                # remainder open at 12/31 (open < original on the aging).
                add_receipt(invoice, receipt_date, fraction=item["fraction"])
                invoice["_bucket"] = "over-90"
                invoice["_loss_rate"] = AGING_LADDER[-1][3]
                open_invoices.append(invoice)
            else:
                add_receipt(invoice, receipt_date)

    # Subsequent receipts: much of the open population collects in January —
    # the subsequent-cash-receipts schedule auditors vouch.
    for invoice in open_invoices:
        if rng.random() < float(_TRADE_AR_POLICY["subsequent_collection_share"]):
            subsequent = year_end + timedelta(
                days=int(_TRADE_AR_POLICY["subsequent_receipt_lag_days"])
            )
            while not _is_business_day(subsequent):
                subsequent += timedelta(days=1)
            # remainder: an opening straggler already collected a partial
            # payment in-year; a full-due receipt would overcollect.
            add_receipt(invoice, subsequent, remainder=True)

    _finish_ar(
        records,
        rng,
        company,
        calendar,
        year_end,
        open_invoices,
    )
    if bill_rates:
        invoices_by_id = {
            row["customer_invoice_id"]: row
            for row in records["customer_invoices"]
            if date.fromisoformat(row["invoice_date"]).year == year_end.year
        }
        amount_by_customer: dict[str, Decimal] = {}
        quantity_by_customer: dict[str, Decimal] = {}
        for invoice in invoices_by_id.values():
            customer_id = invoice["customer_id"]
            amount_by_customer[customer_id] = amount_by_customer.get(
                customer_id, Decimal("0")
            ) + Decimal(invoice["original_amount"])
        for line in records["customer_invoice_lines"]:
            invoice = invoices_by_id.get(line["customer_invoice_id"])
            if invoice is None:
                continue
            customer_id = invoice["customer_id"]
            quantity_by_customer[customer_id] = quantity_by_customer.get(
                customer_id, Decimal("0")
            ) + Decimal(line["quantity"])
        for contract in records["customer_contracts"]:
            customer_id = contract["customer_id"]
            contract["contract_price"] = str(
                amount_by_customer.get(customer_id, Decimal("0"))
            )
            contract["contract_quantity"] = str(
                quantity_by_customer.get(customer_id, Decimal("0"))
            )

    # Cutoff population: the last shipments before year end and the first
    # after, from the same numbering sequence the year used.
    december = [
        row
        for row in records["customer_invoices"]
        if date.fromisoformat(row["invoice_date"]) >= year_end - timedelta(days=30)
    ]
    december.sort(key=lambda row: row["invoice_date"])
    cutoff_index = 0
    for invoice in december[-rng.randint(3, 5) :]:
        cutoff_index += 1
        line = next(
            row
            for row in records["customer_invoice_lines"]
            if row["customer_invoice_id"] == invoice["customer_invoice_id"]
        )
        fulfillment = next(
            row
            for row in records["fulfillment_events"]
            if row["customer_invoice_line_id"] == line["customer_invoice_line_id"]
        )
        contract = next(
            row
            for row in records["customer_contracts"]
            if row["customer_contract_id"] == invoice["customer_contract_id"]
        )
        if service_book:
            purchase_order = "not applicable - service contract process"
            sales_order = str(contract["customer_contract_id"])
            quote_reference = "not applicable - contracted service rate"
        else:
            purchase_order = (
                line.get("customer_purchase_order_reference")
                or f"CPO-{year_end.year}-{cutoff_index:04d}"
            )
            sales_order = (
                line.get("sales_order_reference")
                or f"SO-{year_end.year}-{cutoff_index:04d}"
            )
            quote_reference = (
                line.get("quote_reference") or f"Q-{year_end.year}-{cutoff_index:04d}"
            )
        line.update(
            {
                "customer_purchase_order_reference": purchase_order,
                "sales_order_reference": sales_order,
                "quote_reference": quote_reference,
                "quote_date": line.get("quote_date")
                or (
                    date.fromisoformat(str(fulfillment["fulfillment_date"]))
                    - timedelta(days=14)
                ).isoformat(),
            }
        )
        fulfillment.update(
            {
                "delivery_document_reference": fulfillment["support_reference"],
                "acceptance_reference": fulfillment.get("acceptance_reference")
                or (
                    f"SERVICE-ACCEPT-{year_end.year}-{cutoff_index:04d}"
                    if service_book
                    else f"POD-{year_end.year}-{cutoff_index:04d}"
                ),
                "acceptance_date": fulfillment["fulfillment_date"],
                "performance_evidence_status": "complete",
            }
        )
        records["ar_cutoff_items"].append(
            {
                "amount": invoice["original_amount"],
                "ar_cutoff_item_id": f"CUT-{cutoff_index:03d}",
                "customer_id": invoice["customer_id"],
                "customer_invoice_id": invoice["customer_invoice_id"],
                "customer_invoice_line_id": line["customer_invoice_line_id"],
                "fulfillment_event_id": fulfillment["fulfillment_event_id"],
                "cutoff_status": "in_period",
                "fulfillment_date": fulfillment["fulfillment_date"],
                "invoice_date": invoice["invoice_date"],
                "posting_date": invoice["posting_date"],
                "side_of_year_end": "before",
                "support_reference": fulfillment["support_reference"],
                "customer_purchase_order_reference": purchase_order,
                "sales_order_reference": sales_order,
                "approved_quote_reference": quote_reference,
                "quantity": line["quantity"],
                "delivery_terms": contract["delivery_terms"],
                "transfer_of_control_point": contract["transfer_of_control_point"],
                "recognition_date": fulfillment["fulfillment_date"],
            }
        )
    # First shipments of the new year: recorded next period, listed for the auditor's
    # after-side cutoff testing. These are first-class subsequent- period source
    # documents, not cutoff-only IDs: every selected item has an invoice, line,
    # fulfillment event, and active renewal contract that can be traced independently.
    existing_invoice_numbers = [
        int(str(row["invoice_number"]).removeprefix("INV-"))
        for row in records["customer_invoices"]
        if str(row.get("invoice_number") or "").removeprefix("INV-").isdigit()
    ]
    subsequent_invoice_number = max(
        existing_invoice_numbers, default=invoice_base + sequence
    )
    for offset in range(rng.randint(2, 4)):
        cutoff_index += 1
        ship = year_end + timedelta(days=rng.randint(2, 6) + offset * 3)
        if bill_rates:
            # Staffing invoices describe the Sunday week-ending service date. The first
            # after-side item must therefore start with a billing date whose underlying
            # service week also falls after year-end.
            while ship - timedelta(days=ship.weekday() + 1) <= year_end:
                ship += timedelta(days=1)
        customer = rng.choices(customers)[0]
        customer_id = str(customer["customer_id"])
        contract = next(
            row
            for row in records["customer_contracts"]
            if row["customer_id"] == customer_id
        )

        requested_amount = _money(
            Decimal(
                str(rng.uniform(*_TRADE_AR_POLICY["subsequent_cutoff_invoice_band"]))
            )
        )
        invoice = next_invoice(customer, ship, requested_amount)
        subsequent_invoice_number += 1
        invoice["invoice_number"] = f"INV-{subsequent_invoice_number}"
        line = next(
            row
            for row in records["customer_invoice_lines"]
            if row["customer_invoice_id"] == invoice["customer_invoice_id"]
        )
        event = next(
            row
            for row in records["fulfillment_events"]
            if row["customer_invoice_id"] == invoice["customer_invoice_id"]
        )
        if service_book:
            purchase_order = "not applicable - service contract process"
            sales_order = str(contract["customer_contract_id"])
            quote_reference = "not applicable - contracted service rate"
        else:
            purchase_order = f"CPO-{year_end.year + 1}-{cutoff_index:04d}"
            sales_order = f"SO-{year_end.year + 1}-{cutoff_index:04d}"
            quote_reference = f"Q-{year_end.year + 1}-{cutoff_index:04d}"
        line.update(
            {
                "customer_purchase_order_reference": purchase_order,
                "sales_order_reference": sales_order,
                "quote_reference": quote_reference,
                "quote_date": (ship - timedelta(days=14)).isoformat(),
            }
        )
        event.update(
            {
                "delivery_document_reference": event["support_reference"],
                "acceptance_reference": (
                    f"SERVICE-ACCEPT-{year_end.year + 1}-{cutoff_index:04d}"
                    if service_book
                    else f"POD-{year_end.year + 1}-{cutoff_index:04d}"
                ),
                "acceptance_date": event["fulfillment_date"],
                "performance_evidence_status": "complete",
            }
        )
        records["ar_cutoff_items"].append(
            {
                "amount": str(invoice["original_amount"]),
                "ar_cutoff_item_id": f"CUT-{cutoff_index:03d}",
                "customer_id": customer_id,
                "customer_invoice_id": invoice["customer_invoice_id"],
                "customer_invoice_line_id": line["customer_invoice_line_id"],
                "fulfillment_event_id": event["fulfillment_event_id"],
                "cutoff_status": "in_period",
                "fulfillment_date": event["fulfillment_date"],
                "invoice_date": invoice["invoice_date"],
                "posting_date": invoice["posting_date"],
                "side_of_year_end": "after",
                "support_reference": event["support_reference"],
                "customer_purchase_order_reference": purchase_order,
                "sales_order_reference": sales_order,
                "approved_quote_reference": quote_reference,
                "quantity": line["quantity"],
                "delivery_terms": contract["delivery_terms"],
                "transfer_of_control_point": contract["transfer_of_control_point"],
                "recognition_date": event["fulfillment_date"],
            }
        )
