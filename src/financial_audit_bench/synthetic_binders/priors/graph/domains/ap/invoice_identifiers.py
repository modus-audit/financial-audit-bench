"""AP population admission and catalog-backed vendor invoice identities."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    prior_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

REGISTRY.sample(
    "vendor",
    inputs=("company_context",),
    gate="has_ap_activity",
    rule_name="vendor_population",
)
REGISTRY.sample(
    "vendor_invoice",
    inputs=("vendor",),
    gate="has_ap_activity",
    rule_name="sample_vendor_invoices",
)
REGISTRY.sample(
    "ap_payment",
    inputs=("vendor_invoice",),
    gate="has_ap_activity",
    rule_name="sample_ap_payments",
)
REGISTRY.sample(
    "purchase_order",
    inputs=("vendor",),
    rule_name="sample_purchase_orders",
)
REGISTRY.sample(
    "goods_receipt",
    inputs=("purchase_order",),
    rule_name="sample_goods_receipts",
)
REGISTRY.sample(
    "accrued_expense",
    inputs=(
        "vendor",
        "vendor_invoice",
        "fiscal_calendar",
        "fixed_asset",
        "company_feature_profile",
    ),
    rule_name="sample_accrued_expenses",
)
# Public synthetic invoice-number grammar families: bare sequential counters, INV-
# prefixes, vendor-initial prefixes, year-embedded sequences, and short-run counters.
# The grammar is vendor-sticky and families are dealt from a reshuffled deck so
# unrelated vendors never converge on one shared format (the strongest structured-data
# repetition of a single universal template).
_invoice_identifier_values = load_authored_policy(
    "authored.global.operating-policy.v1"
).values["ap_invoice_identifiers"]
INVOICE_NUMBER_GRAMMARS = tuple(_invoice_identifier_values["grammars"])
# Per-grammar counter start ranges and per-invoice step ranges (gaps = the
# vendor's other customers landing between our invoices).
INVOICE_NUMBER_COUNTERS = {
    str(grammar): (tuple(ranges[0]), tuple(ranges[1]))
    for grammar, ranges in _invoice_identifier_values["counter_ranges"].items()
}
if tuple(INVOICE_NUMBER_COUNTERS) != INVOICE_NUMBER_GRAMMARS:
    raise ValueError("invoice identifier grammars and counter ranges differ")
del _invoice_identifier_values

# Tax authorities issue statement/bill numbers, never INV- commercial
# numbering.
TAX_NOTICE_NUMBER_STYLE = lambda n: f"{n:07d}"  # noqa: E731


def _vendor_initials(name: str) -> str:
    """Uppercase initials of the vendor's leading words for the
    initials-prefixed grammar ("Bluestem Staffing Group" -> "BSG")."""
    letters = [word[0].upper() for word in name.split() if word[0].isalpha()]
    return "".join(letters[:3]) or "V"


def _format_invoice_number(
    grammar: str, n: int, vendor_name: str, document_date
) -> str:
    if grammar == "inv_prefixed":
        return f"INV-{n:05d}"
    if grammar == "initials_prefixed":
        return f"{_vendor_initials(vendor_name)}-{n}"
    if grammar == "year_embedded":
        return f"{str(document_date)[:4]}-{n:04d}"
    return f"{n}"


@REGISTRY.finalize("vendor_invoice")
def assign_vendor_invoice_numbers(world: dict[str, Any]) -> None:
    """Stamp each payable event with the vendor's own invoice number."""
    invoices = world["vendor_invoice"]
    # These controls are populated for construction overhead invoices. All other AP
    # families still carry the declared nullable fields so adding a business-specific
    # control never drifts the shared node shape.
    for row in invoices:
        for field in (
            "cost_designation",
            "natural_account_basis",
            "approved_by",
            "approval_date",
            "approval_reference",
            "inventory_movement_id",
            "inventory_item_id",
        ):
            row.setdefault(field, None)
        if row.get("cost_designation"):
            document_date = date.fromisoformat(str(row["document_date"]))
            row["approval_date"] = prior_business_day(
                document_date - timedelta(days=1)
            ).isoformat()
    if not invoices:
        return
    vendor_names = {row["vendor_id"]: row["name"] for row in world.get("vendor", [])}
    fingerprint = "|".join(
        f"{row['vendor_invoice_id']}:{row['amount']}:{row['document_date']}"
        for row in sorted(invoices, key=lambda row: row["vendor_invoice_id"])
    )
    rng = random.Random(f"vendor_invoice_numbers|{fingerprint}")
    deck: list[str] = []
    grammars: dict[str, str] = {}
    counters: dict[str, int] = {}
    ordered = sorted(
        invoices,
        key=lambda row: (
            row["vendor_id"],
            row["document_date"],
            row["vendor_invoice_id"],
        ),
    )
    for row in ordered:
        vendor_id = row["vendor_id"]
        if vendor_id not in grammars:
            if row["description"] == "taxes_and_licenses":
                grammars[vendor_id] = "tax_notice"
                counters[vendor_id] = rng.randrange(100, 90000)
            else:
                if not deck:
                    deck = list(INVOICE_NUMBER_GRAMMARS)
                    rng.shuffle(deck)
                grammar = deck.pop()
                grammars[vendor_id] = grammar
                counters[vendor_id] = rng.randrange(
                    *INVOICE_NUMBER_COUNTERS[grammar][0]
                )
        grammar = grammars[vendor_id]
        if grammar == "tax_notice":
            counters[vendor_id] += rng.randint(1, 40)
            row["vendor_invoice_number"] = TAX_NOTICE_NUMBER_STYLE(counters[vendor_id])
            continue
        counters[vendor_id] += rng.randint(*INVOICE_NUMBER_COUNTERS[grammar][1])
        row["vendor_invoice_number"] = _format_invoice_number(
            grammar,
            counters[vendor_id],
            vendor_names.get(vendor_id, ""),
            row["document_date"],
        )
