"""Fixed-decimal arithmetic for prior samplers."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, localcontext
from functools import wraps


def _fixed_decimal_context(function):
    """Run policy arithmetic independently of ambient Decimal settings."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 38
            context.rounding = ROUND_HALF_EVEN
            return function(*args, **kwargs)

    return wrapped
