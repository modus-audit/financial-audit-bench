from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile

MARKER = ".fab-harbor.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source: Path, target: Path, hashes: dict[str, str], root: Path) -> None:
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            if child.name != ".DS_Store" and not child.name.startswith("~$"):
                copy(child, target / child.name, hashes, root)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    hashes[str(source.relative_to(root))] = digest(source)


def copy_inputs(binder: str, target: Path, hashes: dict[str, str], root: Path) -> None:
    source = root / "inputs" / binder
    for folder in ("pbc_package", "direct_to_auditor", "planning"):
        copy(source / folder, target / folder, hashes, root)


def prepare(tasks: list, root: Path, *, dataset_root: Path | None = None) -> Path:
    """Replace the fixed Harbor build only after its replacement is complete."""
    if not tasks:
        raise ValueError("No tasks to prepare")
    if dataset_root is None:
        dataset_root = tasks[0].paths.task_dir.parent.parent
    output = root / "artifacts/harbor"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".harbor-build-", dir=output.parent) as directory:
        staging = Path(directory)
        hashes: dict[str, str] = {}
        for task in tasks:
            source = task.paths.task_dir
            target = staging / task.short_name
            copy(source, target, hashes, dataset_root)
            copy_inputs(task.config.metadata["binder"], target / "environment/workspace", hashes, dataset_root)
            rubric = json.loads((source / "tests/rubric.json").read_text())
            template = source / "tests/template.xlsx"
            if not template.is_file():
                template = source / "environment/workspace" / rubric["output"]
            copy(template, target / "tests/template.xlsx", hashes, dataset_root)
        manifest = {"harbor_version": "0.22.0", "task_version": "1.0.0",
                    "task_ids": [task.short_name for task in tasks],
                    "source_sha256": hashes}
        (staging / MARKER).write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            shutil.rmtree(output)
        shutil.move(str(staging), output)
    return output
