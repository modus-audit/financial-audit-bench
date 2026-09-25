"""Compact accounts-receivable confirmations."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents.confirmation_support import (
    _AR_SIGNER_TITLES,
    _active_signer,
    _confirmation_cycle,
    _control_id,
    _doc_rng,
    _person,
    _received_stamp,
    _return_instructions,
    _safe_filename,
    _signature,
    _signature_style,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
    fmt_money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    AUDIT_FIRM,
    RESPONDENT_ADDRESSES,
)


def _render_ar_confirmations(
    world: World, output_dir: Path, audit_plan: dict[str, Any]
) -> list[dict[str, Any]]:
    aging: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in world.get("accounts_receivable_aging") or []:
        aging[str(row["customer_id"])].append(row)
    customer_types = {
        str(row["customer_id"]): str(row.get("customer_type") or "")
        for row in world.get("customer") or []
    }
    selected = sorted(
        (
            customer_id
            for customer_id in aging
            if customer_types.get(customer_id) != "government"
        ),
        key=lambda customer_id: (
            -sum(
                (Decimal(str(row["open_amount"])) for row in aging[customer_id]),
                Decimal("0"),
            ),
            customer_id,
        ),
    )[:3]

    signer = _active_signer(world)
    entries: list[dict[str, Any]] = []
    for index, customer_id in enumerate(selected, 1):
        rows = aging[customer_id]
        customer = str(rows[0]["customer_name"])
        balance = sum((Decimal(str(row["open_amount"])) for row in rows), Decimal("0"))
        as_of, requested, responded, received = _confirmation_cycle(
            world, f"ar|{customer_id}", audit_plan
        )
        control_id = _control_id("AR", customer_id)

        rng = _doc_rng(world, f"ar.reply|{customer_id}")
        responder = _person(rng)
        responder_title = rng.choice(_AR_SIGNER_TITLES)
        street, city, phone = RESPONDENT_ADDRESSES[
            (index - 1) % len(RESPONDENT_ADDRESSES)
        ]
        doc = TextDoc()
        doc.center(company_name(world))
        doc.center("ACCOUNTS RECEIVABLE CONFIRMATION")
        doc.rule("=")
        doc.pair("Customer", customer)
        doc.pair("Address", f"{street}, {city}")
        doc.pair("Balance as of", fmt_date(as_of))
        doc.pair("Date of request", fmt_date(requested.isoformat()))
        doc.wrapped(
            f"Our auditors, {AUDIT_FIRM}, request direct confirmation of the "
            "balance below. This is not a request for payment."
        )
        _return_instructions(doc)
        doc.pair("Auditor control ID", control_id)
        if signer:
            doc.signature(
                signer["signer_name"],
                style=_signature_style(world, signer["signer_name"]),
            )
            doc.line(f"  {signer['signer_name']}, {signer['signer_title']}")
        doc.line()
        doc.tie(f"ar_confirmed_balance_{index}", "Balance", fmt_money(balance))
        doc.rule()
        doc.checkbox(
            "AGREE with the balance above.",
            checked=True,
            mode="typed",
        )
        doc.checkbox(
            "DISAGREE with the balance above.",
            checked=False,
            mode="typed",
        )
        if responded and received:
            doc.pair("Difference", fmt_money(0))
            _signature(
                doc,
                responder,
                responder_title,
                responded,
                contact=phone,
                style=_signature_style(world, responder),
                mode="typed",
            )
            _received_stamp(doc, received, "mail")
        else:
            doc.line("NO RESPONSE RECEIVED; alternative procedures required.")

        path = output_dir / f"AR Confirmation - {_safe_filename(customer)}.txt"
        entries.append(
            entry(
                f"DOC-AR-CONFIRMATION-{index}",
                "accounts_receivable",
                path,
                doc.write(path),
            )
        )
    return entries
