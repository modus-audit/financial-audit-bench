"""GL posting for the inventory subledger."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from fractions import Fraction
from typing import Any

from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)
from financial_audit_bench.synthetic_binders.priors.graph.domains.inventory.common import (
    INVENTORY_GL as INVENTORY_GL,
    _add_policy_business_days,
    _next_policy_business_day,
    _next_policy_run,
    _procurement_order_lead,
    _purchases,
    _rni_movement,
    _unit_price_string,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)

INVENTORY_RULES_POLICY_RECORD = {
    "policy": load_authored_policy("authored.inventory.rules.v1").values["policy"]
}


@REGISTRY.rule(
    "build_inventory_procurement_chain",
    inputs=(
        "company_context",
        "fiscal_calendar",
        "vendor",
        "inventory_item",
        "inventory_movement",
    ),
    outputs=("purchase_order", "goods_receipt", "vendor_invoice"),
    contribution_priority=75,
    gate="has_inventory",
)
def build_inventory_procurement_chain(
    company: dict[str, Any],
    calendar: dict[str, Any],
    vendors: list[dict[str, Any]],
    items: list[dict[str, Any]],
    movements: list[dict[str, Any]],
    *,
    _required_surfaces: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    required_surfaces = (
        frozenset(
            {
                "procurement_documents",
                "procurement_receipts",
                "procurement_invoices",
            }
        )
        if _required_surfaces is None
        else _required_surfaces
    )
    allowed_surfaces = frozenset(
        {"procurement_documents", "procurement_receipts", "procurement_invoices"}
    )
    if (
        type(required_surfaces) is not frozenset
        or any(type(value) is not str for value in required_surfaces)
        or not required_surfaces
        or not required_surfaces <= allowed_surfaces
    ):
        raise ValueError("invalid inventory procurement surface request")
    need_documents = "procurement_documents" in required_surfaces
    need_receipts = "procurement_receipts" in required_surfaces
    need_invoices = "procurement_invoices" in required_surfaces
    snapshot = INVENTORY_RULES_POLICY_RECORD
    company = company if need_documents or need_invoices else {}
    calendar = calendar if need_receipts or need_invoices else {}
    purchases = sorted(
        _purchases(movements),
        key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
    )
    if not purchases:
        return [], [], []
    if not vendors:
        raise ValueError("inventory procurement requires at least one supplier")
    items_by_id = {row["inventory_item_id"]: row for row in items}
    vendors_by_name: dict[str, dict[str, Any]] = {}
    for vendor in vendors:
        name = str(vendor.get("name") or "")
        if not name:
            raise ValueError("inventory procurement vendor lacks a name")
        if name in vendors_by_name:
            raise ValueError(f"inventory procurement vendor name is ambiguous: {name}")
        vendors_by_name[name] = vendor
    policy = snapshot["policy"]["procurement"]
    term_cycle = tuple(int(value) for value in policy["term_days_cycle"])
    year_end_iso = str(calendar["end_date"]) if need_receipts or need_invoices else ""
    purchase_orders: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    invoices: list[dict[str, Any]] = []
    for index, movement in enumerate(purchases, 1):
        item = items_by_id.get(movement["inventory_item_id"])
        if item is None:
            raise ValueError(
                f"inventory procurement movement references unknown item: "
                f"{movement['inventory_movement_id']}"
            )
        named_supplier = str(movement.get("vendor_reference") or "")
        vendor = vendors_by_name.get(named_supplier)
        if vendor is None:
            raise ValueError(
                "admitted inventory receipt supplier does not resolve exactly "
                "to the admitted vendor master: "
                f"{movement['inventory_movement_id']} / {named_supplier!r}"
            )
        receipt_date = date.fromisoformat(
            str(movement.get("receipt_date") or movement["posting_date"])
        )
        source_invoice_date = ""
        invoice_document_date: date | None = None
        if need_receipts or need_invoices:
            source_invoice_date = str(movement.get("invoice_date") or "")
            if not source_invoice_date:
                raise ValueError(
                    f"inventory receipt lacks its admitted invoice date: "
                    f"{movement['inventory_movement_id']}"
                )
            invoice_document_date = date.fromisoformat(source_invoice_date)
        ordinal = index - 1
        rni = (
            _rni_movement(movement, year_end_iso)
            if need_receipts or need_invoices
            else False
        )
        po_id = f"PO-STK-{index:04d}"
        receipt_id = f"GR-STK-{index:04d}"
        invoice_id = f"VENDOR-INVOICE-STK-{index:04d}"
        amount = str(movement["extended_cost"])
        quantity = str(movement["quantity"])
        if Fraction(quantity) <= 0 or Fraction(amount) <= 0:
            raise ValueError(
                f"inventory procurement economics must be positive: "
                f"{movement['inventory_movement_id']}"
            )
        description = (
            f"{item['description']} - {quantity} {item['uom']} received"
            if need_documents or need_invoices
            else ""
        )
        if need_documents:
            order_date = _add_policy_business_days(
                receipt_date, -_procurement_order_lead(snapshot["policy"], ordinal)
            )
            purchase_orders.append(
                {
                    "purchase_order_id": po_id,
                    "vendor_id": vendor["vendor_id"],
                    "order_date": order_date.isoformat(),
                    "description": description,
                    "quantity": str(quantity),
                    "unit_price": _unit_price_string(amount, quantity),
                    "total_amount": amount,
                    "currency_code": company["currency_code"],
                    "inventory_movement_id": movement["inventory_movement_id"],
                    "inventory_item_id": movement["inventory_item_id"],
                    "approval_status": "approved",
                    "approved_by": "Purchasing manager",
                    "expected_receipt_date": receipt_date.isoformat(),
                    "status": "received",
                }
            )
        if need_receipts:
            receipts.append(
                {
                    "goods_receipt_id": receipt_id,
                    "purchase_order_id": po_id,
                    "vendor_id": vendor["vendor_id"],
                    "inventory_movement_id": movement["inventory_movement_id"],
                    "inventory_item_id": movement["inventory_item_id"],
                    "receipt_date": receipt_date.isoformat(),
                    "quantity_received": str(quantity),
                    "condition": "accepted",
                    "receiver_name": "Receiving lead",
                    "location": "Main facility receiving",
                    "invoice_received": "no" if rni else "yes",
                    "matching_status": (
                        "invoice pending - accrued at period end"
                        if rni
                        else "three-way match complete"
                    ),
                }
            )
        if rni or not need_invoices:
            # No in-year AP voucher exists: the year-end accrual carries the liability
            # and the January invoice is subsequent-period evidence only.
            continue
        if invoice_document_date is None:
            raise RuntimeError("inventory invoice date was not resolved")
        invoice_posting_date = max(receipt_date, invoice_document_date)
        term_days = term_cycle[ordinal % len(term_cycle)]
        invoices.append(
            {
                "vendor_invoice_id": invoice_id,
                "vendor_id": vendor["vendor_id"],
                "invoice_reference": f"INVREF-STK-{index:04d}",
                "vendor_invoice_number": "",
                "document_date": invoice_document_date.isoformat(),
                "posting_date": invoice_posting_date.isoformat(),
                "due_date": _next_policy_business_day(
                    invoice_posting_date + timedelta(days=term_days)
                ).isoformat(),
                "payment_terms": f"net {term_days}",
                "service_period_start": receipt_date.isoformat(),
                "service_period_end": receipt_date.isoformat(),
                "amount": amount,
                "currency_code": company["currency_code"],
                "inventory_movement_id": movement["inventory_movement_id"],
                "inventory_item_id": movement["inventory_item_id"],
                "purchase_order_id": po_id,
                "goods_receipt_id": receipt_id,
                "expense_gl_account_id": INVENTORY_GL,
                "payable_gl_account_id": "GL-AP-001",
                "journal_id": f"JOURNAL-{invoice_id}",
                "voucher_id": f"V-{invoice_id}",
                "description": description,
            }
        )
    return (
        purchase_orders if "procurement_documents" in required_surfaces else [],
        receipts if "procurement_receipts" in required_surfaces else [],
        invoices if "procurement_invoices" in required_surfaces else [],
    )


def _construct_inventory_rni_rows(
    snapshot: Mapping[str, Any],
    calendar: Mapping[str, Any],
    vendors: tuple[Mapping[str, Any], ...],
    items: tuple[Mapping[str, Any], ...],
    movements: tuple[Mapping[str, Any], ...],
    receipts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Construct both exact RNI surfaces from already-admitted snapshots."""
    year_end = str(calendar["end_date"])
    rni_policy = snapshot["policy"]["rni_accrual"]
    selection = rni_policy["selection"]
    items_by_id = {row["inventory_item_id"]: row for row in items}
    rni_movements = sorted(
        (
            row
            for row in _purchases(movements)
            if row["movement_kind"] == selection["movement_kind"]
            and items_by_id.get(row["inventory_item_id"], {}).get("category")
            == selection["item_category"]
            and bool(str(row.get("receipt_date") or ""))
            and str(row.get("receipt_date") or "") <= year_end
            and _rni_movement(row, year_end)
        ),
        key=lambda row: (row["posting_date"], row["inventory_movement_id"]),
    )
    if not rni_movements:
        return [], []
    pending_receipts = {
        str(row.get("inventory_movement_id") or ""): row
        for row in receipts
        if row.get("invoice_received") == "no"
    }
    movement_ids = {str(row["inventory_movement_id"]) for row in rni_movements}
    if "" in pending_receipts or set(pending_receipts) != movement_ids:
        raise ValueError(
            "RNI movements do not identity-match the unvouched receiving records"
        )
    vendor_names = {row["vendor_id"]: row["name"] for row in vendors}
    rni_items: list[dict[str, Any]] = []
    accruals: list[dict[str, Any]] = []
    for n, movement in enumerate(rni_movements, 1):
        receipt = pending_receipts[str(movement["inventory_movement_id"])]
        if receipt.get("inventory_item_id") != movement.get("inventory_item_id"):
            raise ValueError("RNI receiving record does not tie to its inventory item")
        if str(receipt["quantity_received"]) != str(movement["quantity"]):
            raise ValueError("RNI receiving record does not tie to its movement")
        amount = str(movement["extended_cost"])
        vendor_id = receipt["vendor_id"]
        accrual_id = str(rni_policy["accrual_id_template"]).format(one_based_ordinal=n)
        rni_id = str(rni_policy["rni_id_template"]).format(one_based_ordinal=n)
        accruals.append(
            {
                "accrued_expense_id": accrual_id,
                "vendor_id": vendor_id,
                "category": "raw materials received not invoiced",
                "service_period_start": movement["receipt_date"],
                "service_period_end": movement["receipt_date"],
                "estimate_method": "receiving_report_cost",
                "source_data_reference": (
                    f"{vendor_names.get(vendor_id, vendor_id)} receiving log "
                    f"through {year_end}"
                ),
                "assumptions": (
                    "goods received before year end; vendor invoice arrives "
                    "after close at the purchase-order price"
                ),
                "opening_balance": "0.00",
                "addition_amount": amount,
                "settlement_amount": "0.00",
                "reversal_amount": "0.00",
                "ending_balance": amount,
                "expense_gl_account_id": INVENTORY_GL,
                "accrual_gl_account_id": "GL-ACCRUED-001",
                "posting_date": year_end,
                "subsequent_invoice_date": movement["invoice_date"],
                "journal_entry_id": f"JOURNAL-{accrual_id}",
                "book_layer": "client_book",
            }
        )
        rni_items.append(
            {
                "rni_accrual_item_id": rni_id,
                "inventory_movement_id": movement["inventory_movement_id"],
                "inventory_item_id": movement["inventory_item_id"],
                "goods_receipt_id": receipt["goods_receipt_id"],
                "purchase_order_id": receipt["purchase_order_id"],
                "vendor_id": vendor_id,
                "amount": amount,
                "receipt_date": movement["receipt_date"],
                "subsequent_invoice_date": movement["invoice_date"],
                "accrued_expense_id": accrual_id,
            }
        )
    return rni_items, accruals


