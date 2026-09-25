"""The benchmark's bash-only agent, executed and supervised by Harbor."""
from __future__ import annotations

import asyncio
import json
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories.agent import Agent
from harbor.models.trajectories.observation import Observation
from harbor.models.trajectories.observation_result import ObservationResult
from harbor.models.trajectories.step import Step
from harbor.models.trajectories.tool_call import ToolCall
from harbor.models.trajectories.trajectory import Trajectory

from financial_audit_bench.defaults import DEFAULT_MAX_MODEL_CALLS, DEFAULT_MODEL
from financial_audit_bench.llms import acomplete, routed_litellm_model

COMPLETION_SIGNAL = "<<TASK_FINISHED>>"
COMMAND_TIMEOUT_SECONDS = 600
BASH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash", "description": "Execute a bash command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The bash command to execute"}},
            "required": ["command"],
        },
    },
}


def load_prompt(name: str) -> str:
    text = Path(__file__).with_name("prompts.txt").read_text().split(f"=== {name} ===\n", 1)[1]
    return text.split("\n=== ", 1)[0].strip().format(completion_signal=COMPLETION_SIGNAL)


def truncate_output(text: str | None) -> str:
    if text is None or len(text) <= 20_000:
        return text or ""
    return text[:20_000] + "\n[TRUNCATED: output exceeded 20000 characters]"


async def execute_bash(environment, command: str) -> dict:
    started = time.perf_counter()
    command = f"timeout --kill-after=5s {COMMAND_TIMEOUT_SECONDS}s bash -lc {shlex.quote(command)}"
    try:
        executed = await environment.exec(command, cwd="/workspace", timeout_sec=COMMAND_TIMEOUT_SECONDS + 10)
        result = {"exit_code": executed.return_code,
                  "stdout": truncate_output(executed.stdout),
                  "stderr": truncate_output(executed.stderr)}
    except TimeoutError:
        result = {"exit_code": 124, "stdout": "",
                  "stderr": f"command timed out after {COMMAND_TIMEOUT_SECONDS} seconds"}
    result["duration_seconds"] = time.perf_counter() - started
    return result


class AuditAgent(BaseAgent):
    SUPPORTS_ATIF = True

    def __init__(self, logs_dir: Path, model_name: str | None = None, *args,
                 max_model_calls=DEFAULT_MAX_MODEL_CALLS, api_base=None,
                 model_kwargs=None, **kwargs):
        load_dotenv(Path.cwd() / ".env")
        super().__init__(logs_dir, model_name or DEFAULT_MODEL, *args, **kwargs)
        self.max_model_calls = max_model_calls
        self.api_base = api_base
        self.model_kwargs = model_kwargs or {}
        self.model = routed_litellm_model(self.model_name, api_base)

    @staticmethod
    def name() -> str:
        return "financial-audit"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment) -> None:
        pass

    async def run(self, instruction: str, environment, context: AgentContext) -> None:
        prompt = load_prompt("system")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": instruction}]
        steps = [Step(step_id=1, source="system", message=prompt),
                 Step(step_id=2, source="user", message=instruction)]
        context.metadata = {"model_calls": 0, "termination_reason": "running"}
        cost_available = True
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        def save():
            trajectory = Trajectory(
                session_id=self.session_id,
                agent=Agent(name=self.name(), version=self.version(), model_name=self.model,
                            tool_definitions=[BASH_TOOL_SCHEMA],
                            extra={"max_model_calls": self.max_model_calls, "model_kwargs": self.model_kwargs}),
                steps=steps, extra=context.metadata,
            )
            # Preserve the last complete trajectory if a write is interrupted.
            temporary = self.logs_dir / "trajectory.tmp"
            temporary.write_text(trajectory.model_dump_json(indent=2, exclude_none=True))
            temporary.replace(self.logs_dir / "trajectory.json")

        save()
        try:
            for call_number in range(1, self.max_model_calls + 1):
                context.metadata["model_calls"] = call_number
                response = await acomplete(model=self.model, messages=messages,
                                           tools=[BASH_TOOL_SCHEMA], api_base=self.api_base,
                                           **self.model_kwargs)
                usage = response.usage or {}
                inputs = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                outputs = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                cached = (usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                context.n_input_tokens = (context.n_input_tokens or 0) + inputs
                context.n_output_tokens = (context.n_output_tokens or 0) + outputs
                context.n_cache_tokens = (context.n_cache_tokens or 0) + cached
                # One unpriced call makes the total unknown; keep per-call costs below.
                cost_available = cost_available and response.cost is not None
                context.cost_usd = (context.cost_usd or 0) + response.cost if cost_available else None
                step = Step(step_id=len(steps) + 1, source="agent", message=response.content,
                            timestamp=datetime.now(UTC).isoformat(), model_name=response.model,
                            metrics={"prompt_tokens": inputs, "completion_tokens": outputs,
                                     "cached_tokens": cached, "cost_usd": response.cost,
                                     "extra": {"usage": usage, "latency_seconds": response.latency_seconds}})
                steps.append(step)
                messages.append(response.to_assistant_message())
                if response.tool_calls:
                    step.tool_calls = []
                    step.observation = Observation(results=[])
                for tool_call in response.tool_calls:
                    function = tool_call["function"]
                    arguments = function.get("arguments", {})
                    invalid = None
                    try:
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        if (function["name"] != "bash" or not isinstance(arguments, dict)
                                or set(arguments) != {"command"} or not isinstance(arguments["command"], str)):
                            raise ValueError("Expected bash with a string command")
                    except (ValueError, TypeError) as error:
                        invalid = {"error": "invalid_tool_arguments", "message": str(error)}
                    step.tool_calls.append(ToolCall(
                        tool_call_id=tool_call["id"], function_name=function["name"],
                        arguments=arguments if isinstance(arguments, dict) else {"raw": arguments},
                    ))
                    # Persist the requested command before awaiting it, including on timeout.
                    save()
                    result = invalid or await execute_bash(environment, arguments["command"])
                    content = json.dumps(result, default=str)
                    step.observation.results.append(ObservationResult(
                        source_call_id=tool_call["id"], content=content,
                    ))
                    messages.append({"role": "tool", "tool_call_id": tool_call["id"], "name": function["name"], "content": content})
                    save()
                if not response.tool_calls:
                    if response.content.strip().splitlines()[-1:] == [COMPLETION_SIGNAL]:
                        context.metadata["termination_reason"] = "completed"
                        break
                    reminder = load_prompt("reminder")
                    messages.append({"role": "user", "content": reminder})
                    steps.append(Step(step_id=len(steps) + 1, source="user", message=reminder))
                save()
            else:
                context.metadata["termination_reason"] = "max_model_calls"
        except asyncio.CancelledError:
            context.metadata["termination_reason"] = "cancelled"
            raise
        except Exception:
            context.metadata["termination_reason"] = "error"
            raise
        finally:
            save()
