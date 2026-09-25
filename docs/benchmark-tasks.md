# Benchmark tasks

The task dataset is hosted on [Hugging Face](https://huggingface.co/datasets/modusaudit/FinancialAuditBench).

## Layout

- `tasks/<task_id>/`: task configuration, instructions, starting workbook
  (`environment/workspace/`) and rubric (`tests/rubric.json`).
- `review_tasks/<task_id>/`: review tasks starting from completed model workpapers.
- `inputs/<binder_id>/`: shared PBC records, direct auditor evidence, and planning documents.

## Usage

See the [main README](../README.md) for setup and run commands. Select tasks with
`--task` or `--binder`; use `--n-attempts` for repetitions. Each run rebuilds
`artifacts/harbor/`; use one launcher at a time.

## Grading and results

Each task has one `tests/rubric.json`.

Judge settings and prompts are defined in
[judge.py](../src/financial_audit_bench/benchmark/grading/judge.py).

Results are saved under `jobs/<job>/<trial>/`: submissions in `artifacts/workspace/`,
check results in `verifier/report.json`, and graded copies in `verifier/annotated/`.
Launch configurations are saved in `experiments/`. Browse jobs with `uv run harbor view jobs`.
