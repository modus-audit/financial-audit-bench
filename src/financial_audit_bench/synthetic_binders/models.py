from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_BUSINESS_TYPES = frozenset({"manufacturing", "staffing_services"})


@dataclass
class SyntheticBinderConfig:
    business_type: str
    seed: int
    output_dir: Path
    run_id: str | None = None

    def __post_init__(self) -> None:
        if self.business_type not in SUPPORTED_BUSINESS_TYPES:
            raise ValueError(
                f"unsupported business type: {self.business_type!r}; "
                f"supported: {sorted(SUPPORTED_BUSINESS_TYPES)}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "business_type": self.business_type,
            "seed": self.seed,
            "output_dir": str(self.output_dir),
            "run_id": self.run_id,
        }
