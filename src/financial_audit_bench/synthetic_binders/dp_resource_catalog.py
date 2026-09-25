"""Access to the packaged DP priors in a source tree or zipped wheel."""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from functools import cache
from importlib import resources
from pathlib import Path
from threading import RLock

_RESOURCE_CONTEXTS = ExitStack()
_RESOURCE_LOCK = RLock()
atexit.register(_RESOURCE_CONTEXTS.close)


@cache
def runtime_dp_root() -> Path:
    """Keep the packaged prior directory available for the process lifetime."""
    node = resources.files("financial_audit_bench.synthetic_binders").joinpath(
        "data", "dp", "releases", "dp-priors"
    )
    with _RESOURCE_LOCK:
        return _RESOURCE_CONTEXTS.enter_context(resources.as_file(node))
