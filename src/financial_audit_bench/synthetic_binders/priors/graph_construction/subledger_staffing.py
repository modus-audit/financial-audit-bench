"""Approved staffing time, assignment, and billing-support construction."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_admission import (
    SUBLEDGER_SYNTHETIC_POLICIES,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.subledger_common import (
    CENT,
    _is_business_day,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.public_catalog import (
    DEFAULT_STAFFING_APPROVER,
    STAFFING_CUSTOMER_APPROVER_NAMES,
)

_STAFFING_POLICY = SUBLEDGER_SYNTHETIC_POLICIES["policy.staffing_billing"]


def _build_staffing_time_entries(
    records: dict[str, list[dict[str, Any]]],
    calendar: dict[str, Any],
    employees: list[dict[str, Any]],
) -> None:
    """Build the approved time population that supports staffing billings."""
    workers = [
        row for row in employees if row.get("employee_class") == "assigned_worker"
    ]
    if not workers:
        assigned_roles = {
            "warehouse_associate",
            "production_associate",
            "administrative_associate",
            "customer_service_representative",
            "forklift_operator",
        }
        workers = [row for row in employees if row.get("role") in assigned_roles]
    if not workers:
        raise ValueError(
            f"staffing invoices require assigned workers; employee rows={len(employees)}"
        )
    start = date.fromisoformat(str(calendar["start_date"]))
    end = date.fromisoformat(str(calendar["end_date"]))
    invoices = {row["customer_invoice_id"]: row for row in records["customer_invoices"]}
    lines = sorted(
        (
            row
            for row in records["customer_invoice_lines"]
            if row.get("uom") == "hours"
            and start
            <= date.fromisoformat(invoices[row["customer_invoice_id"]]["invoice_date"])
            <= end
        ),
        # Allocate in billing chronology so a later invoice cannot consume earlier
        # service capacity that an already-issued invoice needs. For invoices on the
        # same date, place the most rate-constrained work first.
        key=lambda row: (
            invoices[row["customer_invoice_id"]]["invoice_date"],
            Decimal(str(row["unit_price"])),
            invoices[row["customer_invoice_id"]]["customer_id"],
            row["customer_invoice_line_id"],
        ),
    )
    first_week_end = start + timedelta(days=(4 - start.weekday()) % 7)
    first_full_billing_date = first_week_end + timedelta(days=7)
    for invoice in invoices.values():
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        if start <= invoice_date < first_full_billing_date:
            shift = first_full_billing_date - invoice_date
            invoice["invoice_date"] = first_full_billing_date.isoformat()
            invoice["posting_date"] = first_full_billing_date.isoformat()
            invoice["due_date"] = (
                date.fromisoformat(invoice["due_date"]) + shift
            ).isoformat()
    service_window_start = start
    week = service_window_start + timedelta(
        days=(4 - service_window_start.weekday()) % 7
    )
    weeks: list[date] = []
    while week <= end:
        weeks.append(week)
        week += timedelta(days=7)

    quarter = Decimal("0.25")
    regular_capacity = Decimal(str(_STAFFING_POLICY["regular_hours"]))
    invoice_lag_days = int(_STAFFING_POLICY["invoice_days_after_latest_week"])
    authorization_lead_days = int(_STAFFING_POLICY["authorization_lead_days"])
    customer_approval_days = int(_STAFFING_POLICY["customer_approval_days"])
    usage: dict[tuple[str, date], Decimal] = {}
    worker_customers: dict[str, set[str]] = {
        worker["employee_id"]: set() for worker in workers
    }
    entry_sequence = 0
    internal_approver = next(
        (
            row["full_name"]
            for row in employees
            if row.get("employee_class") != "assigned_worker"
            and row.get("role") in {"branch_manager", "controller", "payroll_manager"}
        ),
        next(
            (
                row["full_name"]
                for row in employees
                if row.get("employee_class") != "assigned_worker"
            ),
            DEFAULT_STAFFING_APPROVER,
        ),
    )

    def active(worker: dict[str, Any], service_week: date) -> bool:
        return date.fromisoformat(worker["hire_date"]) <= service_week and (
            not worker.get("termination_date")
            or service_week < date.fromisoformat(worker["termination_date"])
        )

    def pay_rate(worker: dict[str, Any], service_week: date) -> Decimal:
        salary = Decimal(worker["annual_salary"])
        raise_date = worker.get("raise_effective_date")
        raise_pct = Decimal(str(worker.get("raise_pct") or "0"))
        if raise_date and service_week < date.fromisoformat(raise_date) and raise_pct:
            salary /= Decimal("1") + raise_pct
        annual_regular_hours = regular_capacity * Decimal("52")
        return (salary / annual_regular_hours).quantize(CENT)

    for line in lines:
        invoice = invoices[line["customer_invoice_id"]]
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        remaining = Decimal(line["quantity"])
        allocated_weeks: list[date] = []
        # Prefer recent service. Within the recent window, use the least-loaded week
        # first; if a holiday or billing spike exhausts it, extend farther back while
        # retaining the no-post-invoice-service invariant.
        candidates = [value for value in weeks if value <= invoice_date]
        candidates.sort(
            key=lambda value: (
                max(0, (invoice_date - value).days // invoice_lag_days),
                sum(
                    (
                        usage.get((worker["employee_id"], value), Decimal("0"))
                        for worker in workers
                    ),
                    Decimal("0"),
                )
                / max(1, sum(1 for worker in workers if active(worker, value))),
                -value.toordinal(),
            )
        )
        bulk_candidates = candidates

        def eligible(worker: dict[str, Any], service_week: date) -> bool:
            return active(worker, service_week)

        def worker_order(worker: dict[str, Any], service_week: date) -> tuple[Any, ...]:
            return (
                len(worker_customers[worker["employee_id"]]),
                usage.get((worker["employee_id"], service_week), Decimal("0")),
                worker["employee_id"],
            )

        def record_hours(
            worker: dict[str, Any], service_week: date, capacity: Decimal
        ) -> bool:
            nonlocal entry_sequence, remaining
            key = (worker["employee_id"], service_week)
            prior = usage.get(key, Decimal("0"))
            room = capacity - prior
            if room <= 0:
                return False
            hours = min(room, remaining)
            # Invoice quantities and all capacity inputs are quarter-hour
            # values, so this operation remains exact.
            hours = (hours / quarter).to_integral_value() * quarter
            if hours <= 0:
                return False
            regular = min(hours, max(regular_capacity - prior, Decimal("0")))
            overtime = hours - regular
            usage[key] = prior + hours
            worker_customers[worker["employee_id"]].add(invoice["customer_id"])
            entry_sequence += 1
            internal_approved_date = service_week
            approved_date = min(
                invoice_date,
                internal_approved_date + timedelta(days=customer_approval_days),
            )
            while not _is_business_day(approved_date):
                approved_date -= timedelta(days=1)
            approved_date = max(approved_date, internal_approved_date)
            customer_approver = STAFFING_CUSTOMER_APPROVER_NAMES[
                entry_sequence % len(STAFFING_CUSTOMER_APPROVER_NAMES)
            ]
            assignment_reference = (
                f"ASN-{invoice['customer_id']}-{worker['employee_id']}"
            )
            assignment_authorized_date = max(
                date.fromisoformat(str(worker["hire_date"])),
                service_week - timedelta(days=authorization_lead_days),
            )
            records["staffing_time_entries"].append(
                {
                    "staffing_time_entry_id": f"STE-{entry_sequence:05d}",
                    "timecard_id": (
                        f"TC-{service_week.strftime('%Y%m%d')}-{worker['employee_id']}"
                    ),
                    "assignment_reference": assignment_reference,
                    "assignment_authorization_reference": (
                        f"AA-{assignment_reference.removeprefix('ASN-')}"
                    ),
                    "assignment_authorized_date": (
                        assignment_authorized_date.isoformat()
                    ),
                    "rate_authorization_reference": (
                        f"RA-{assignment_reference.removeprefix('ASN-')}"
                    ),
                    "rate_authorized_date": assignment_authorized_date.isoformat(),
                    "rate_authorized_by": internal_approver,
                    "employee_id": worker["employee_id"],
                    "customer_id": invoice["customer_id"],
                    "customer_invoice_id": line["customer_invoice_id"],
                    "customer_invoice_line_id": line["customer_invoice_line_id"],
                    "week_ending": service_week.isoformat(),
                    "earning_type": "client assignment",
                    "regular_hours": str(regular),
                    "overtime_hours": str(overtime),
                    "billable_hours": str(hours),
                    "pay_rate": str(pay_rate(worker, service_week)),
                    "bill_rate": line["unit_price"],
                    "internal_approval_status": "approved for payroll",
                    "internal_approved_by": internal_approver,
                    "internal_approval_reference": f"PAYAPR-{entry_sequence:05d}",
                    "internal_approved_date": internal_approved_date.isoformat(),
                    "approval_status": "approved",
                    "approved_by": customer_approver,
                    "approval_reference": f"APR-{entry_sequence:05d}",
                    "approved_date": approved_date.isoformat(),
                }
            )
            remaining -= hours
            allocated_weeks.append(service_week)
            return True

        def allocate_from(
            available_workers: list[dict[str, Any]], capacity: Decimal
        ) -> None:
            for service_week in bulk_candidates:
                for worker in sorted(
                    (row for row in available_workers if eligible(row, service_week)),
                    key=lambda row: worker_order(row, service_week),
                ):
                    record_hours(worker, service_week, capacity)
                    if remaining == 0:
                        return

        def related_workers() -> list[dict[str, Any]]:
            return [
                worker
                for worker in workers
                if invoice["customer_id"] in worker_customers[worker["employee_id"]]
            ]

        # Exhaust existing assignments before opening a new worker relationship.
        allocate_from(related_workers(), regular_capacity)
        rejected: set[str] = set()
        while remaining:
            new_workers = [
                worker
                for worker in workers
                if worker["employee_id"] not in rejected
                and invoice["customer_id"]
                not in worker_customers[worker["employee_id"]]
                and any(
                    eligible(worker, service_week)
                    and regular_capacity
                    - usage.get((worker["employee_id"], service_week), Decimal("0"))
                    > 0
                    for service_week in bulk_candidates
                )
            ]
            if not new_workers:
                break
            reference_week = bulk_candidates[0]
            new_workers.sort(key=lambda row: worker_order(row, reference_week))
            worker = new_workers[0]
            before = remaining
            allocate_from([worker], regular_capacity)
            if remaining == before:
                rejected.add(worker["employee_id"])
        if remaining:
            # The public package needs a complete approved-time population, not a second
            # workforce-capacity model. Residual hours are shown as overtime, opening an
            # assignment if this is a new customer.
            overflow_workers = related_workers() or workers
            overflow_capacity = remaining + max(
                (
                    usage.get((worker["employee_id"], service_week), Decimal("0"))
                    for worker in overflow_workers
                    for service_week in bulk_candidates
                    if eligible(worker, service_week)
                ),
                default=regular_capacity,
            )
            allocate_from(overflow_workers, overflow_capacity)
        if remaining:
            active_count = sum(1 for worker in workers if active(worker, invoice_date))
            raise ValueError(
                f"staffing capacity exhausted for {line['customer_invoice_line_id']}: "
                f"{remaining} of {line['quantity']} hours at "
                f"{line['unit_price']}/hr on {invoice['invoice_date']}; "
                f"active workers={active_count}"
            )
        first_week = min(allocated_weeks)
        last_week = max(allocated_weeks)
        line["goods_services"] = (
            "contract staffing services - approved assignment hours, "
            f"service period {first_week.strftime('%m/%d/%Y')} to "
            f"{last_week.strftime('%m/%d/%Y')}"
        )
        for fulfillment in records.get("fulfillment_events") or []:
            if (
                fulfillment.get("customer_invoice_line_id")
                == line["customer_invoice_line_id"]
            ):
                fulfillment["fulfillment_date"] = last_week.isoformat()
                fulfillment["support_reference"] = (
                    f"Approved time {first_week:%m/%d/%Y}-{last_week:%m/%d/%Y}"
                )

    billed = sum(
        (Decimal(row["quantity"]) for row in lines),
        Decimal("0"),
    )
    timed = sum(
        (Decimal(row["billable_hours"]) for row in records["staffing_time_entries"]),
        Decimal("0"),
    )
    if billed != timed:
        raise ValueError(f"staffing time {timed} does not tie to billed hours {billed}")
    assignment_periods: dict[str, tuple[str, str]] = {}
    for row in records["staffing_time_entries"]:
        assignment = row["assignment_reference"]
        week_ending = row["week_ending"]
        current = assignment_periods.get(assignment)
        assignment_periods[assignment] = (
            min(current[0], week_ending) if current else week_ending,
            max(current[1], week_ending) if current else week_ending,
        )
    for row in records["staffing_time_entries"]:
        start_date, end_date = assignment_periods[row["assignment_reference"]]
        row["assignment_start_date"] = start_date
        row["assignment_end_date"] = end_date
        worker = next(
            employee
            for employee in workers
            if employee["employee_id"] == row["employee_id"]
        )
        authorized_date = max(
            date.fromisoformat(str(worker["hire_date"])),
            date.fromisoformat(start_date) - timedelta(days=authorization_lead_days),
        ).isoformat()
        row["assignment_authorized_date"] = authorized_date
        row["rate_authorized_date"] = authorized_date

    # Record the service window and normal billing deadline used by the
    # retained staffing cutoff report.
    entries_by_invoice: dict[str, list[dict[str, Any]]] = {}
    for row in records["staffing_time_entries"]:
        entries_by_invoice.setdefault(row["customer_invoice_id"], []).append(row)
    for invoice_id, invoice_entries in entries_by_invoice.items():
        invoice = invoices[invoice_id]
        service_dates = [
            date.fromisoformat(row["week_ending"]) for row in invoice_entries
        ]
        service_start = min(service_dates)
        service_end = max(service_dates)
        deadline = service_end + timedelta(days=invoice_lag_days)
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        invoice["service_period_start"] = service_start.isoformat()
        invoice["service_period_end"] = service_end.isoformat()
        invoice["billing_cadence"] = (
            f"No later than {invoice_lag_days} days after latest approved weekly time in batch"
        )
        invoice["billing_deadline"] = deadline.isoformat()
        invoice["billing_batch_timestamp"] = (
            f"{invoice_date.isoformat()}T06:15:00-06:00 (automated billing queue)"
        )
