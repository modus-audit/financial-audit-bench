"""Bootstrap samplers over a priors release, one per link type."""

from __future__ import annotations

import random
from typing import Any

from financial_audit_bench.synthetic_binders.dp_resource_catalog import (
    runtime_dp_root,
)
from financial_audit_bench.synthetic_binders.utils import read_json


def load_release() -> dict[str, Any]:
    """Read the published release; ``blocks`` is keyed by block family."""
    release_dir = runtime_dp_root()
    manifest_path = release_dir / "manifest.json"
    manifest = read_json(manifest_path)

    payloads = manifest["payloads"]
    financial_path = release_dir / payloads["financial_priors"]["path"]
    financial_priors = read_json(financial_path)
    blocks = {
        family: read_json(release_dir / entry["path"])["blocks"]
        for family, entry in payloads["blocks"].items()
    }
    return {
        "financial_priors": financial_priors,
        "blocks": blocks,
    }


def derive_rng(seed: int, *parts: Any) -> random.Random:
    return random.Random("|".join((str(seed), *(str(part) for part in parts))))


def _usable_values(release: dict[str, Any], target_id: str) -> list[Any]:
    entry = release["financial_priors"].get(target_id)
    if not entry or not entry["quality"]["usable_for_sampling"]:
        return []
    return entry["values"]


def draw_ratio(
    release: dict[str, Any],
    target_id: str,
    rng: random.Random,
    default: float | None = None,
    segment: str | None = None,
    segment_only: bool = False,
) -> float | None:
    """Draw a value from the released DP priors."""
    values = _usable_values(release, target_id)
    if segment:
        in_segment = [row for row in values if row.get("business_type") == segment]
        if len(in_segment) >= 2:
            values = in_segment
        elif segment_only:
            return default
    if not values:
        return default
    row = values[rng.randrange(len(values))]
    return float(row["value"])


def _family_pool(
    release: dict[str, Any],
    family: str,
    business_types: set[str] | None,
    predicate: Any = None,
    segment_only: bool = False,
) -> list[dict[str, Any]]:
    """DP-synthetic pool for a block family, preferring its segment."""
    pool = [
        block
        for block in release["blocks"].get(family, [])
        if predicate is None or predicate(block)
    ]
    if business_types is not None:
        in_segment = [
            block for block in pool if block["business_type"] in business_types
        ]
        if in_segment:
            return in_segment
        if segment_only:
            return []
    return pool


def bootstrap_book(
    release: dict[str, Any],
    family: str,
    rng: random.Random,
    business_types: set[str] | None = None,
    predicate: Any = None,
    segment_only: bool = False,
) -> list[dict[str, Any]]:
    """Draw one DP-synthetic entity-year book intact."""
    by_book: dict[str, list[dict[str, Any]]] = {}
    for block in _family_pool(
        release, family, business_types, predicate, segment_only=segment_only
    ):
        by_book.setdefault(block["binder_id"], []).append(block)
    books = sorted(book for book, units in by_book.items() if units)
    if not books:
        return []
    return by_book[books[rng.randrange(len(books))]]
