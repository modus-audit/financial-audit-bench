"""Resolve the released task assets independently of the source checkout."""

from pathlib import Path

from huggingface_hub import snapshot_download

DATASET_REPO = "modusaudit/FinancialAuditBench"
DATASET_REVISION = "tasks-2026-09-24"


def resolve_dataset(tasks_dir: Path | None = None, *, revision: str = DATASET_REVISION) -> Path:
    """Use an explicit local catalog or download a release into the Hub cache.

    Hub snapshots are read-only inputs. Prepared workspaces live under artifacts/.
    HF_HOME/HF_HUB_CACHE and HF_HUB_OFFLINE retain their standard Hub behavior.
    """
    if tasks_dir is not None:
        root = tasks_dir.expanduser().resolve()
    else:
        root = Path(snapshot_download(
            repo_id=DATASET_REPO,
            repo_type="dataset",
            revision=revision,
            allow_patterns=["tasks/**", "review_tasks/**", "inputs/**"],
        ))
    for directory in ("tasks", "review_tasks", "inputs"):
        if not (root / directory).is_dir():
            raise ValueError(f"Task dataset is missing {directory}/: {root}")
    return root
