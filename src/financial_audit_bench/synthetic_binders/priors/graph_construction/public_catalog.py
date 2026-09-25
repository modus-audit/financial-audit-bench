"""Catalog-owned synthetic document identities."""

from __future__ import annotations

import hashlib

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)


_IDENTITIES = load_authored_policy("authored.identities.document-identities.v1").values

IDENTITY_VERSION = str(_IDENTITIES["identity_version"])
BANK_NAME_POOL = tuple(_IDENTITIES["bank_names"])
BANK_PERSONAS = tuple(dict(row) for row in _IDENTITIES["bank_personas"])
NON_ROUTABLE_ABA_PREFIX = _IDENTITIES["non_routable_aba_prefix"]
PAYROLL_MASKED_ACCOUNT_NUMBER = str(_IDENTITIES["payroll_masked_account_number"])

AUDIT_FIRM = str(_IDENTITIES["audit_firm"]["name"])
AUDIT_FIRM_ADDRESS = _IDENTITIES["audit_firm"]["address"]
AUDIT_FIRM_FAX = _IDENTITIES["audit_firm"]["fax"]
RESPONDENT_ADDRESSES = tuple(tuple(row) for row in _IDENTITIES["respondent_addresses"])
REMIT_LOCALITY_POOL = tuple(_IDENTITIES["remit_localities"])
WAREHOUSE_ADDRESSES = tuple(_IDENTITIES["warehouse_addresses"])
COMPANY_NAME_STEMS = tuple(_IDENTITIES["company_name_stems"])
COMPANY_SURNAMES = tuple(_IDENTITIES["company_surnames"])
EMPLOYEE_GIVEN_NAMES = tuple(_IDENTITIES["employee_given_names"])
COMPANY_NAME_TEMPLATES = {
    kind: tuple(templates)
    for kind, templates in _IDENTITIES["company_name_templates"].items()
}
DEFAULT_STAFFING_APPROVER = str(_IDENTITIES["default_staffing_approver"])
STAFFING_CUSTOMER_APPROVER_NAMES = tuple(
    _IDENTITIES["staffing_customer_approver_names"]
)
ROLE_ORGANIZATIONS = dict(_IDENTITIES["role_organizations"])


def _stable_index(key: str, size: int) -> int:
    digest = hashlib.sha256(f"{IDENTITY_VERSION}|{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def person_name(key: str, ordinal: int = 0) -> str:
    position = _stable_index(key, len(EMPLOYEE_GIVEN_NAMES) * len(COMPANY_SURNAMES))
    given = EMPLOYEE_GIVEN_NAMES[(position + ordinal * 17) % len(EMPLOYEE_GIVEN_NAMES)]
    surname = COMPANY_SURNAMES[
        (position // len(EMPLOYEE_GIVEN_NAMES) + ordinal * 11) % len(COMPANY_SURNAMES)
    ]
    return f"{given} {surname}"


def bank_name(key: str) -> str:
    return BANK_NAME_POOL[_stable_index(key, len(BANK_NAME_POOL))]


def customer_confirmation_email(customer_id: str) -> str:
    return str(_IDENTITIES["customer_confirmation_email_template"]).format(
        customer_id=customer_id.lower()
    )


def vendor_name(expense_class: str, ordinal: int) -> str:
    return str(_IDENTITIES["vendor_name_template"]).format(
        expense_class=expense_class.replace("_", " ").title(), ordinal=ordinal
    )


def organization_name(key: str, archetype: str, ordinal: int = 0) -> str:
    template_key = {
        "trade": "manufacturing",
        "staffing_service": "staffing_services",
    }[archetype]
    templates = COMPANY_NAME_TEMPLATES[template_key]
    position = (
        _stable_index(
            key, len(templates) * len(COMPANY_NAME_STEMS) * len(COMPANY_SURNAMES)
        )
        + ordinal
    )
    template = templates[position % len(templates)]
    position //= len(templates)
    stem = COMPANY_NAME_STEMS[position % len(COMPANY_NAME_STEMS)]
    surname = COMPANY_SURNAMES[
        position // len(COMPANY_NAME_STEMS) % len(COMPANY_SURNAMES)
    ]
    return template.format(stem=stem, surname=surname, state="")
