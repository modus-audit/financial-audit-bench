from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from financial_audit_bench.benchmark.build import MARKER, prepare
from financial_audit_bench.benchmark.dataset import DATASET_REVISION, resolve_dataset
from financial_audit_bench.benchmark.grading.local import (
    copy_job,
    grade_job,
    require_libreoffice,
)
from financial_audit_bench.benchmark.grading.recalculation_cache import (
    RecalculationCache,
)
from financial_audit_bench.defaults import DEFAULT_MAX_MODEL_CALLS
from financial_audit_bench.synthetic_binders.models import (
    SUPPORTED_BUSINESS_TYPES,
    SyntheticBinderConfig,
)

DEFAULT_BINDERS_DIR = "generated_binders"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_generate_synthetic_binder_parser(subparsers)
    add_run_parser(subparsers)
    add_regrade_parser(subparsers)
    return parser


def add_dataset_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--tasks-dir", type=Path,
                        help="Use a local dataset containing tasks/, review_tasks/, and inputs/ instead of Hugging Face.")
    source.add_argument("--dataset-revision", default=DATASET_REVISION,
                        help=f"Hugging Face task revision (default: {DATASET_REVISION}).")


def add_run_parser(subparsers) -> None:
    parser = subparsers.add_parser("run", help="Rebuild tasks and run the benchmark with Harbor.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--model",
                           help="Model name, e.g. gpt-6-astra or openrouter/google/gemini-3.8-flash.")
    selection.add_argument("--agent", choices=("oracle", "nop"), help="Run a Harbor diagnostic control instead of a model.")
    parser.add_argument("--reasoning-effort", default="medium", help="Model reasoning effort (default: medium).")
    parser.add_argument("--temperature", type=float, help="Sampling temperature; omitted by default.")
    parser.add_argument("--review", action="store_true", help="Use the review task catalog.")
    parser.add_argument("--task", action="append", dest="task_ids", help="Task ID; repeat to select more.")
    parser.add_argument("--binder", help="Input binder name, e.g. manufacturing_v1_01.")
    parser.add_argument("--limit", type=int, help="Maximum number of tasks.")
    parser.add_argument("--max-model-calls", type=int, default=DEFAULT_MAX_MODEL_CALLS)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--n-attempts", type=int, default=1)
    parser.add_argument("--grade", action="store_true", help="Grade saved workbooks locally after execution.")
    add_dataset_arguments(parser)
    parser.set_defaults(func=run_benchmark)


def run_benchmark(args: argparse.Namespace) -> None:
    from harbor.models.job.config import JobConfig

    from financial_audit_bench.benchmark.tasks import load_tasks

    root = Path.cwd()
    if args.agent == "oracle" and args.tasks_dir is None:
        raise ValueError("The published dataset has no reference solutions. "
                         "Oracle controls require --tasks-dir with local solution files.")
    model_id = args.agent or args.model
    job_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", model_id).strip(".-")
    if not job_suffix:
        raise ValueError("Model name must contain a letter or digit")

    dataset_root = resolve_dataset(args.tasks_dir, revision=args.dataset_revision)
    tasks = load_tasks(dataset_root / ("review_tasks" if args.review else "tasks"))
    if args.task_ids:
        tasks = [task for task in tasks if task.short_name in args.task_ids]
    if args.binder:
        tasks = [task for task in tasks if task.config.metadata["binder"] == args.binder]
    if not tasks:
        raise ValueError("No tasks match the selected binder and task IDs")
    pending = [task.short_name for task in tasks if task.config.metadata.get("grading") == "pending"]
    if pending and (args.grade or args.agent == "oracle"):
        raise ValueError("Grading and oracle controls are not ready for: " + ", ".join(pending)
                         + ". Run these tasks without --grade and with a model or nop agent.")

    if args.grade:
        require_libreoffice()
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    output = root / "jobs"
    experiment = root / "experiments" / run_id
    model_kwargs = {"reasoning_effort": args.reasoning_effort}
    if args.temperature is not None:
        model_kwargs["temperature"] = args.temperature
    agent = ({"name": args.agent} if args.agent else {
        "import_path": "financial_audit_bench.benchmark.agent:AuditAgent",
        "model_name": args.model,
        "kwargs": {"max_model_calls": args.max_model_calls, "model_kwargs": model_kwargs},
    })
    config = JobConfig.model_validate({
        "job_name": f"{run_id}-{job_suffix}", "jobs_dir": "jobs",
        "n_concurrent_trials": args.num_workers, "n_attempts": args.n_attempts,
        "retry": {"max_retries": 0}, "verifier": {"disable": True},
        "datasets": [{"path": "artifacts/harbor", "n_tasks": args.limit}],
        "agents": [agent],
    })
    prepared = prepare(tasks, root, dataset_root=dataset_root)
    output.mkdir(parents=True, exist_ok=True)
    experiment.mkdir(parents=True)
    shutil.copy2(prepared / MARKER, experiment / MARKER)
    print(f"Prepared {len(tasks)} tasks in {prepared}", flush=True)
    print(f"Saving jobs to {output}; experiment metadata to {experiment}", flush=True)
    config_path = experiment / (config.job_name + ".json")
    config_path.write_text(config.model_dump_json(indent=2, exclude_none=True) + "\n")
    (experiment / "run.json").write_text(json.dumps({
        "kind": "benchmark_run", "run_id": run_id, "local_grading": args.grade,
        "dataset_path": str(dataset_root),
        "task_ids": [task.short_name for task in tasks],
        "jobs": [{"model_id": model_id, "path": f"jobs/{config.job_name}",
                  "config": config_path.name}],
    }, indent=2) + "\n")

    with ExitStack() as stack:
        recalculation_cache = None
        if args.grade:
            directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="fab-grade-cache-"))
            recalculation_cache = RecalculationCache(Path(directory))
        subprocess.run([sys.executable, "-m", "harbor.cli.main", "run", "-c", str(config_path), "-y"],
                       cwd=root, check=True)
        if args.grade:
            from financial_audit_bench.benchmark.tasks import load_tasks
            grade_job(output / config.job_name, load_tasks(prepared), args.num_workers,
                      recalculation_cache=recalculation_cache)
        require_completed_job(output / config.job_name)