@REGISTRY.finalize("rni_accrual_item")
def attach_inventory_movement_source_ids(
    world: dict[str, Any],
) -> None:
    """Replace admitted base movements with detached, procurement-linked rows."""
    snapshot = INVENTORY_RULES_POLICY_RECORD
    # Replace the node population rather than mutating the builder's rows.
    if type(world) is not dict:
        raise TypeError("inventory finalizer world must be a built-in dictionary")

    def world_value(node_id: str, empty: Any) -> Any:
        value = dict.get(world, node_id)
        return empty if value is None else value

    company = world_value("company_context", {})
    calendar = world_value("fiscal_calendar", {})
    vendors = world_value("vendor", [])
    items = world_value("inventory_item", [])
    base_movements = world_value("inventory_movement", [])
    purchase_orders, receipts, invoices = build_inventory_procurement_chain(
        company,
        calendar,
        vendors,
        items,
        base_movements,
    )
    rni_items, _accruals = _construct_inventory_rni_rows(
        snapshot,
        calendar,
        vendors,
        items,
        base_movements,
        receipts,
    )
    movements = [dict(row) for row in base_movements]

    def one_per_movement(
        rows: list[dict[str, Any]], label: str
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            movement_id = str(row.get("inventory_movement_id") or "")
            if not movement_id:
                raise ValueError(f"{label} lacks inventory movement identity")
            if movement_id in result:
                raise ValueError(f"{label} duplicates movement {movement_id}")
            result[movement_id] = row
        return result

    orders = one_per_movement(
        purchase_orders,
        "stock purchase order",
    )
    receipts = one_per_movement(
        receipts,
        "stock goods receipt",
    )
    invoices = one_per_movement(
        invoices,
        "stock vendor invoice",
    )
    rni_by_movement = one_per_movement(rni_items, "inventory RNI accrual")
    vendor_names = {str(row["vendor_id"]): str(row["name"]) for row in vendors}
    purchase_ids = {
        str(row["inventory_movement_id"])
        for row in movements
        if row.get("movement_kind") == "purchase_receipt"
    }
    if purchase_ids != set(orders) or purchase_ids != set(receipts):
        raise ValueError("purchase movements do not identity-match PO/GRN populations")
    if purchase_ids != set(invoices) | set(rni_by_movement) or set(invoices) & set(
        rni_by_movement
    ):
        raise ValueError(
            "purchase movements must resolve exactly one invoice/RNI state"
        )
    for movement in movements:
        movement_id = str(movement["inventory_movement_id"])
        if movement_id not in purchase_ids:
            continue
        order = orders[movement_id]
        receipt = receipts[movement_id]
        invoice = invoices.get(movement_id)
        rni = rni_by_movement.get(movement_id)
        write_fields = (
            "vendor_id",
            "purchase_order_id",
            "goods_receipt_id",
            "vendor_invoice_id",
            "rni_accrual_item_id",
        )
        if any(movement.get(field) is not None for field in write_fields):
            raise ValueError(
                f"pre-lineage purchase movement already contains bridge values: {movement_id}"
            )
        if (
            order.get("inventory_item_id") != movement.get("inventory_item_id")
            or receipt.get("inventory_item_id") != movement.get("inventory_item_id")
            or order.get("vendor_id") != receipt.get("vendor_id")
            # The admitted movement is authoritative for its supplier. No independent
            # cycling or description-based remapping may create a different item-vendor
            # relationship here.
            or movement.get("vendor_reference")
            != vendor_names.get(str(order.get("vendor_id") or ""))
            or (
                invoice is not None
                and (
                    invoice.get("inventory_item_id")
                    != movement.get("inventory_item_id")
                    or invoice.get("purchase_order_id")
                    != order.get("purchase_order_id")
                    or invoice.get("goods_receipt_id")
                    != receipt.get("goods_receipt_id")
                    or invoice.get("vendor_id") != order.get("vendor_id")
                )
            )
            or (
                rni is not None
                and (
                    rni.get("inventory_item_id") != movement.get("inventory_item_id")
                    or rni.get("purchase_order_id") != order.get("purchase_order_id")
                    or rni.get("goods_receipt_id") != receipt.get("goods_receipt_id")
                    or rni.get("vendor_id") != order.get("vendor_id")
                )
            )
        ):
            raise ValueError(
                f"typed procurement bridge disagrees for {movement_id}: "
                f"movement item={movement.get('inventory_item_id')!r}, "
                f"PO item={order.get('inventory_item_id')!r}, "
                f"GRN item={receipt.get('inventory_item_id')!r}, "
                f"movement vendor={movement.get('vendor_reference')!r}, "
                f"PO vendor={vendor_names.get(str(order.get('vendor_id') or ''))!r}, "
                f"invoice={invoice!r}, RNI={rni!r}"
            )
        movement.update(
            {
                "vendor_id": order["vendor_id"],
                "purchase_order_id": order["purchase_order_id"],
                "goods_receipt_id": receipt["goods_receipt_id"],
                "vendor_invoice_id": (
                    invoice["vendor_invoice_id"] if invoice is not None else None
                ),
                "rni_accrual_item_id": (
                    rni["rni_accrual_item_id"] if rni is not None else None
                ),
            }
        )
    world["inventory_movement"] = movements


@REGISTRY.rule(
    "settle_inventory_procurement_invoices",
    inputs=(
        "company_context",
        "fiscal_calendar",
        "vendor",
        "inventory_item",
        "inventory_movement",
    ),
    outputs="ap_payment",
    contribution_priority=75,
    gate="has_inventory",
)
def settle_inventory_procurement_invoices(
    company: dict[str, Any],
    calendar: dict[str, Any],
    vendors: list[dict[str, Any]],
    items: list[dict[str, Any]],
    movements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Never inspect a mixed vendor-invoice node. Recompute this producer's
    # admitted contribution from the same frozen movement/item/vendor inputs.
    snapshot = INVENTORY_RULES_POLICY_RECORD
    _orders, _receipts, inventory_invoices = build_inventory_procurement_chain(
        company,
        calendar,
        vendors,
        items,
        movements,
        _required_surfaces=frozenset({"procurement_invoices"}),
    )
    procurement = sorted(
        inventory_invoices,
        key=lambda row: (row["due_date"], row["vendor_invoice_id"]),
    )
    if not procurement:
        return []
    year_end = date.fromisoformat(str(calendar["end_date"]))
    rows: list[dict[str, Any]] = []
    policy = snapshot["policy"]["ap_settlement"]
    target_lead = int(policy["target_business_days_before_due"])
    run_weekdays = tuple(int(value) for value in policy["payment_run_weekdays"])
    settlement_lag = int(policy["bank_settlement_business_days"])
    payment_method = str(policy["payment_method"])
    for index, invoice in enumerate(procurement, 1):
        due = date.fromisoformat(invoice["due_date"])
        posting = date.fromisoformat(invoice["posting_date"])
        target = max(_add_policy_business_days(due, -target_lead), posting)
        issue = _next_policy_run(target, run_weekdays)
        cleared = _add_policy_business_days(issue, settlement_lag)
        if cleared > year_end:
            continue
        payment_id = f"AP-PAYMENT-STK-{index:04d}"
        rows.append(
            {
                "amount": invoice["amount"],
                "ap_payment_id": payment_id,
                "bank_activity_date": cleared.isoformat(),
                "check_run_id": f"RUN-STK-{issue:%Y%m%d}",
                "cleared_date": cleared.isoformat(),
                "journal_id": f"JOURNAL-{payment_id}",
                "payable_gl_account_id": invoice["payable_gl_account_id"],
                "payment_initiated_date": issue.isoformat(),
                "payment_method": payment_method,
                "payment_reference": f"PAYREF-STK-{index:04d}",
                "posting_date": issue.isoformat(),
                "vendor_invoice_id": invoice["vendor_invoice_id"],
                "voucher_id": f"V-{payment_id}",
            }
        )
    return rows


@REGISTRY.rule(
    "accrue_rni_inventory_receipts",
    inputs=(
        "fiscal_calendar",
        "vendor",
        "inventory_item",
        "inventory_movement",
    ),
    outputs=("rni_accrual_item", "accrued_expense"),
    contribution_priority=76,
    gate="has_inventory",
)
def accrue_rni_inventory_receipts(
    calendar: dict[str, Any],
    vendors: list[dict[str, Any]],
    items: list[dict[str, Any]],
    movements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One accrued-expense row per RNI receipt, paired to its receiving record. ``post_accrued_expenses`` turns each row into the year-end journal entry, and the projection routes the inventory debit to the in-transit account."""
    snapshot = INVENTORY_RULES_POLICY_RECORD
    _orders, receipts, _invoices = build_inventory_procurement_chain(
        {},
        calendar,
        vendors,
        items,
        movements,
        _required_surfaces=frozenset({"procurement_receipts"}),
    )
    rni_items, accruals = _construct_inventory_rni_rows(
        snapshot,
        calendar,
        vendors,
        items,
        movements,
        receipts,
    )
    return rni_items, accruals


# Import here to preserve contribution registration order.
from financial_audit_bench.synthetic_binders.priors.graph.domains.inventory.costing import (  # noqa: E402
    capitalize_inventory_conversion_costs as capitalize_inventory_conversion_costs,
    relieve_inventory_cogs as relieve_inventory_cogs,
    transfer_inventory_production_stages as transfer_inventory_production_stages,
)
