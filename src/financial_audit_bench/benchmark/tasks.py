from __future__ import annotations

from pathlib import Path
import json

from harbor.models.task.task import Task

from .grading.rubric import load_rubric

def load_tasks(root: Path) -> list[Task]:
    paths = sorted(Path(root).glob("*/task.toml"))
    if not paths:
        raise ValueError(f"No native Harbor v1 tasks in {root}")
    tasks = []
    for path in paths:
        folder = path.parent
        task = Task(folder, disable_verification=True)
        if task.config.metadata.get("setting") == "review" and not (folder / "tests/template.xlsx").is_file():
            raise ValueError(f"Review task must retain the original blank verifier template: {folder}")
        if task.config.metadata.get("grading") == "pending":
            rubric = json.loads((folder / "tests/rubric.json").read_text())
        else:
            rubric = load_rubric(folder / "tests")
        filename = rubric["output"]
        if task.config.artifacts != ["/workspace/" + filename]:
            raise ValueError(f"Output artifact differs from rubric: {folder}")
        tasks.append(task)
    return tasks
