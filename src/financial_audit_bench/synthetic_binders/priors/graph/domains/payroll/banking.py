"""Payroll clearing-bank account projection."""

from __future__ import annotations

from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    PAYROLL_MASKED_ACCOUNT_NUMBER,
)


@REGISTRY.rule(
    "derive_payroll_bank_account",
    inputs=("payroll_run", "bank_account"),
    outputs="payroll_bank_account",
    gate="has_payroll",
)
def derive_payroll_bank_account(
    runs: list[dict[str, Any]],
    bank_account: dict[str, Any],
) -> list[dict[str, str]]:
    """One zero-balance payroll clearing account when the company runs payroll: the operating account funds NET pay each pay date and the run disburses the same day, so the account opens and closes at zero and never touches the trial balance — it exists to carry payroll statements in the public synthetic package."""
    if not runs:
        return []
    return [
        {
            "bank_account_id": "BANK-003",
            "account_name": "Payroll",
            "account_type": "checking",
            "bank_name": bank_account["bank_name"],
            "masked_account_number": PAYROLL_MASKED_ACCOUNT_NUMBER,
            "opening_balance": "0.00",
            "closing_balance": "0.00",
        }
    ]
