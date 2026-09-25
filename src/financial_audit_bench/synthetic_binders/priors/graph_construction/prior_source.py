"""Value sampling from the published priors and authored policies."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from financial_audit_bench.synthetic_binders.priors.graph_construction.bootstrap import (
    derive_rng,
    draw_ratio,
    load_release,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction import (
    prior_source_ap as _prior_source_ap,
    prior_source_entities as _prior_source_entities,
    prior_source_opening_balances as _prior_source_opening_balances,
    prior_source_policy as _prior_source_policy,
    prior_source_workforce as _prior_source_workforce,
)
from financial_audit_bench.synthetic_binders.priors.graph_construction.authored import (
    fixed_asset_register as _fixed_asset_register,
    core_company_cash as _authored_data,
)
from financial_audit_bench.synthetic_binders.priors.graph.engine.registry import (
    World,
)
from financial_audit_bench.synthetic_binders.data_catalog import (
    load_authored_policy,
)

_BINDER_CASE = load_authored_policy("authored.global.operating-policy.v1").values[
    "binder_case"
]

# Explicit channels keep new graph fields from changing existing random draws.
RELEASE_RATIO_FIELDS = {
    "bank_reconciliation_scale": ("outstanding_checks_to_bank_balance",),
    "operating_scale": (
        "accounts_receivable_to_revenue",
        "inventory_to_cogs",
        "inventory_to_revenue",
        "payroll_to_revenue",
    ),
}


class ReleaseValueSource:
    """Sample graph inputs from released priors and authored policies."""

    def __init__(
        self,
        seed: int,
        release: dict[str, Any] | None = None,
        business_type: str = "manufacturing",
    ) -> None:
        self.seed = seed
        self.release = release if release is not None else load_release()
        self.business_type = business_type

    def has(self, node_id: str) -> bool:
        return node_id in SAMPLE_HANDLERS

    def _rng(self, *parts: Any):
        # business_type salts every stream: two binders of different types
        # at the same seed must never share a drawn series.
        return derive_rng(self.seed, self.business_type, *parts)

    def _ratio(
        self,
        target_id: str,
        default=None,
        segment: bool = True,
        segment_only: bool = False,
    ):
        """Draw one ratio target from the release (its own RNG stream)."""
        return draw_ratio(
            self.release,
            target_id,
            self._rng(target_id),
            default=default,
            segment=self.business_type if segment else None,
            segment_only=segment_only,
        )

    def _draw_ratio_dict(
        self,
        node_id: str,
        empty: str | None = None,
        segment_only: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """Draw a ``ratio.<field>`` for every field of a ratio-dict node."""
        result: dict[str, str] = {}
        for name in RELEASE_RATIO_FIELDS[node_id]:
            target_id = f"ratio.{name}"
            value = self._ratio(target_id, segment_only=name in segment_only)
            if value is None and empty is None:
                raise ValueError(f"no released value or fallback for {target_id!r}")
            result[name] = str(value) if value is not None else empty
        return result

    def sample(self, node_id: str, world: World) -> Any:
        handler = SAMPLE_HANDLERS.get(node_id)
        if handler is not None:
            return handler(self, world)
        raise KeyError(f"no sampler for graph node {node_id!r}")

    # -- entity identities ----------------------------------------------------

    def _sample_company_context(self, world: World) -> dict[str, Any]:
        del world
        source_id = "identity.company_name"
        templates = tuple(_authored_data.COMPANY_NAME_TEMPLATES[self.business_type])
        name_stems = tuple(_authored_data.COMPANY_NAME_STEMS)
        surnames = tuple(_authored_data.COMPANY_SURNAMES)
        states = tuple(_authored_data.COMPANY_STATE_POOL)
        if not templates or not name_stems or not surnames or not states:
            raise ValueError("synthetic company identity policy is incomplete")
        rng = self._rng(source_id)
        legal_name = rng.choice(templates).format(
            stem=rng.choice(name_stems),
            surname=rng.choice(surnames),
            state=rng.choice(states),
        )
        return {
            "case_id": str(_BINDER_CASE["case_id"]),
            "company_id": "COMPANY-001",
            "currency_code": "USD",
            "fiscal_year_end": "12-31",
            "legal_name": legal_name,
            "target_fiscal_year": int(_BINDER_CASE["target_fiscal_year"]),
        }

    _sample_bank_account = _prior_source_entities._sample_bank_account

    _sample_bank_account_signer = _prior_source_entities._sample_bank_account_signer

    _sample_accounting_user = _prior_source_entities._sample_accounting_user

    def _sample_general_ledger_account(self, world: World) -> list[dict[str, Any]]:
        del world
        return _authored_data.general_ledger_accounts_for(self.business_type)

    def _empty_population(self, world: World) -> list[Any]:
        """Start populations that graph rules fill from other schedules."""
        return []

    _sample_goods_receipt = _empty_population
    _sample_purchase_order = _empty_population
    _sample_accrued_expense = _empty_population
    _sample_vendor_invoice = _empty_population
    _sample_ap_payment = _empty_population

    _sample_capitalization_policy = _prior_source_policy._sample_capitalization_policy

    _sample_operating_scale = _prior_source_policy._sample_operating_scale

    _sample_bank_reconciliation_scale = (
        _prior_source_policy._sample_bank_reconciliation_scale
    )

    _sample_employee = _prior_source_workforce._sample_employee

    _sample_company_feature_profile = (
        _prior_source_workforce._sample_company_feature_profile
    )

    def _sample_fixed_asset(self, world: World) -> list[dict[str, Any]]:
        return _fixed_asset_register.build_fixed_asset_register(
            self.business_type,
            int(world["fiscal_calendar"]["fiscal_year"]),
        )

    # -- opening balance buffer ----------------------------------------------

    _sample_prior_period_bank_balance = (
        _prior_source_opening_balances._sample_prior_period_bank_balance
    )

    _sample_prior_period_account_balance = (
        _prior_source_opening_balances._sample_prior_period_account_balance
    )

    # -- AP activity: one whole entity-year book of vendor chains -------------

    # Mortgage/escrow and owner chains are excluded because the debt and
    # equity modules already generate that cash causally.
    AP_MODULE_COVERED_CLASSES = _prior_source_ap.AP_MODULE_COVERED_CLASSES

    _ap_activity_book = _prior_source_ap._ap_activity_book

    _sample_vendor = _prior_source_ap._sample_vendor


# Node IDs map to their sampler methods.
SAMPLE_HANDLERS = MappingProxyType(
    {
        name.removeprefix("_sample_"): handler
        for name, handler in vars(ReleaseValueSource).items()
        if name.startswith("_sample_") and callable(handler)
    }
)