def require_completed_job(path: Path) -> None:
    result = json.loads((path / "result.json").read_text())
    stats = result["stats"]
    if stats["n_errored_trials"] or stats["n_completed_trials"] != result["n_total_trials"]:
        raise RuntimeError(f"Incomplete Harbor job: {path}")


def add_regrade_parser(subparsers) -> None:
    parser = subparsers.add_parser("regrade", help="Regrade a saved Harbor job locally using current rubrics.")
    parser.add_argument("source", type=Path, help="Original Harbor job directory containing config.json and result.json.")
    parser.add_argument("--review", action="store_true", help="Use the review task catalog.")
    parser.add_argument("--num-workers", type=int, default=4)
    add_dataset_arguments(parser)
    parser.set_defaults(func=regrade_benchmark)


def regrade_benchmark(args: argparse.Namespace) -> None:
    from financial_audit_bench.benchmark.tasks import load_tasks

    root = Path.cwd()
    source = args.source.resolve()
    require_libreoffice()
    source_id = json.loads((source / "result.json").read_text())["id"]
    dataset_root = resolve_dataset(args.tasks_dir, revision=args.dataset_revision)
    tasks = load_tasks(dataset_root / ("review_tasks" if args.review else "tasks"))
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    name = f"{run_id}-regrade-{source_id[:8]}"
    output = root / "jobs" / name
    experiment = root / "experiments" / run_id
    experiment.mkdir(parents=True)
    (experiment / "run.json").write_text(json.dumps({
        "kind": "regrade", "run_id": run_id,
        "dataset_path": str(dataset_root),
        "source_job": {"path": str(source), "id": source_id},
        "jobs": [{"path": f"jobs/{name}"}],
        "task_ids": [task.short_name for task in tasks], "grading": "local",
    }, indent=2) + "\n")
    print(f"Regrading {source}; saving native Harbor results to {output}", flush=True)
    copy_job(source, output)
    grade_job(output, tasks, args.num_workers)
    require_completed_job(output)


def add_generate_synthetic_binder_parser(subparsers):
    synthetic_parser = subparsers.add_parser(
        "generate-synthetic-binder",
    )
    synthetic_parser.add_argument(
        "business_type", choices=sorted(SUPPORTED_BUSINESS_TYPES)
    )
    synthetic_parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        help="Output directory.",
    )
    synthetic_parser.add_argument("--seed", type=int, default=42)
    synthetic_parser.add_argument("--run-id")
    synthetic_parser.set_defaults(func=generate_synthetic_binder_command)


def generate_synthetic_binder_command(args: argparse.Namespace) -> None:
    from financial_audit_bench.synthetic_binders.fsm import run_synthetic_binder

    output_dir = args.output_dir or Path(DEFAULT_BINDERS_DIR) / args.business_type
    manifest = run_synthetic_binder(
        SyntheticBinderConfig(
            business_type=args.business_type,
            seed=args.seed,
            output_dir=output_dir,
            run_id=args.run_id,
        )
    )
    print(f"Generated synthetic binder {manifest['run_id']} at {output_dir}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    try:
        args.func(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
