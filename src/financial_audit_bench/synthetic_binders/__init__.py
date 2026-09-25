"""Synthetic binder generation package."""

from typing import Any

__all__ = [
    "SyntheticBinderConfig",
    "run_synthetic_binder",
]


def __getattr__(name: str) -> Any:
    if name == "run_synthetic_binder":
        from financial_audit_bench.synthetic_binders.fsm import (
            run_synthetic_binder,
        )

        return run_synthetic_binder
    if name == "SyntheticBinderConfig":
        from financial_audit_bench.synthetic_binders import models

        return getattr(models, name)
    raise AttributeError(name)
