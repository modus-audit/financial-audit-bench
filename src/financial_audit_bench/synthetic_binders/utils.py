from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def json_compatible(value: Any) -> Any:
    """Convert Python values to a stable, JSON-compatible form."""
    if isinstance(value, Mapping):
        return {str(key): json_compatible(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        items = [json_compatible(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items.sort(
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
            )
        return items

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return json_compatible(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    return value


def resolve_json_path(path: Path) -> Path:
    """Resolve a JSON artifact, accepting its deterministic gzip form."""
    if path.exists():
        return path
    compressed = Path(f"{path}.gz")
    return compressed if compressed.exists() else path


def read_json(path: Path) -> Any:
    source = resolve_json_path(path)
    if source.suffix == ".gz":
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(source.read_text())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_compatible(payload), indent=2, sort_keys=True) + "\n"
    )
