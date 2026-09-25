"""Target graph populations and their manufacturing/staffing rule packages."""

from financial_audit_bench.synthetic_binders.priors.graph.engine.registry_instance import (
    REGISTRY,
)


for _node_id in (
    "accounting_user",
    "accounts_payable_aging",
    "accounts_payable_open_item",
    "accounts_receivable_aging",
    "accounts_receivable_rollforward",
    "accrued_expense",
    "ap_payment",
    "ar_allowance_estimate",
    "ar_allowance_policy",
    "ar_cutoff_item",
    "ar_receipt_application",
    "bank_account_signer",
    "bank_book_reconciliation",
    "bank_reconciling_item",
    "bank_statement_month",
    "capitalization_policy",
    "cash_book_movement",
    "cash_movement",
    "customer",
    "customer_cash_receipt",
    "customer_contract",
    "customer_credit_adjustment",
    "customer_invoice",
    "customer_invoice_line",
    "employee",
    "final_adjusted_trial_balance_account",
    "fixed_asset",
    "fixed_asset_depreciation",
    "fixed_asset_gl_reconciliation",
    "fixed_asset_rollforward",
    "fulfillment_event",
    "general_ledger_account",
    "goods_receipt",
    "inventory_count",
    "inventory_count_line",
    "inventory_gl_reconciliation",
    "inventory_item",
    "inventory_location",
    "inventory_lot_balance",
    "inventory_movement",
    "inventory_rollforward",
    "journal_entry",
    "journal_entry_line",
    "opening_account_balance",
    "payroll_authorization_event",
    "payroll_bank_account",
    "payroll_register_line",
    "payroll_remittance",
    "payroll_run",
    "payroll_subsequent_event",
    "prior_period_account_balance",
    "purchase_order",
    "rni_accrual_item",
    "staffing_time_entry",
    "steel_production_order",
    "trial_balance_account",
    "vendor",
    "vendor_invoice",
):
    REGISTRY.node(_node_id)

for _node_id in (
    "bank_account",
    "company_context",
    "company_feature_profile",
    "fiscal_calendar",
    "operating_scale",
    "prior_period_bank_balance",
):
    REGISTRY.node(_node_id, singleton=True)

from financial_audit_bench.synthetic_binders.priors.graph.domains import (  # noqa: F401
    ap,
    cash,
    core,
    fixed_assets,
    inventory,
    journal,
    noise,
    optional,
    payroll,
    receivables,
    schema_defaults,
    tb,
)


def families_for(profile) -> frozenset[str]:
    """Add the shared core domain to an already validated target profile."""
    return profile.families | {"core"}


# Cross-cutting checks register on domain import. ``for_families`` drops checks
# whose inputs are not composed, so registering them unconditionally is safe.
from financial_audit_bench.synthetic_binders.priors.graph import (  # noqa: E402,F401
    cross_document,
    validation,
)
