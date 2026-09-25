"""Subsequent settlements shared by AP and January bank evidence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
    add_business_days,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.identity import (
    package_identity,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.op_registry import (
    World,
)


def subsequent_open_invoice_settlements(world: World) -> list[dict[str, Any]]:
    """Build one shared subsequent-payment population for retained AP evidence."""
    paid: dict[str, Decimal] = {}
    for payment in world["ap_payment"]:
        invoice_id = str(payment["vendor_invoice_id"])
        paid[invoice_id] = paid.get(invoice_id, Decimal("0")) + Decimal(
            str(payment["amount"])
        )

    year = int(world["fiscal_calendar"]["fiscal_year"]) + 1
    vendors = {row["vendor_id"]: row["name"] for row in world["vendor"]}
    settlements: list[dict[str, Any]] = []
    for ordinal, invoice in enumerate(world["vendor_invoice"], start=1):
        invoice_id = str(invoice["vendor_invoice_id"])
        open_amount = Decimal(str(invoice["amount"])) - paid.get(
            invoice_id, Decimal("0")
        )
        if open_amount <= 0:
            continue
        settlements.append(
            {
                "amount": str(open_amount),
                "document_date": invoice["document_date"],
                "payable_gl_account_id": invoice["payable_gl_account_id"],
                "payee": vendors[invoice["vendor_id"]],
                "payment_date": add_business_days(
                    date(year, 1, 1), 1 + (ordinal - 1) % 19
                ).isoformat(),
                "service_period_end": invoice["service_period_end"],
                "vendor_invoice_id": invoice_id,
            }
        )

    payable_account = (
        world["vendor_invoice"][0]["payable_gl_account_id"]
        if world["vendor_invoice"]
        else "GL-AP-001"
    )
    for rni in world.get("rni_accrual_item", []):
        rni_id = str(rni["rni_accrual_item_id"])
        invoice_date = str(rni["subsequent_invoice_date"])
        payment_date = add_business_days(
            date.fromisoformat(invoice_date), 5
        ).isoformat()
        vendor_name = str(vendors[rni["vendor_id"]])
        initials = "".join(word[0] for word in vendor_name.split()[:2]).upper()
        settlements.append(
            {
                "amount": str(rni["amount"]),
                "cross_cycle_evidence_reference": None,
                "document_date": invoice_date,
                "invoice_number": (
                    f"{initials}-{invoice_date.replace('-', '')[:6]}-{rni_id[-2:]}"
                ),
                "payable_gl_account_id": payable_account,
                "payee": vendor_name,
                "payment_date": payment_date,
                "service_period_end": rni["receipt_date"],
                "vendor_invoice_id": rni_id,
            }
        )

    settlements.sort(key=lambda row: (row["payment_date"], row["vendor_invoice_id"]))
    identity = package_identity(world)
    account = world["bank_account"]
    for index, row in enumerate(settlements, start=1):
        row["payment_id"] = f"PAY-{year}-{index:04d}"
        row["payment_method"] = "ACH" if index % 3 else "Check"
        row["payment_reference"] = (
            f"ACH-{year}{index:06d}"
            if row["payment_method"] == "ACH"
            else str(41000 + index)
        )
        row["source_bank_account"] = (
            f"{account['account_name']} {account['masked_account_number']}"
        )
        row["clearing_date"] = add_business_days(
            date.fromisoformat(str(row["payment_date"])), 1
        ).isoformat()
        row["invoice_number"] = row.get(
            "invoice_number"
        ) or identity.invoice_numbers.get(
            row["vendor_invoice_id"], row["vendor_invoice_id"]
        )
    return settlements
