import pytest

from financial_audit_bench.benchmark.dataset import resolve_dataset


@pytest.fixture(scope="session")
def benchmark_dataset():
    """Integration checks use the release, not untracked task-authoring files."""
    return resolve_dataset()
