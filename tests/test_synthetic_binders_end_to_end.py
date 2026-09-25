from __future__ import annotations

import json

import pytest

from financial_audit_bench.synthetic_binders.fsm import run_synthetic_binder
from financial_audit_bench.synthetic_binders.models import SyntheticBinderConfig


@pytest.mark.parametrize("business_type", ["manufacturing", "staffing_services"])
def test_supported_business_type_generates_package(
    tmp_path, business_type: str
) -> None:
    output_dir = tmp_path / business_type

    result = run_synthetic_binder(
        SyntheticBinderConfig(
            business_type=business_type,
            seed=42,
            output_dir=output_dir,
            run_id=f"test-{business_type}",
        )
    )

    manifest = json.loads((output_dir / "generation_manifest.json").read_text())
    assert result["status"] == "package_ready"
    assert manifest["business_type"] == business_type
    assert manifest["validation_status"] == "passed"
    assert manifest["artifacts"]


def test_start_does_not_modify_unrelated_directory(tmp_path) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    existing_file = output_dir / "keep.txt"
    existing_file.write_text("keep")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        run_synthetic_binder(
            SyntheticBinderConfig(
                business_type="manufacturing",
                seed=42,
                output_dir=output_dir,
            )
        )

    assert list(output_dir.iterdir()) == [existing_file]
