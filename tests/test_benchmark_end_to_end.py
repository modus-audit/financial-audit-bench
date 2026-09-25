"""Benchmark workflows: released assets, CLI jobs, agent tool loops, and regrading.

External model HTTP responses and Docker execution are replaced with deterministic
stand-ins; SDK serialization, trajectories, and saved job results are exercised.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories.trajectory import Trajectory
from financial_audit_bench.benchmark import agent
from financial_audit_bench.llms import LLMResponse
from copy import deepcopy
import httpx
from financial_audit_bench.benchmark.agent import AuditAgent
import os
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from harbor.models.job.config import JobConfig
from harbor.models.job.result import JobResult
from harbor.viewer.scanner import JobScanner
from harbor.models.trial.result import TrialResult
from financial_audit_bench.benchmark.grading import local
from financial_audit_bench import cli
from financial_audit_bench.benchmark import tasks
import hashlib
from financial_audit_bench.benchmark.build import MARKER, prepare
from financial_audit_bench.benchmark.tasks import load_tasks


def response(content='', calls=None):
    return LLMResponse(content, 'gpt-test', {'input_tokens': 10, 'output_tokens': 5},
                       .01, calls or [], [], {'output': []}, .1)


@pytest.mark.parametrize('missing_cost', [False, True])
def test_agent_executes_tools_reminds_and_records_native_trajectory(tmp_path, monkeypatch, missing_cost):
    commands, messages = [], []
    answers = iter([
        response(calls=[{'id': 'c1', 'function': {'name': 'bash', 'arguments': '{"command":"ls"}'}}]),
        replace(response('Still working.'), cost=None if missing_cost else .01),
        response('Done.\n<<TASK_FINISHED>>'),
    ])
    async def complete(**kwargs):
        messages.append(list(kwargs['messages']))
        return next(answers)
    async def execute(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(return_code=0, stdout='workpaper.xlsx', stderr='')
    monkeypatch.setattr(agent, 'acomplete', complete)
    context = AgentContext()
    runner = agent.AuditAgent(logs_dir=tmp_path, max_model_calls=4)
    asyncio.run(runner.run('Complete the workpaper.', SimpleNamespace(exec=execute), context))
    assert commands == [('timeout --kill-after=5s 600s bash -lc ls', {'cwd': '/workspace', 'timeout_sec': 610})]
    assert messages[1][-1]['role'] == 'tool'
    assert messages[2][-1]['content'] == agent.load_prompt('reminder')
    assert context.metadata == {'model_calls': 3, 'termination_reason': 'completed'}
    assert (context.n_input_tokens, context.n_output_tokens, context.cost_usd) == (30, 15, None if missing_cost else .03)
    trajectory = Trajectory.model_validate_json((tmp_path / 'trajectory.json').read_text())
    assert trajectory.steps[2].observation.results[0].source_call_id == 'c1'


@pytest.mark.parametrize('failure', ['budget', 'cancelled', 'error'])
def test_agent_limits_and_errors_preserve_trajectory(tmp_path, monkeypatch, failure):
    async def complete(**kwargs):
        if failure == 'cancelled':
            raise asyncio.CancelledError()
        if failure == 'error':
            raise RuntimeError('provider unavailable')
        return response('Still working.')
    monkeypatch.setattr(agent, 'acomplete', complete)
    context = AgentContext()
    runner = agent.AuditAgent(logs_dir=tmp_path, max_model_calls=1)
    async def run():
        await runner.run('Work.', SimpleNamespace(), context)
    if failure == 'budget':
        asyncio.run(run())
        assert context.metadata['termination_reason'] == 'max_model_calls'
    else:
        with pytest.raises(asyncio.CancelledError if failure == 'cancelled' else RuntimeError):
            asyncio.run(run())
        assert context.metadata['termination_reason'] == failure
    assert context.metadata['model_calls'] == 1
    Trajectory.model_validate_json((tmp_path / 'trajectory.json').read_text())


def test_multiple_tools_preserve_completed_and_pending_calls(tmp_path, monkeypatch):
    async def complete(**kwargs):
        return response(calls=[
            {"id": "c1", "function": {"name": "bash", "arguments": '{"command":"echo first"}'}},
            {"id": "c2", "function": {"name": "bash", "arguments": '{"command":"sleep 100"}'}},
        ])
    async def execute(command, **kwargs):
        if "sleep" in command:
            raise asyncio.CancelledError()
        return SimpleNamespace(return_code=0, stdout="first", stderr="")
    monkeypatch.setattr(agent, "acomplete", complete)
    context = AgentContext()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.AuditAgent(logs_dir=tmp_path).run("Work.", SimpleNamespace(exec=execute), context))
    trace = Trajectory.model_validate_json((tmp_path / "trajectory.json").read_text())
    step = trace.steps[-1]
    assert [call.tool_call_id for call in step.tool_calls] == ["c1", "c2"]
    assert step.tool_calls[1].arguments['command'] == 'sleep 100'
    assert context.metadata['termination_reason'] == 'cancelled'
    assert [result.source_call_id for result in step.observation.results] == ["c1"]
    assert json.loads(step.observation.results[0].content)["stdout"] == "first"


def tool_call(index):
    return {"id": f"call_{index}", "type": "function", "function": {
        "name": "bash", "arguments": json.dumps({"command": f"echo {index}"}),
    }}


def provider_response(provider, turn, text_only_first=False):
    """Synthetic opaque state, including omitted and redacted Claude thinking."""
    calls = [tool_call(f"{turn}_{i}") for i in range(2)] if turn < 3 and not (text_only_first and turn == 1) else []
    content = "Checking." if turn < 3 else "Done.\n<<TASK_FINISHED>>"
    if provider == "anthropic":
        blocks = [
            {"type": "thinking", "thinking": "", "signature": f"opaque-{turn}"},
            {"type": "redacted_thinking", "data": f"redacted-{turn}"},
            {"type": "text", "text": content},
            *[{"type": "tool_use", "id": c["id"], "name": "bash",
               "input": json.loads(c["function"]["arguments"]), "caller": {"type": "direct"}} for c in calls],
        ]
        return {"id": f"msg_{turn}", "type": "message", "role": "assistant",
                "model": "claude-opus-4-6", "content": blocks,
                "stop_reason": "tool_use" if calls else "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    if provider == "openrouter":
        # OpenRouter binds encrypted Gemini thought signatures to tool-call IDs.
        details = [{"type": "reasoning.text", "text": "Synthetic reasoning.",
                    "index": 0, "format": "unknown"},
                   {"type": "reasoning.encrypted", "data": f"opaque-{turn}",
                    "id": calls[0]["id"] if calls else "final",
                    "index": 1, "format": "google-gemini-v1"}]
        return {"id": f"chat_{turn}", "object": "chat.completion", "created": 0,
                "model": "google/gemini-2.5-pro", "choices": [{"index": 0,
                "finish_reason": "tool_calls" if calls else "stop", "message": {
                    "role": "assistant", "content": content, "tool_calls": calls,
                    "reasoning": "Synthetic reasoning.", "reasoning_details": details}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    output = [{"id": f"rs_{turn}", "type": "reasoning", "summary": [],
               "encrypted_content": f"opaque-{turn}"},
              {"id": f"msg_{turn}", "type": "message", "role": "assistant",
               "status": "completed", "content": [
                   {"type": "output_text", "text": content, "annotations": [], "logprobs": None}]},
              *[{"type": "function_call", "id": f"fc_{c['id']}", "call_id": c["id"],
                 "name": "bash", "arguments": c["function"]["arguments"],
                 "status": "completed"} for c in calls]]
    return {"id": f"resp_{turn}", "object": "response", "created_at": 0,
            "model": "gpt-5", "status": "completed", "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


@pytest.mark.parametrize("provider,model", [
    ("anthropic", "claude-opus-4-6"),
    ("openrouter", "openrouter/google/gemini-2.5-pro"),
    ("openai", "gpt-5"),
])
@pytest.mark.parametrize("text_only_first", [False, True])
def test_agent_round_trips_provider_state_on_actual_wire(tmp_path, monkeypatch, provider, model, text_only_first):
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    requests, responses, commands = [], [], []

    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        requests.append(body)
        payload = provider_response(provider, len(requests), text_only_first)
        responses.append(deepcopy(payload))
        return httpx.Response(200, json=payload, request=request)

    async def execute(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(return_code=0, stdout="ok", stderr="")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    context = AgentContext()
    runner = AuditAgent(logs_dir=tmp_path, model_name=model, max_model_calls=3,
                        model_kwargs={"reasoning_effort": "medium"})
    asyncio.run(runner.run("Use bash twice, then finish.", SimpleNamespace(exec=execute), context))
    assert len(requests) == 3
    assert len(commands) == (2 if text_only_first else 4)
    assert context.metadata["termination_reason"] == "completed"
    for turn, request in enumerate(requests[1:], start=1):
        previous = responses[:turn]
        if provider == "anthropic":
            assistants = [m for m in request["messages"] if m["role"] == "assistant"]
            # Anthropic reports caller=direct; this is response metadata, not tool input.
            expected = [[{k: v for k, v in b.items() if k != "caller"} for b in p["content"]]
                        for p in previous]
            assert [m["content"] for m in assistants] == expected
            results = [b for m in request["messages"] for b in m["content"]
                       if b["type"] == "tool_result"]
            assert [b["tool_use_id"] for b in results] == [
                b["id"] for p in previous for b in p["content"] if b["type"] == "tool_use"]
        elif provider == "openrouter":
            assistants = [m for m in request["messages"] if m["role"] == "assistant"]
            for actual, original in zip(assistants, previous, strict=True):
                message = original["choices"][0]["message"]
                assert actual["reasoning_details"] == message["reasoning_details"]
                assert actual.get("tool_calls", []) == message["tool_calls"]
            results = [m["tool_call_id"] for m in request["messages"] if m["role"] == "tool"]
            assert results == [c["id"] for p in previous for c in p["choices"][0]["message"]["tool_calls"]]
        else:
            assert request["store"] is False
            assert "reasoning.encrypted_content" in request["include"]
            output = [i for i in request["input"] if i.get("type") in {"reasoning", "message", "function_call"}]
            expected = [{k: v for k, v in i.items() if k != "status"}
                        for p in previous for i in p["output"]]
            # SDK model_dump includes optional null fields; compare all original fields.
            assert len(output) == len(expected)
            for actual, original in zip(output, expected, strict=True):
                assert {k: actual[k] for k in original} == original
            results = [i["call_id"] for i in request["input"] if i.get("type") == "function_call_output"]
            assert results == [i["call_id"] for p in previous for i in p["output"] if i["type"] == "function_call"]


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = [SimpleNamespace(short_name=name, config=SimpleNamespace(metadata={"binder": binder}))
               for name, binder in [("cash", "manufacturing"), ("debt", "staffing")]]
    monkeypatch.setattr(tasks, "load_tasks", lambda *_a, **_k: catalog)
    monkeypatch.setattr(cli, "resolve_dataset", lambda *_a, **_k: tmp_path / "hub-snapshot")
    return tmp_path


@pytest.mark.parametrize("grade", [False, True])
@pytest.mark.parametrize("model,options,expected_kwargs", [
    ("gpt-test", [], {"reasoning_effort": "medium"}),
    ("openrouter/provider/test", ["--reasoning-effort", "high", "--temperature", "1"],
     {"reasoning_effort": "high", "temperature": 1.0}),
    (None, [], None),
])
def test_run_builds_once_and_preserves_previous_results(checkout, monkeypatch, grade, model, options, expected_kwargs):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (checkout / ".env").write_text("OPENAI_API_KEY=file-key\n")
    old = checkout / "jobs/previous/result.json"
    old.parent.mkdir(parents=True)
    old.write_text("previous result")
    events, graded_jobs, caches = [], [], []

    def prepare(selected, root, *, dataset_root):
        events.append("build")
        assert dataset_root == checkout / "hub-snapshot"
        assert root == checkout and [t.short_name for t in selected] == ["cash"]
        built = root / "artifacts/harbor"
        built.mkdir(parents=True, exist_ok=True)
        (built / cli.MARKER).write_text('{"build": "current"}')
        return built

    def harbor(command, **kwargs):
        assert events[0] == "build" and os.environ["OPENAI_API_KEY"] == "file-key"
        assert kwargs == {"cwd": checkout, "check": True}
        config = JobConfig.model_validate_json(Path(command[command.index("-c") + 1]).read_text())
        assert config.datasets[0].path == Path("artifacts/harbor")
        assert config.n_concurrent_trials == 25 and config.n_attempts == 8
        assert config.verifier.disable
        assert config.jobs_dir == Path("jobs")
        if model:
            assert config.agents[0].model_name == model
            assert config.agents[0].kwargs["model_kwargs"] == expected_kwargs
        else:
            assert config.agents[0].name == "nop"
        assert Path(command[command.index("-c") + 1]).is_relative_to(checkout / "experiments")
        assert "/" not in config.job_name
        events.append("run")
        result = config.jobs_dir / config.job_name / "result.json"
        result.parent.mkdir()
        (result.parent / "config.json").write_text(config.model_dump_json())
        result.write_text(json.dumps({"n_total_trials": 8,
            "stats": {"n_completed_trials": 8, "n_errored_trials": 0}}))

    monkeypatch.setattr(cli, "prepare", prepare)
    def grade_job(job, tasks, workers, *, recalculation_cache):
        assert recalculation_cache.directory.exists()
        caches.append(recalculation_cache)
        graded_jobs.append(job)
    monkeypatch.setattr(cli, "grade_job", grade_job)
    monkeypatch.setattr(cli.subprocess, "run", harbor)
    selection = ["--model", model] if model else ["--agent", "nop"]
    monkeypatch.setattr("sys.argv", ["fab", "run", *selection, *options,
                                   "--task", "cash", "--num-workers", "25",
                                   "--n-attempts", "8"] + (["--grade"] if grade else []))
    cli.main()
    cli.main()
    assert events == ["build", "run", "build", "run"]
    runs = [p for p in (checkout / "jobs").iterdir() if p.name != "previous"]
    assert len(runs) == 2
    experiments = [p for p in (checkout / "experiments").iterdir() if p.is_dir()]
    assert len(experiments) == 2
    assert all((p / cli.MARKER).read_text() == '{"build": "current"}' for p in experiments)
    scanner = JobScanner(checkout / "jobs")
    assert all(scanner.get_job_config(p.name) is not None for p in runs)
    recorded = [job["path"] for p in experiments for job in json.loads((p / "run.json").read_text())["jobs"]]
    assert {checkout / path for path in recorded} == set(runs)
    assert all(json.loads((p / "run.json").read_text())["jobs"][0]["model_id"] == (model or "nop")
               for p in experiments)
    assert old.read_text() == "previous result"
    assert len(graded_jobs) == (2 if grade else 0)
    if grade:
        assert caches[0] is not caches[1]
        assert all(not cache.directory.exists() for cache in caches)


@pytest.mark.parametrize("incomplete", [False, True])
def test_local_regrade_updates_native_results_and_preserves_source(checkout, monkeypatch, incomplete):
    source = checkout / "jobs/original"
    folder = source / "cash__trial"
    (folder / "artifacts/workspace").mkdir(parents=True)
    (folder / "agent").mkdir()
    (folder / "agent/trajectory.json").write_text('{"original": true}')
    (folder / "artifacts/workspace/answer.xlsx").write_bytes(b"original submission")
    job = JobResult(id=uuid4(), started_at=datetime.now(UTC), n_total_trials=1, stats={})
    (source / "config.json").write_text(JobConfig(job_name="original").model_dump_json())
    (source / "result.json").write_text(job.model_dump_json())
    trial = TrialResult(
        task_name="financial-audit-bench/cash", trial_name=folder.name,
        trial_uri=folder.as_uri(), task_id={"path": checkout / "task"}, task_checksum="original",
        config={"task": {"path": checkout / "task"}, "agent": {"name": "nop"}},
        agent_info={"name": "nop", "version": "1.0.0"},
        agent_result={"n_input_tokens": 100, "n_output_tokens": 25, "cost_usd": .5},
    )
    (folder / "result.json").write_text(trial.model_dump_json())
    (folder / "verifier").mkdir()
    (folder / "verifier/reward.json").write_text('{"reward": 1}')
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    task_dir = checkout / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests/rubric.json").write_text('{"output":"answer.xlsx"}')
    (task_dir / "tests/template.xlsx").write_bytes(b"blank template")
    catalog = [SimpleNamespace(short_name="cash", paths=SimpleNamespace(task_dir=task_dir),
               config=SimpleNamespace(verifier=SimpleNamespace(timeout_sec=3600)))]
    monkeypatch.setattr(tasks, "load_tasks", lambda *_: catalog)
    monkeypatch.setattr(cli, "prepare", lambda *_: pytest.fail("regrade must not rebuild Docker tasks"))

    @contextmanager
    def convert(sources, **kwargs):
        yield sources
    monkeypatch.setattr("financial_audit_bench.benchmark.grading.recalculate.recalculated_workbooks", convert)

    def grade(command, **kwargs):
        assert command[1:3] == ["-m", "financial_audit_bench.benchmark.grading.grade"]
        workspace = Path(command[command.index("--workspace") + 1])
        assert (workspace / "answer.xlsx").read_bytes() == b"original submission"
        from financial_audit_bench.benchmark.grading.recalculation_cache import RecalculationCache
        cache = RecalculationCache(Path(command[command.index("--recalculation-cache") + 1]))
        assert cache.resolve(workspace / "answer.xlsx").read_bytes() == b"original submission"
        report = Path(command[command.index("--output") + 1])
        assert not (report.parent / "reward.json").exists()
        if incomplete:
            raise local.subprocess.CalledProcessError(1, command)
        report.write_text('{"score":0.5}')
        (report.parent / "reward.json").write_text('{"reward":0.5}')

    monkeypatch.setattr(local.subprocess, "run", grade)
    args = cli.build_parser().parse_args(["regrade", str(source), "--num-workers", "3"])
    if incomplete:
        with pytest.raises(RuntimeError, match="Incomplete Harbor job"):
            args.func(args)
    else:
        args.func(args)
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    experiment, = [p for p in (checkout / "experiments").iterdir() if p.is_dir()]
    record = json.loads((experiment / "run.json").read_text())
    output = checkout / record["jobs"][0]["path"]
    result = JobResult.model_validate_json((output / "result.json").read_text())
    graded = TrialResult.model_validate_json((output / folder.name / "result.json").read_text())
    assert result.id != job.id and graded.id != trial.id
    assert graded.config.source_trial.trial_id == trial.id
    assert graded.agent_result == trial.agent_result
    assert result.stats.cost_usd == .5 and result.stats.n_input_tokens == 100
    assert (output / folder.name / "agent/trajectory.json").read_bytes() == before[Path(folder.name)/"agent/trajectory.json"]
    assert result.stats.n_errored_trials == int(incomplete)
    if incomplete:
        assert graded.verifier_result is None
        incomplete = False
        local.grade_job(output, catalog, 1)
        recovered = TrialResult.model_validate_json((output / folder.name / "result.json").read_text())
        assert recovered.exception_info is None
        assert recovered.verifier_result.rewards == {"reward": .5}
        cli.require_completed_job(output)
    else:
        assert graded.verifier_result.rewards == {"reward": .5}
        assert next(iter(result.stats.evals.values())).metrics == [{"mean": .5}]


@pytest.mark.parametrize("review", [False, True])
def test_released_tasks_build_from_external_cache_without_mutating_it(tmp_path, benchmark_dataset, review):
    name = "manufacturing_v1_01_cash" + ("_review" if review else "")
    group = "review_tasks" if review else "tasks"
    task = next(t for t in load_tasks(benchmark_dataset / group) if t.short_name == name)
    source = task.paths.task_dir
    workbook = source / "environment/workspace/cash_workpaper.xlsx"
    before = workbook.read_bytes()
    assert not (tmp_path / "benchmark_tasks").exists()
    built = prepare([task], tmp_path, dataset_root=benchmark_dataset)
    target = built / name
    assert (target / "environment/workspace/cash_workpaper.xlsx").read_bytes() == before
    template = source / "tests/template.xlsx" if review else workbook
    assert (target / "tests/template.xlsx").read_bytes() == template.read_bytes()
    for directory in ("pbc_package", "planning", "direct_to_auditor"):
        assert any((target / "environment/workspace" / directory).rglob("*"))
    assert not (target / "solution").exists()
    assert not (target / "environment/workspace/tests").exists()
    hashes = json.loads((built / MARKER).read_text())["source_sha256"]
    assert hashes[f"{group}/{name}/environment/workspace/cash_workpaper.xlsx"] == hashlib.sha256(before).hexdigest()
    # Agent writes must affect only the prepared copy, never the cached source.
    (target / "environment/workspace/cash_workpaper.xlsx").write_bytes(b"agent edit")
    assert workbook.read_bytes() == before
    assert not (tmp_path / "benchmark_tasks").exists()
