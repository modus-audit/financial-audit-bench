"""Deterministic entity, bank-account, signer, and accounting-user samplers."""

from __future__ import annotations

import random
from datetime import date
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    IDENTITY_VERSION,
    bank_name,
    person_name,
)


def _sample_bank_account(self, world: World) -> dict[str, Any]:
    rng = random.Random(
        f"{IDENTITY_VERSION}|bank-account|{self.seed}|{self.business_type}"
    )
    last_four = f"{rng.randrange(10_000):04d}"
    target_year = int(world["company_context"]["target_fiscal_year"])
    opened = date(target_year, 1, 1)
    value = {
        "bank_account_id": "BANK-001",
        "company_id": world["company_context"]["company_id"],
        "bank_name": bank_name(f"{self.seed}|{self.business_type}|operating-bank"),
        "account_name": "Operating Checking",
        "masked_account_number": f"****{last_four}",
        "last_four_digits": last_four,
        "account_type": "checking",
        "currency_code": world["company_context"]["currency_code"],
        "opening_date": opened.isoformat(),
        "closing_date": None,
        "restriction_status": "unrestricted",
        "sweep_arrangement_description": None,
    }
    return value


def _sample_bank_account_signer(self, world: World) -> list[dict[str, Any]]:
    """Generate one fictional active signer for the synthetic account."""
    name = person_name(f"{self.seed}|{self.business_type}|bank-account-signer")
    account = world["bank_account"]
    rows = [
        {
            "bank_account_signer_id": "SIGNER-001",
            "bank_account_id": account["bank_account_id"],
            "signer_name": name,
            "signer_title": "Authorized Signer",
            "authorization_start_date": account["opening_date"],
            "authorization_end_date": None,
            "authorization_status": "active",
        }
    ]
    return rows


def _sample_accounting_user(self, world: World) -> list[dict[str, Any]]:
    """Generate fictional preparer/approver identities and a system user."""
    identity_key = f"{self.seed}|{self.business_type}|accounting-user"
    names = [person_name(identity_key, index) for index in range(2)]
    target_year = int(world["company_context"]["target_fiscal_year"])
    company_id = world["company_context"]["company_id"]
    created = date(target_year, 1, 1).isoformat()
    changed = date(target_year, 1, 1).isoformat()
    users = [
        {
            "accounting_user_id": "USER-PREPARER",
            "company_id": company_id,
            "user_name": names[0],
            "role_name": "Senior Accountant",
            "employment_status": "active",
            "account_status": "active",
            "privilege_level": "preparer",
            "created_date": created,
            "changed_date": changed,
        },
        {
            "accounting_user_id": "USER-APPROVER",
            "company_id": company_id,
            "user_name": names[1],
            "role_name": "Controller",
            "employment_status": "active",
            "account_status": "active",
            "privilege_level": "approver",
            "created_date": created,
            "changed_date": changed,
        },
        {
            "accounting_user_id": "USER-SYSTEM",
            "company_id": company_id,
            "user_name": "SYSTEM",
            "role_name": "System Service Account",
            "employment_status": "active",
            "account_status": "active",
            "privilege_level": "system",
            "created_date": created,
            "changed_date": changed,
        },
    ]
    return users
