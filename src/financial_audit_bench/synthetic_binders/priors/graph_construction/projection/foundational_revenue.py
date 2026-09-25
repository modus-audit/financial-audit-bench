"""Manufacturing revenue detail, reconciliation, and policy workbooks."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.foundational_support import (
    _add_schedule_sheet,
    account_names,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.projection.materialize import (
    save_workbook,
    open_pbc_sheet,
    write_header,
)

World = dict[str, Any]


def _manufacturing_revenue_sources(world: World) -> dict[str, Any]:
    """Build the shared source indexes consumed by REV-01 through REV-03."""
    invoices = {
        row["customer_invoice_id"]: row for row in world.get("customer_invoice") or []
    }
    customers = {row["customer_id"]: row for row in world.get("customer") or []}
    contracts = {
        row["customer_contract_id"]: row for row in world.get("customer_contract") or []
    }
    fulfillments = {
        row["customer_invoice_line_id"]: row
        for row in world.get("fulfillment_event") or []
    }
    applications: dict[str, list[dict[str, Any]]] = {}
    for row in world.get("ar_receipt_application") or []:
        applications.setdefault(row["customer_invoice_id"], []).append(row)
    credits: dict[str, list[dict[str, Any]]] = {}
    for row in world.get("customer_credit_adjustment") or []:
        credits.setdefault(row["related_invoice_id"], []).append(row)
    receipts = {
        row["customer_cash_receipt_id"]: row
        for row in world.get("customer_cash_receipt") or []
    }
    accounts = account_names(world)
    return {
        "invoices": invoices,
        "customers": customers,
        "contracts": contracts,
        "fulfillments": fulfillments,
        "applications": applications,
        "credits": credits,
        "receipts": receipts,
        "accounts": accounts,
    }


def _credit_tax_amount(
    invoice: dict[str, Any], lines: list[dict[str, Any]], credit: dict[str, Any]
) -> Decimal:
    invoice_amount = Decimal(str(invoice["original_amount"]))
    invoice_tax = sum(
        (Decimal(str(row.get("tax_amount") or "0")) for row in lines),
        Decimal("0.00"),
    )
    return (
        (Decimal(str(credit["amount"])) * invoice_tax / invoice_amount).quantize(
            Decimal("0.01")
        )
        if invoice_amount and invoice_tax
        else Decimal("0.00")
    )


def render_manufacturing_revenue_detail(
    path: Path, world: World, title: str
) -> list[dict[str, Any]]:
    """Render the complete manufacturing invoice-to-cash source population."""
    source = _manufacturing_revenue_sources(world)
    start = str(world["fiscal_calendar"]["start_date"])
    end = str(world["fiscal_calendar"]["end_date"])
    lines = [
        row
        for row in world.get("customer_invoice_line") or []
        if start
        <= source["invoices"][row["customer_invoice_id"]]["posting_date"]
        <= end
    ]
    wb, ws = open_pbc_sheet(title, "REV-01", world)
    ws.title = "Annual Revenue Detail"
    headers = [
        "Source Line ID",
        "Invoice",
        "Invoice Date",
        "Posting Date",
        "Customer",
        "Contract",
        "Customer PO",
        "Sales Order",
        "Approved Quote",
        "Goods / Services",
        "Quantity",
        "UOM",
        "Unit Price",
        "Gross",
        "Discount",
        "Freight",
        "Rebate",
        "Tax Status",
        "Tax Rate",
        "Exemption Certificate",
        "Sales Tax",
        "Invoice Total",
        "Revenue GL",
        "AR GL",
        "Currency",
        "Shipment Date",
        "BOL / Shipment Support",
        "POD / Acceptance Support",
        "Acceptance Date",
        "Evidence Status",
        "Credit Memos",
        "Receipt / Application IDs",
        "Bank References",
        "Cash Applied (All Dates)",
        "Remaining after Applications",
        "Revenue Journal",
        "FS Presentation",
    ]
    write_header(ws, headers)
    cell_map: list[dict[str, Any]] = []
    invoice_line_totals: dict[str, Decimal] = {}
    for line in sorted(lines, key=lambda row: row["customer_invoice_line_id"]):
        invoice = source["invoices"][line["customer_invoice_id"]]
        customer = source["customers"][invoice["customer_id"]]
        contract = source["contracts"].get(invoice["customer_contract_id"], {})
        fulfillment = source["fulfillments"].get(line["customer_invoice_line_id"], {})
        evidence_fields = (
            fulfillment.get("delivery_document_reference"),
            fulfillment.get("acceptance_reference"),
            fulfillment.get("acceptance_date"),
        )
        evidence_status = "Complete" if all(evidence_fields) else "Open"
        invoice_total = Decimal(str(line["net_amount"]))
        invoice_line_totals[invoice["customer_invoice_id"]] = (
            invoice_line_totals.get(invoice["customer_invoice_id"], Decimal("0.00"))
            + invoice_total
        )
        credit_refs = ", ".join(
            row["customer_credit_adjustment_id"]
            for row in source["credits"].get(invoice["customer_invoice_id"], [])
        )
        cash_applied = sum(
            (
                Decimal(str(row["applied_amount"]))
                for row in source["applications"].get(
                    invoice["customer_invoice_id"], []
                )
            ),
            Decimal("0.00"),
        )
        applications = source["applications"].get(invoice["customer_invoice_id"], [])
        receipt_ids = ", ".join(
            f"{row['customer_cash_receipt_id']} / {row['ar_receipt_application_id']}"
            for row in applications
        )
        bank_refs = ", ".join(
            str(
                source["receipts"]
                .get(row["customer_cash_receipt_id"], {})
                .get("bank_reference")
                or ""
            )
            for row in applications
        )
        credit_total = sum(
            (
                Decimal(str(row["amount"]))
                for row in source["credits"].get(invoice["customer_invoice_id"], [])
            ),
            Decimal("0.00"),
        )
        account = source["accounts"].get(invoice["revenue_gl_account_id"], {})
        ws.append(
            [
                line["customer_invoice_line_id"],
                invoice["invoice_number"],
                invoice["invoice_date"],
                invoice["posting_date"],
                customer["customer_name"],
                contract.get("customer_contract_id", invoice["customer_contract_id"]),
                line.get("customer_purchase_order_reference"),
                line.get("sales_order_reference"),
                line.get("quote_reference"),
                line["goods_services"],
                line["quantity"],
                line["uom"],
                line["unit_price"],
                line["gross_amount"],
                line["discount_amount"],
                line["freight_amount"],
                line.get("rebate_amount") or "0.00",
                line.get("sales_tax_status"),
                line.get("sales_tax_rate"),
                line.get("tax_exemption_certificate"),
                line.get("tax_amount") or "0.00",
                line["net_amount"],
                invoice["revenue_gl_account_id"],
                invoice["ar_gl_account_id"],
                invoice["currency_code"],
                fulfillment.get("fulfillment_date"),
                fulfillment.get("delivery_document_reference"),
                fulfillment.get("acceptance_reference"),
                fulfillment.get("acceptance_date"),
                evidence_status,
                credit_refs,
                receipt_ids,
                bank_refs,
                cash_applied,
                invoice_total - credit_total - cash_applied,
                f"JOURNAL-REV-{invoice['customer_invoice_id']}",
                account.get("name") or account.get("fsli_mapping") or "Revenue",
            ]
        )
        if evidence_status == "Open":
            raise ValueError(
                f"REV-01 line {line['customer_invoice_line_id']} lacks performance evidence"
            )
    for invoice_id, line_total in invoice_line_totals.items():
        invoice_total = Decimal(str(source["invoices"][invoice_id]["original_amount"]))
        if line_total != invoice_total:
            raise ValueError(
                f"REV-01 invoice {invoice_id} lines {line_total} do not tie to {invoice_total}"
            )
    ws.append(
        [
            "Total",
            len(lines),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            sum((Decimal(str(row["gross_amount"])) for row in lines), Decimal("0.00")),
            sum(
                (Decimal(str(row["discount_amount"])) for row in lines), Decimal("0.00")
            ),
            sum(
                (Decimal(str(row["freight_amount"])) for row in lines), Decimal("0.00")
            ),
            sum(
                (Decimal(str(row.get("rebate_amount") or "0")) for row in lines),
                Decimal("0.00"),
            ),
            "",
            "",
            "",
            sum(
                (Decimal(str(row.get("tax_amount") or "0")) for row in lines),
                Decimal("0.00"),
            ),
            sum(invoice_line_totals.values()),
        ]
    )

    credits_ws = _add_schedule_sheet(wb, "Credit Memo Population", world)
    write_header(
        credits_ws,
        [
            "Credit Memo ID",
            "Customer",
            "Related Invoice",
            "Date",
            "Type",
            "Reason",
            "Amount",
            "Approval",
            "Settlement Status",
        ],
    )
    for credit in world.get("customer_credit_adjustment") or []:
        invoice = source["invoices"][credit["related_invoice_id"]]
        credits_ws.append(
            [
                credit["customer_credit_adjustment_id"],
                source["customers"][credit["customer_id"]]["customer_name"],
                invoice["invoice_number"],
                credit["posting_date"],
                credit["adjustment_kind"],
                credit["reason"],
                credit["amount"],
                credit["approval_status"],
                credit["settlement_status"],
            ]
        )
    controls = _add_schedule_sheet(wb, "Population Controls", world)
    write_header(controls, ["Control", "Count", "Amount", "Status"])
    controls.append(
        [
            "Current-year invoice lines",
            len(lines),
            sum(invoice_line_totals.values()),
            "Tied to invoice headers",
        ]
    )
    controls.append(
        [
            "Sales tax liability component",
            len([r for r in lines if Decimal(str(r.get("tax_amount") or 0))]),
            sum(
                (Decimal(str(r.get("tax_amount") or 0)) for r in lines), Decimal("0.00")
            ),
            "Separately identified",
        ]
    )
    controls.append(
        [
            "Credit memo population",
            len(world.get("customer_credit_adjustment") or []),
            sum(
                (
                    Decimal(str(r["amount"]))
                    for r in world.get("customer_credit_adjustment") or []
                ),
                Decimal("0.00"),
            ),
            "Complete",
        ]
    )
    controls.append(["Performance evidence exceptions", 0, "", "Resolved"])
    cell_map.append(
        {
            "field": "Invoice population total",
            "cell": f"V{ws.max_row}",
            "value": str(sum(invoice_line_totals.values())),
        }
    )
    save_workbook(wb, path)
    return cell_map


def render_manufacturing_revenue_reconciliation(
    path: Path, world: World, title: str
) -> list[dict[str, Any]]:
    """Reconcile invoice lines and credits through GL, TB, and presentation."""
    source = _manufacturing_revenue_sources(world)
    start = str(world["fiscal_calendar"]["start_date"])
    end = str(world["fiscal_calendar"]["end_date"])
    invoice_lines: dict[str, list[dict[str, Any]]] = {}
    for line in world.get("customer_invoice_line") or []:
        invoice = source["invoices"][line["customer_invoice_id"]]
        if start <= invoice["posting_date"] <= end:
            invoice_lines.setdefault(invoice["customer_invoice_id"], []).append(line)
    by_account: dict[str, dict[str, Decimal]] = {}
    for invoice_id, lines in invoice_lines.items():
        invoice = source["invoices"][invoice_id]
        bucket = by_account.setdefault(
            invoice["revenue_gl_account_id"],
            {"sales": Decimal("0"), "credits": Decimal("0")},
        )
        bucket["sales"] += sum(
            (
                Decimal(str(row["net_amount"]))
                - Decimal(str(row.get("tax_amount") or 0))
                for row in lines
            ),
            Decimal("0"),
        )
        for credit in source["credits"].get(invoice_id, []):
            bucket["credits"] += Decimal(str(credit["amount"])) - _credit_tax_amount(
                invoice, lines, credit
            )
    journal_lines = world.get("journal_entry_line") or []
    final_tb = {
        row["gl_account_id"]: row
        for row in world.get("final_adjusted_trial_balance_account") or []
    }
    wb, ws = open_pbc_sheet(title, "REV-02", world)
    ws.title = "Revenue to GL"
    write_header(
        ws,
        [
            "Revenue Account",
            "Gross Sales ex Tax",
            "Credits ex Tax",
            "Invoice Register Net",
            "Revenue GL Activity",
            "Register-to-GL Difference",
            "Final TB Balance",
            "GL-to-TB Difference",
            "FS Presentation",
            "Status",
        ],
    )
    cell_map: list[dict[str, Any]] = []
    for account_id, values in sorted(by_account.items()):
        register_net = values["sales"] - values["credits"]
        entry_prefixes = tuple(
            [
                f"JOURNAL-REV-{invoice_id}"
                for invoice_id, invoice in source["invoices"].items()
                if invoice["revenue_gl_account_id"] == account_id
                and invoice_id in invoice_lines
            ]
            + [
                f"JOURNAL-CR-{credit['customer_credit_adjustment_id']}"
                for invoice_id in invoice_lines
                for credit in source["credits"].get(invoice_id, [])
                if source["invoices"][invoice_id]["revenue_gl_account_id"] == account_id
            ]
        )
        gl_signed = sum(
            (
                Decimal(str(row["signed_amount"]))
                for row in journal_lines
                if row["gl_account_id"] == account_id
                and row["journal_entry_id"] in entry_prefixes
            ),
            Decimal("0.00"),
        )
        gl_activity = -gl_signed
        difference = register_net - gl_activity
        if difference != 0:
            raise ValueError(
                f"REV-02 {account_id} register-to-GL difference {difference}"
            )
        account = source["accounts"].get(account_id, {})
        tb = final_tb.get(account_id, {})
        tb_balance = -Decimal(str(tb.get("final_adjusted_closing_balance") or "0"))
        tb_difference = gl_activity - tb_balance
        if tb_difference != 0:
            raise ValueError(f"REV-02 {account_id} GL-to-TB difference {tb_difference}")
        row_number = ws.max_row + 1
        ws.append(
            [
                account_id,
                values["sales"],
                values["credits"],
                f"=B{row_number}-C{row_number}",
                gl_activity,
                f"=D{row_number}-E{row_number}",
                tb_balance,
                f"=E{row_number}-G{row_number}",
                account.get("name") or account.get("fsli_mapping"),
                "Reconciled",
            ]
        )
        cell_map.append(
            {"field": "Differences", "cell": f"F{ws.max_row}", "value": str(difference)}
        )
    tax = _add_schedule_sheet(wb, "Sales Tax Control", world)
    write_header(
        tax,
        [
            "Tax Status",
            "Line Count",
            "Taxable Base",
            "Gross Tax",
            "Credit Tax",
            "Net Tax",
            "Tax GL Activity",
            "Difference",
            "Certificate Exceptions",
        ],
    )
    statuses: dict[str, list[dict[str, Any]]] = {}
    for lines in invoice_lines.values():
        for line in lines:
            statuses.setdefault(str(line.get("sales_tax_status") or "open"), []).append(
                line
            )
    for status, lines in sorted(statuses.items()):
        exceptions = sum(
            1
            for row in lines
            if status == "resale_exempt" and not row.get("tax_exemption_certificate")
        )
        gross_tax = sum(
            (Decimal(str(r.get("tax_amount") or 0)) for r in lines), Decimal("0.00")
        )
        status_invoice_ids = {row["customer_invoice_id"] for row in lines}
        credit_tax = sum(
            (
                _credit_tax_amount(
                    source["invoices"][invoice_id],
                    invoice_lines[invoice_id],
                    credit,
                )
                for invoice_id in status_invoice_ids
                for credit in source["credits"].get(invoice_id, [])
            ),
            Decimal("0.00"),
        )
        tax.append(
            [
                status,
                len(lines),
                sum(
                    (
                        Decimal(str(r["gross_amount"]))
                        + Decimal(str(r["freight_amount"]))
                        - Decimal(str(r["discount_amount"]))
                        for r in lines
                    ),
                    Decimal("0.00"),
                ),
                gross_tax,
                credit_tax,
                gross_tax - credit_tax,
                "",
                "",
                exceptions,
            ]
        )
        if exceptions:
            raise ValueError(
                f"REV-02 has {exceptions} resale-exemption certificate exceptions"
            )
    revenue_entry_ids = {
        f"JOURNAL-REV-{invoice_id}" for invoice_id in invoice_lines
    } | {
        f"JOURNAL-CR-{credit['customer_credit_adjustment_id']}"
        for invoice_id in invoice_lines
        for credit in source["credits"].get(invoice_id, [])
    }
    expected_tax = sum(
        (
            Decimal(str(row.get("tax_amount") or 0))
            for lines in invoice_lines.values()
            for row in lines
        ),
        Decimal("0.00"),
    ) - sum(
        (
            _credit_tax_amount(source["invoices"][invoice_id], lines, credit)
            for invoice_id, lines in invoice_lines.items()
            for credit in source["credits"].get(invoice_id, [])
        ),
        Decimal("0.00"),
    )
    sales_tax_account_ids = {
        str(row["gl_account_id"])
        for row in world.get("general_ledger_account") or []
        if str(row.get("name") or row.get("fsli_mapping") or "").casefold()
        == "sales tax payable"
    }
    if expected_tax and not sales_tax_account_ids:
        raise ValueError("REV-02 cannot identify the sales-tax payable account")
    tax_gl_activity = -sum(
        (
            Decimal(str(row["signed_amount"]))
            for row in journal_lines
            if str(row["gl_account_id"]) in sales_tax_account_ids
            and row["journal_entry_id"] in revenue_entry_ids
        ),
        Decimal("0.00"),
    )
    tax_difference = expected_tax - tax_gl_activity
    tax_row = tax.max_row + 1
    tax.append(
        [
            "Tax liability reconciliation",
            "",
            "",
            "",
            "",
            expected_tax,
            tax_gl_activity,
            f"=F{tax_row}-G{tax_row}",
            "",
        ]
    )
    if tax_difference != 0:
        raise ValueError(f"REV-02 sales-tax register-to-GL difference {tax_difference}")
    save_workbook(wb, path)
    return cell_map


def render_manufacturing_revenue_policy(
    path: Path, world: World, title: str
) -> list[dict[str, Any]]:
    """Render contract-specific recognition policy and evidence coverage."""
    source = _manufacturing_revenue_sources(world)
    wb, ws = open_pbc_sheet(title, "REV-03", world)
    ws.title = "Stream and Policy Mapping"
    write_header(
        ws,
        [
            "Revenue Stream",
            "GL Account",
            "Customer Type",
            "Goods / Services",
            "Payment Terms",
            "Delivery Terms",
            "Acceptance Terms",
            "Transfer of Control",
            "Performance Evidence Required",
            "Presentation",
            "Approval",
        ],
    )
    seen: set[tuple[str, str]] = set()
    for contract in world.get("customer_contract") or []:
        customer = source["customers"][contract["customer_id"]]
        invoice = next(
            (
                row
                for row in source["invoices"].values()
                if row["customer_contract_id"] == contract["customer_contract_id"]
            ),
            None,
        )
        if invoice is None:
            continue
        key = (invoice["revenue_gl_account_id"], contract["goods_services"])
        if key in seen:
            continue
        seen.add(key)
        ws.append(
            [
                "Manufactured product sales",
                invoice["revenue_gl_account_id"],
                customer["customer_type"],
                contract["goods_services"],
                contract["payment_terms"],
                contract["delivery_terms"],
                contract["acceptance_terms"],
                contract["transfer_of_control_point"],
                contract["required_evidence"],
                contract["presentation"],
                "Controller approved",
            ]
        )
    coverage = _add_schedule_sheet(wb, "Performance Evidence Coverage", world)
    write_header(
        coverage,
        [
            "Invoice",
            "Line",
            "Customer PO",
            "Sales Order",
            "Quote",
            "Shipment Date",
            "BOL / Shipment",
            "POD / Acceptance",
            "Acceptance Date",
            "Recognition Date",
            "Status",
        ],
    )
    open_count = 0
    start = str(world["fiscal_calendar"]["start_date"])
    end = str(world["fiscal_calendar"]["end_date"])
    current_lines = []
    for line in world.get("customer_invoice_line") or []:
        invoice = source["invoices"][line["customer_invoice_id"]]
        if not start <= invoice["posting_date"] <= end:
            continue
        current_lines.append(line)
        fulfillment = source["fulfillments"].get(line["customer_invoice_line_id"], {})
        required = [
            line.get("customer_purchase_order_reference"),
            line.get("sales_order_reference"),
            line.get("quote_reference"),
            fulfillment.get("delivery_document_reference"),
            fulfillment.get("acceptance_reference"),
            fulfillment.get("acceptance_date"),
        ]
        status = "Complete" if all(required) else "Open"
        open_count += status == "Open"
        coverage.append(
            [
                invoice["invoice_number"],
                line["customer_invoice_line_id"],
                *required[:3],
                fulfillment.get("fulfillment_date"),
                *required[3:],
                invoice["posting_date"],
                status,
            ]
        )
    coverage.append(
        [
            "Total",
            len(current_lines),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            f"Open: {open_count}",
        ]
    )
    if open_count:
        raise ValueError(f"REV-03 has {open_count} open performance-evidence lines")
    # Keep the machine tie on the primary sheet. Workbook-noise preservation reads
    # primary-sheet ties, while the full evidence matrix remains on its dedicated tab
    # for auditor review.
    ws.append([])
    ws.append(["Performance evidence exceptions", open_count])
    exception_cell = f"B{ws.max_row}"
    save_workbook(wb, path)
    return [
        {
            "field": "Performance evidence exceptions",
            "cell": exception_cell,
            "value": open_count,
        }
    ]
