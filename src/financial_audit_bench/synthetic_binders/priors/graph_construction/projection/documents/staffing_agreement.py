"""Executed staffing service agreements and assignment rate cards."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.documents._shared import (
    World,
    company_name,
    entry,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.formats import (
    fmt_date,
    fmt_money,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.textdoc import (
    TextDoc,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    DEFAULT_STAFFING_APPROVER,
    STAFFING_CUSTOMER_APPROVER_NAMES,
)


def _applicable(world: World) -> bool:
    return str(world.get("business_type") or "") == "staffing_services" and bool(
        world.get("staffing_time_entry")
    )


def _filename(name: str) -> str:
    clean = "".join(
        character for character in name if character.isalnum() or character in " -"
    )
    return f"{clean} Staffing Services Agreement.txt"


STAFFING_AGREEMENT_SAMPLE_SIZE = 3


def render(world: World, output_dir: Path) -> list[dict[str, Any]]:
    customers = {row["customer_id"]: row for row in world.get("customer") or []}
    contracts = {
        row["customer_id"]: row for row in world.get("customer_contract") or []
    }
    employees = {row["employee_id"]: row for row in world.get("employee") or []}
    time_rows = world.get("staffing_time_entry") or []
    provider_signer = next(
        (
            row["full_name"]
            for row in world.get("employee") or []
            if row.get("employee_class") != "assigned_worker"
            and row.get("role") in {"branch_manager", "controller", "account_manager"}
        ),
        DEFAULT_STAFFING_APPROVER,
    )
    eligible = []
    for customer_id, customer in customers.items():
        rows = [row for row in time_rows if row["customer_id"] == customer_id]
        if contracts.get(customer_id) is None or not rows:
            continue
        billed = sum(
            Decimal(str(row["bill_rate"])) * Decimal(str(row["billable_hours"]))
            for row in rows
        )
        eligible.append(
            (billed, customer["customer_name"], customer_id, customer, rows)
        )

    # Retain a deterministic sample of the largest customer relationships.
    selected = sorted(eligible, key=lambda row: (-row[0], row[1]))[
        :STAFFING_AGREEMENT_SAMPLE_SIZE
    ]
    entries: list[dict[str, Any]] = []
    for selection_index, (_billed, _name, customer_id, customer, rows) in enumerate(
        selected
    ):
        contract = contracts.get(customer_id)
        assert contract is not None
        rate_card = sorted(
            {
                (
                    row["assignment_reference"],
                    str(employees[row["employee_id"]]["role"])
                    .replace("_", " ")
                    .title(),
                    row["bill_rate"],
                    row["pay_rate"],
                    row["assignment_start_date"],
                    row["assignment_end_date"],
                    row.get(
                        "assignment_authorization_reference",
                        row["assignment_reference"],
                    ),
                    row.get(
                        "rate_authorization_reference", row["assignment_reference"]
                    ),
                    row.get("rate_authorized_date", row["assignment_start_date"]),
                )
                for row in rows
            }
        )
        doc = TextDoc()
        doc.center("STAFFING SERVICES AGREEMENT")
        doc.rule("=")
        doc.pair("Staffing provider", company_name(world))
        doc.pair("Customer", customer["customer_name"])
        doc.pair("Effective date", fmt_date(contract["contract_start_date"]))
        doc.pair("Expiration date", fmt_date(contract["contract_end_date"]))
        start = date.fromisoformat(contract["contract_start_date"])
        execution_date = start - timedelta(days=15)
        doc.pair("Execution date", fmt_date(execution_date.isoformat()))
        doc.pair(
            "Annual committed spend", "None; charges arise only from approved time"
        )
        doc.line()
        doc.line("1. SERVICES AND APPROVAL")
        doc.wrapped(
            "Provider will supply assigned personnel for the roles and periods "
            "shown in the attached rate card. Customer's designated supervisor "
            "will approve weekly time through the timekeeping system. Only "
            "approved hours may be invoiced."
        )
        doc.line()
        doc.line("2. RATES AND OVERTIME")
        doc.wrapped(
            "Approved regular and overtime hours are billed at the stated "
            "assignment rate. The rate card intentionally uses a straight bill "
            "rate for overtime; Provider remains responsible for legally required "
            "overtime pay to its employees. Any premium bill rate requires a "
            "written rate-card amendment signed by both parties."
        )
        doc.line()
        doc.table(
            [
                "Assignment",
                "Role",
                "Bill",
                "Pay",
                "Start",
                "End",
                "Assignment auth",
                "Rate auth / date",
            ],
            [
                [
                    reference,
                    role,
                    fmt_money(Decimal(rate)),
                    fmt_money(Decimal(pay_rate)),
                    fmt_date(start),
                    fmt_date(end),
                    assignment_auth,
                    f"{rate_auth} / {fmt_date(auth_date)}",
                ]
                for reference, role, rate, pay_rate, start, end, assignment_auth, rate_auth, auth_date in rate_card
            ],
            [12, 14, 7, 7, 9, 9, 10, 10],
        )
        doc.line()
        doc.line("3. INVOICING AND PAYMENT")
        doc.wrapped(
            f"Provider will invoice approved time under {contract['payment_terms']}. "
            "Approved-time batches may be invoiced as soon as approved and must "
            "be invoiced no later than 35 calendar days after the latest weekly "
            "time included in the batch. Delayed batches require a documented "
            "billing exception approved by the account manager. "
            "Customer must identify disputed hours within ten business days. "
            "Undisputed amounts remain payable when due."
        )
        doc.line()
        doc.line("4. EMPLOYMENT RESPONSIBILITIES")
        doc.wrapped(
            "Provider is the employer of assigned personnel and is responsible "
            "for payroll, withholding, workers' compensation, and employment "
            "records. Customer controls day-to-day site direction and safety "
            "requirements while an assignment is active."
        )
        doc.line()
        doc.line("PROVIDER: " + company_name(world))
        doc.line(f"By: /s/ {provider_signer}, Account Manager")
        doc.line(f"Date: {fmt_date(execution_date.isoformat())}")
        doc.line()
        doc.line("CUSTOMER: " + customer["customer_name"])
        customer_signer = STAFFING_CUSTOMER_APPROVER_NAMES[
            selection_index % len(STAFFING_CUSTOMER_APPROVER_NAMES)
        ]
        doc.line(f"By: /s/ {customer_signer}, Operations Director")
        doc.line(f"Date: {fmt_date(execution_date.isoformat())}")
        path = output_dir / "agreements" / _filename(customer["customer_name"])
        entries.append(
            entry(
                f"DOC-STAFFING-AGREEMENT-{contract['customer_contract_id']}",
                "revenue",
                path,
                doc.write(path),
            )
        )
    return entries
