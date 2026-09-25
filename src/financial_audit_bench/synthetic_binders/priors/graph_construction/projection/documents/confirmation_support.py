"""Shared deterministic lifecycle and layout helpers for confirmations."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.authored.core_company_cash import (
    COMPANY_SURNAMES,
    EMPLOYEE_GIVEN_NAMES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    AUDIT_FIRM,
    AUDIT_FIRM_ADDRESS,
    AUDIT_FIRM_FAX,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)

# The engagement audit firm (shared with the prior-year report) and where
# respondents mail or fax the completed form.
_AR_SIGNER_TITLES = (
    "Controller",
    "Accounts Payable Manager",
    "Accounting Manager",
    "Assistant Controller",
)

_BANK_OFFICER_TITLES = (
    "Operations Officer",
    "Client Services Officer",
    "Deposit Operations Manager",
)


def _doc_rng(world: World, label: str) -> random.Random:
    """Deterministic per-world, per-document RNG (same convention as
    identity._world_rng: keyed to drawn world values, not a constant)."""
    fingerprint = "|".join(
        (
            label,
            str(world["prior_period_bank_balance"]["ending_balance"]),
            str(world["fiscal_calendar"]["fiscal_year"]),
        )
    )
    return random.Random(fingerprint)


def _person(rng: random.Random) -> str:
    return f"{rng.choice(EMPLOYEE_GIVEN_NAMES)} {rng.choice(COMPANY_SURNAMES)}"


# Stable signer identities shared by confirmation documents.
_SIGNATURE_STYLE_COUNT = 12


def _signature_style(world: World, name: str) -> int:
    """Assign each signer a stable display style within the binder."""
    styles: dict[str, int] = world.setdefault("__signature_styles", {})
    if name in styles:
        return styles[name]
    style = len(styles) % _SIGNATURE_STYLE_COUNT
    styles[name] = style
    return style


def _safe_filename(name: str) -> str:
    """Drop commas and trailing punctuation from a company filename fragment."""
    return name.replace(",", "").rstrip(". ")


def _control_id(kind: str, subject: str) -> str:
    token = "".join(character for character in subject.upper() if character.isalnum())[
        :10
    ]
    return f"{kind.upper()}-{token}"


def _confirmation_cycle(
    world: World, label: str, audit_plan: dict[str, Any]
) -> tuple[str, date, date, date]:
    """(as-of date, request, response, received) for one confirmation."""
    from financial_audit_bench.synthetic_binders.priors.graph.business_days import (
        is_business_day,
    )

    def business(day: date) -> date:
        # Letters are typed, signed, and stamped on business days — a
        # Confirmation replies use business dates.
        while not is_business_day(day):
            day += timedelta(days=1)
        return day

    rng = _doc_rng(world, f"confirmation.dates|{label}")
    as_of = date.fromisoformat(str(world["fiscal_calendar"]["end_date"])[:10])
    milestones = audit_plan["engagement"]["milestones"]
    planned_send = milestones.get("confirmation_send_date")
    if planned_send:
        requested = business(date.fromisoformat(str(planned_send)))
    else:
        batch = _doc_rng(world, "confirmation.request_batch")
        requested = business(as_of + timedelta(days=batch.randint(3, 13)))
    response_cutoff = date.fromisoformat(
        str(
            milestones.get("confirmation_response_cutoff")
            or milestones.get("report_date")
            or as_of + timedelta(days=75)
        )
    )
    responded = min(
        business(
            max(
                requested + timedelta(days=rng.randint(6, 44)),
                as_of + timedelta(days=1),
            )
        ),
        response_cutoff - timedelta(days=1),
    )
    received = min(
        business(responded + timedelta(days=rng.randint(1, 9))), response_cutoff
    )
    return as_of.isoformat(), requested, responded, received


def _active_signer(world: World) -> dict[str, str] | None:
    for row in world.get("bank_account_signer") or []:
        if row["authorization_status"] == "active":
            return row
    return None


def _return_instructions(doc: TextDoc) -> None:
    doc.wrapped("Please complete and return this form directly to our auditors:")
    doc.line(f"  {AUDIT_FIRM}")
    doc.line(f"  {AUDIT_FIRM_ADDRESS}")
    doc.line(f"  Mail, or fax to {AUDIT_FIRM_FAX}")


def _signature(
    doc: TextDoc,
    name: str,
    title: str,
    completed: date,
    contact: str = "",
    style: int | None = None,
    mode: str = "handwritten",
) -> None:
    doc.line()
    doc.signature(name, style=style, mode=mode)
    doc.line(f"  {name}, {title}")
    if contact:
        doc.line(f"  {contact}")
    doc.line(f"  Date completed: {fmt_date(completed.isoformat())}")


def _received_stamp(doc: TextDoc, received: date, return_channel: str = "mail") -> None:
    doc.line()
    label = "RECEIVED" if return_channel in {"mail", "fax"} else "RECEIVED BY EMAIL"
    doc.stamp([label, fmt_date(received.isoformat()), AUDIT_FIRM.upper()])
    # The firm scans returned confirmations into the file on receipt, so the
    # Retain the stamped receipt date on the document model.


def _tie_table_rows(doc: TextDoc, ties: list[tuple[str, str]]) -> None:
    """Tie each just-rendered table row's trailing value cell."""
    start = len(doc.lines) - len(ties)
    for offset, (field, value) in enumerate(ties):
        doc.ties.append({"field": field, "line": start + offset + 1, "value": value})
