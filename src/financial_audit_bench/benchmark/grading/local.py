"""Grade collected Harbor workbooks on the host and update native results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from harbor.metrics.mean import Mean
from harbor.models.job.config import JobConfig
from harbor.models.job.result import JobResult, JobStats
from harbor.models.trial.config import SourceTrialConfig
from harbor.models.trial.result import ExceptionInfo, TimingInfo, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.utils.pass_at_k import compute_pass_at_k_by_evals

from .recalculation_cache import BATCH_SIZE, RecalculationCache
from .results import (
    write_json,
)


def require_libreoffice() -> None:
    if shutil.which("soffice") is None:
        raise ValueError("Local grading requires LibreOffice (soffice on PATH).")


def copy_job(source: Path, output: Path) -> None:
    """Create a regrade job while retaining the original agent work and metrics."""
    output.mkdir(parents=True)
    job = JobResult.model_validate_json((source / "result.json").read_text())
    job.id = uuid4()
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.trial_results = []
    job.stats = JobStats.from_counts(n_total_trials=job.n_total_trials)
    job.updated_at = job.started_at
    config = JobConfig.model_validate_json((source / "config.json").read_text())
    config.job_name, config.jobs_dir = output.name, output.parent
    config.verifier.disable = True
    (output / "config.json").write_text(config.model_dump_json(indent=2))
    for result_path in sorted(source.glob("*/result.json")):
        trial = TrialResult.model_validate_json(result_path.read_text())
        folder = output / result_path.parent.name
        shutil.copytree(result_path.parent, folder)
        shutil.rmtree(folder / "verifier", ignore_errors=True)
        trial.config.source_trial = SourceTrialConfig(
            action="regrade", type="local", path=result_path.parent, trial_id=trial.id
        )
        trial.id = uuid4()
        trial.verifier_result = None
        trial.trial_uri = folder.as_uri()
        trial.config.job_id = job.id
        trial.config.trials_dir = output
        trial.config.verifier.disable = True
        (folder / "config.json").write_text(trial.config.model_dump_json(indent=2))
        (folder / "result.json").write_text(trial.model_dump_json(indent=2))
    (output / "result.json").write_text(job.model_dump_json(indent=2))


def _agent_failed(trial: TrialResult) -> bool:
    return bool(
        trial.exception_info
        and (
            not trial.verifier
            or not trial.verifier.started_at
            or trial.exception_info.occurred_at.timestamp()
            < trial.verifier.started_at.timestamp()
        )
    )


def grade_job(
    job_dir: Path,
    tasks: list,
    num_workers: int,
    *,
    recalculation_cache: RecalculationCache | None = None,
) -> None:
    """Run the local grader for each trial; preserve agent errors and unknown scores."""
    require_libreoffice()
    if recalculation_cache is None:
        with tempfile.TemporaryDirectory(prefix="fab-grade-cache-") as directory:
            return grade_job(
                job_dir,
                tasks,
                num_workers,
                recalculation_cache=RecalculationCache(Path(directory)),
            )
    task_by_name = {task.short_name: task for task in tasks}
    job = JobResult.model_validate_json((job_dir / "result.json").read_text())
    result_paths = sorted(job_dir.glob("*/result.json"))
    sources = []
    for result_path in result_paths:
        trial = TrialResult.model_validate_json(result_path.read_text())
        if _agent_failed(trial):
            continue
        try:
            task = task_by_name[trial.task_name.rsplit("/", 1)[-1]]
            rubric = json.loads((task.paths.task_dir / "tests/rubric.json").read_text())
            source = result_path.parent / "artifacts/workspace" / rubric["output"]
            if source.is_file() and not source.is_symlink():
                sources.append(source)
        except (OSError, ValueError, KeyError):
            continue  # The per-trial grader records the task/input error below.
    errors = recalculation_cache.prepare(sources)
    print(
        f"Prepared shared recalculation for {job_dir.name}: {len(sources)} workbooks, "
        f"{len(errors)} errors; batch size {BATCH_SIZE}.",
        flush=True,
    )

    def grade_trial(result_path: Path) -> TrialResult:
        trial = TrialResult.model_validate_json(result_path.read_text())
        # Regrade verifier failures, but never turn an agent failure into a scored success.
        if _agent_failed(trial):
            return trial
        verifier = result_path.parent / "verifier"
        verifier.mkdir(exist_ok=True)
        trial.verifier = TimingInfo(started_at=datetime.now(UTC))
        trial.verifier_result = None
        trial.verifier_environment_mode = None
        trial.exception_info = None
        (result_path.parent / "exception.txt").unlink(missing_ok=True)
        for name in ("reward.json", "reward.txt"):
            (verifier / name).unlink(missing_ok=True)
        try:
            task = task_by_name[trial.task_name.rsplit("/", 1)[-1]]
            task_dir = task.paths.task_dir
            rubric = json.loads((task_dir / "tests/rubric.json").read_text())
            template = task_dir / "tests/template.xlsx"
            if not template.is_file():
                template = task_dir / "environment/workspace" / rubric["output"]
            command = [
                sys.executable,
                "-m",
                "financial_audit_bench.benchmark.grading.grade",
                "--workspace",
                str(result_path.parent / "artifacts/workspace"),
                "--task",
                str(task_dir / "tests"),
                "--template",
                str(template),
                "--output",
                str(verifier / "report.json"),
                "--reward",
                "--recalculation-cache",
                str(recalculation_cache.directory),
            ]
            if os.environ.get("FAB_AUTOMATIC_ONLY") == "1":
                command.append("--automatic-only")
            with (verifier / "grading.log").open("w") as log:
                subprocess.run(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=task.config.verifier.timeout_sec,
                )
            rewards = json.loads((verifier / "reward.json").read_text())
            trial.verifier_result = VerifierResult(rewards=rewards)
        except Exception as error:
            trial.exception_info = ExceptionInfo.from_exception(error)
            (result_path.parent / "exception.txt").write_text(
                trial.exception_info.exception_traceback
            )
        finally:
            trial.verifier.finished_at = datetime.now(UTC)
            trial.finished_at = trial.verifier.finished_at
            result_path.write_text(trial.model_dump_json(indent=2))
        return trial

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        trials = list(pool.map(grade_trial, result_paths))
    job.stats = JobStats.from_trial_results(trials, n_total_trials=job.n_total_trials)
    rewards_by_eval = defaultdict(list)
    for trial in trials:
        key = JobStats.format_agent_evals_key(
            trial.agent_info.name,
            trial.agent_info.model_info.name if trial.agent_info.model_info else None,
            trial.source or "adhoc",
        )
        rewards_by_eval[key].append(
            trial.verifier_result.rewards if trial.verifier_result else None
        )
    for key, rewards in rewards_by_eval.items():
        job.stats.evals[key].metrics = [Mean().compute(rewards)]
    for key, values in compute_pass_at_k_by_evals(trials).items():
        job.stats.evals[key].pass_at_k = values
    job.updated_at = datetime.now(UTC)
    job.finished_at = job.updated_at
    job.trial_results = []
    (job_dir / "result.json").write_text(job.model_dump_json(indent=2))
    write_json(
        job_dir / "local_grading.json",
        {
            "graded_at": job.updated_at.isoformat(),
            "recalculation_batch_size": BATCH_SIZE,
            "mode": "local",
            "automatic_only": os.environ.get("FAB_AUTOMATIC_ONLY") == "1",
            "task_directories": {
                name: str(task.paths.task_dir) for name, task in task_by_name.items()
            },
            "n_errored_trials": job.stats.n_errored_trials,
        },
    )
