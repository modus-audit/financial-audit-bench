"""Direct subledger policy access and customer-master construction."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
    to_mutable_json,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    customer_confirmation_email,
    organization_name,
)

_POLICY_RESOURCES = {
    "policy.trade_ar": "authored.receivables.trade-ar.v1",
    "policy.staffing_billing": "authored.receivables.staffing-billing.v1",
    "policy.customer_master_generation.v1": ("authored.customers.master-generation.v1"),
}
SUBLEDGER_SYNTHETIC_POLICIES: dict[str, dict[str, Any]] = {
    policy_id: to_mutable_json(load_authored_policy(resource_id).values)
    for policy_id, resource_id in _POLICY_RESOURCES.items()
}
_INVENTORY_POLICY = load_authored_policy("authored.inventory.rules.v1").values
SUBLEDGER_SYNTHETIC_POLICIES.update(
    {
        "policy.inventory_operations": to_mutable_json(_INVENTORY_POLICY["operations"]),
        "policy.steel_production": to_mutable_json(
            _INVENTORY_POLICY["steel_production"]
        ),
        "policy.inventory_execution": to_mutable_json(_INVENTORY_POLICY["execution"]),
    }
)

_CUSTOMER_MASTER_SOURCE_ID = "policy.customer_master_generation.v1"
_CUSTOMER_POLICY = SUBLEDGER_SYNTHETIC_POLICIES[_CUSTOMER_MASTER_SOURCE_ID]


def _synthetic_customer_row(
    *,
    archetype: str,
    ordinal: int,
    company_id: str,
    currency_code: str,
    business_unit: str,
    payment_terms: str,
    payer_kind: str | None = None,
    customer_class: str = "services",
) -> dict[str, Any]:
    """Build one deterministic fictional customer row."""
    policy = _CUSTOMER_POLICY
    templates = policy["identity"]["templates"]
    if archetype not in templates:
        raise ValueError(f"unregistered synthetic customer archetype {archetype!r}")
    ordinal_policy = policy["identity"]["ordinal"]
    if not int(ordinal_policy["minimum"]) <= ordinal <= int(ordinal_policy["maximum"]):
        raise ValueError("synthetic customer ordinal is outside policy bounds")

    tax_policy = policy["synthetic_sales_tax"]
    if customer_class == "services":
        tax_rule = tax_policy["services_rule"]
        sales_tax_rate = tax_rule["sales_tax_rate"]
        certificate = tax_rule["tax_exemption_certificate"]
    elif (
        customer_class == "goods"
        and ordinal % int(tax_policy["resale_cadence_divisor"]) == 0
    ):
        tax_rule = tax_policy["goods_resale_exempt_rule"]
        sales_tax_rate = tax_rule["sales_tax_rate"]
        certificate_policy = tax_policy["certificate"]
        body = (
            ordinal * int(certificate_policy["body_multiplier"])
            + int(certificate_policy["body_increment"])
        ) % int(certificate_policy["body_modulus"])
        check = (
            body * int(certificate_policy["check_multiplier"])
            + int(certificate_policy["check_increment"])
        ) % int(certificate_policy["check_modulus"])
        certificate = str(certificate_policy["format"]).format(
            body_6=f"{body:0{int(certificate_policy['body_width'])}d}",
            check_2=f"{check:0{int(certificate_policy['check_width'])}d}",
        )
    elif customer_class == "goods":
        tax_rule = tax_policy["goods_taxable_rule"]
        rates = tax_policy["invented_rate_list"]
        sales_tax_rate = str(rates[(ordinal - 1) % len(rates)])
        certificate = tax_rule["tax_exemption_certificate"]
    else:
        raise ValueError(f"unsupported customer class {customer_class!r}")

    ordinal6 = f"{ordinal:06d}"
    template = templates[archetype]
    customer_id = str(template["customer_id"]).format(ordinal6=ordinal6)
    customer_name = organization_name(customer_id, archetype, ordinal)
    type_rule = policy["customer_type"]["mapping"][archetype]
    customer_type = (
        payer_kind if "copy_verbatim_from" in type_rule else type_rule["const"]
    )
    defaults = policy["binding"]["default_fields_exact"]
    return {
        "company_id": company_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_type": customer_type,
        "business_unit": business_unit,
        "currency_code": currency_code,
        "payment_terms": payment_terms,
        "salesperson": defaults["salesperson"],
        "account_status": defaults["account_status"],
        "confirmation_contact": customer_confirmation_email(customer_id),
        "sales_tax_status": tax_rule["sales_tax_status"],
        "sales_tax_rate": sales_tax_rate,
        "tax_exemption_certificate": certificate,
        "related_party": defaults["related_party"],
        "billing_cycle_class": defaults["billing_cycle_class"],
    }
