"""Client-plausible identifiers, allocated once per package."""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    BANK_PERSONAS,
    NON_ROUTABLE_ABA_PREFIX,
)
from financial_audit_bench.synthetic_binders.contracts.journal_risk import (
    assign_stable_public_journal_ids,
)

World = dict[str, Any]


def synthetic_routing_number(rng: random.Random) -> str:
    """Return a numeric-looking value that cannot be a valid ABA number."""
    digits = [int(d) for d in NON_ROUTABLE_ABA_PREFIX] + [
        rng.randrange(10) for _ in range(6)
    ]
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    partial = sum(w * d for w, d in zip(weights, digits))
    valid_check_digit = (10 - partial % 10) % 10
    digits.append((valid_check_digit + 1) % 10)
    return "".join(str(d) for d in digits)


def _world_rng(world: World, label: str) -> random.Random:
    """Deterministic RNG keyed to drawn world values, not the constant case id."""
    fingerprint = "|".join(
        (
            label,
            world["prior_period_bank_balance"]["ending_balance"],
            str(len(world["cash_movement"])),
            str(len(world["vendor_invoice"])),
        )
    )
    return random.Random(fingerprint)


class PackageIdentity:
    """All client-visible identifiers for one generated package."""

    def __init__(self, world: World) -> None:
        rng = _world_rng(world, "identity")
        account = world["bank_account"]
        self.bank = dict(rng.choice(BANK_PERSONAS))
        self.bank["name"] = account["bank_name"]
        # Full number ends in the world's last four, so the statement's
        # printed number matches the rec's masked reference.
        self.account_number = (
            f"{rng.randrange(10**5, 10**6)}{account['last_four_digits']}"
        )
        # One routing identity per package, so the check faces, the bank rec, and any
        # statement footer that prints it all agree. The value is deliberately non-
        # routable and fails the ABA checksum.
        self.routing_number = synthetic_routing_number(rng)
        self.check_numbers = self._allocate_checks(world)
        # Vendor invoice numbers live on the payable event itself (rules/ap.py
        # assign_vendor_invoice_numbers): the GL memo, the AP detail, and the invoice
        # copy must all show the number the event carries, so this map only reads it.
        self.invoice_numbers = {
            row["vendor_invoice_id"]: row["vendor_invoice_number"]
            for row in world["vendor_invoice"]
        }
        self.journal_numbers = self._allocate_journals(world)
        self.invoice_sample = self._sample_invoices(world)

    def masked_account(self) -> str:
        style = self.bank["mask"]
        if style == "full":
            return self.account_number
        if style == "x6":
            return "XXXXXX" + self.account_number[-4:]
        return "****" + self.account_number[-4:]

    def _allocate_checks(self, world: World) -> dict[str, str]:
        """Allocate sequential checks in issue-date order."""
        invoices = {
            row["vendor_invoice_id"]: row for row in world.get("vendor_invoice", [])
        }
        drawn: list[tuple[str, str, str]] = []
        for payment in world["ap_payment"]:
            if payment["payment_method"] == "check":
                vendor = invoices.get(payment["vendor_invoice_id"], {}).get(
                    "vendor_id", payment["ap_payment_id"]
                )
                drawn.append(
                    (
                        payment["posting_date"],
                        f"{vendor}:{payment['posting_date']}",
                        payment["ap_payment_id"],
                    )
                )
        for movement in world["cash_movement"]:
            if (
                movement["transaction_class"] == "check"
                and not movement["ap_payment_id"]
            ):
                drawn.append(
                    (
                        movement["bank_activity_date"],
                        movement["cash_movement_id"],
                        movement["cash_movement_id"],
                    )
                )
        drawn.sort()
        number_by_key: dict[str, str] = {}
        for _, key, _ in drawn:
            if key not in number_by_key:
                number_by_key[key] = str(10000 + len(number_by_key) + 1)
        return {record_id: number_by_key[key] for _, key, record_id in drawn}

    def _allocate_journals(self, world: World) -> dict[str, str]:
        """Return the journal numbers assigned by the journal finalizer."""
        assign_stable_public_journal_ids(world)
        return {
            str(entry["journal_entry_id"]): str(entry["public_journal_entry_id"])
            for entry in world.get("journal_entry", [])
        }

    @staticmethod
    def _sample_invoices(world: World) -> frozenset[str]:
        """Which invoices get a rendered copy: the auditor's sample."""
        invoices = world["vendor_invoice"]
        paid: dict[str, Decimal] = {}
        for payment in world["ap_payment"]:
            paid[payment["vendor_invoice_id"]] = paid.get(
                payment["vendor_invoice_id"], Decimal("0")
            ) + Decimal(payment["amount"])
        open_ids = {
            row["vendor_invoice_id"]
            for row in invoices
            if Decimal(row["amount"]) > paid.get(row["vendor_invoice_id"], Decimal("0"))
        }
        chosen = open_ids | {
            max(rows, key=lambda r: Decimal(r["amount"]))["vendor_invoice_id"]
            for rows in _group_by(invoices, "vendor_id").values()
        }
        return frozenset(chosen)


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def package_identity(world: World) -> PackageIdentity:
    cached = world.get("__package_identity")
    if isinstance(cached, PackageIdentity):
        return cached
    identity = PackageIdentity(world)
    try:
        world["__package_identity"] = identity
    except TypeError:
        pass
    return identity
