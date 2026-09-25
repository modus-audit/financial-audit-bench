import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from litellm import acompletion, completion, completion_cost
from openai import AsyncOpenAI, OpenAI

MODEL_REQUEST_TIMEOUT_SECONDS = 600
MODEL_REQUEST_MAX_RETRIES = 2


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    usage: dict[str, Any] | None
    cost: float | None
    tool_calls: list[dict[str, Any]]
    reasoning_details: list[dict[str, Any]]
    raw: dict[str, Any]
    latency_seconds: float

    def to_assistant_message(self) -> dict[str, Any]:
        """Return replayable history, including opaque provider reasoning state.

        Visible text and tool calls alone are not a complete assistant turn.
        Keep signed/encrypted blocks unchanged; never reconstruct them from the
        reasoning summary. Copy it so history and the response do not share state.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if "output" in self.raw:
            # Responses items include encrypted reasoning and function-call IDs.
            message["response_output"] = self.raw["output"]
        else:
            choices = self.raw.get("choices") or []
            raw_message = choices[0]["message"] if choices else {}
            provider_fields = raw_message.get("provider_specific_fields") or {}
            if "content" in raw_message:
                message["content"] = raw_message["content"]
            for key in ("thinking_blocks", "reasoning_details", "reasoning_content"):
                value = raw_message.get(key)
                if value is None:
                    value = provider_fields.get(key)
                if value is not None:
                    message[key] = value
            if provider_fields:
                message["provider_specific_fields"] = provider_fields
            if "reasoning_details" not in message and self.reasoning_details:
                message["reasoning_details"] = self.reasoning_details
        return deepcopy(message)


def routed_litellm_model(model: str, api_base: str | None = None) -> str:
    if api_base is not None:
        return model if "/" in model else f"openai/{model}"
    if model.startswith(("openrouter/", "openai/", "gpt-", "claude-")):
        return model
    return f"openrouter/{model}"


def complete(*, model: str, messages: list[dict[str, Any]],
             api_base: str | None = None, **kwargs: Any) -> LLMResponse:
    """Synchronous model call for grading; options are shared with acomplete."""
    request = _request(model=model, messages=messages, api_base=api_base, **kwargs)
    start = time.perf_counter()
    if model.startswith("gpt-") and api_base is None:
        with OpenAI(max_retries=MODEL_REQUEST_MAX_RETRIES) as client:
            response = client.responses.create(**request)
        return _openai_response(response, model, time.perf_counter() - start)
    response = completion(**request)
    return _litellm_response(response, model, time.perf_counter() - start)


async def acomplete(*, model: str, messages: list[dict[str, Any]],
                    api_base: str | None = None, **kwargs: Any) -> LLMResponse:
    """Cancellable asynchronous model call for agent trials."""
    request = _request(model=model, messages=messages, api_base=api_base, **kwargs)
    start = time.perf_counter()
    if model.startswith("gpt-") and api_base is None:
        async with AsyncOpenAI(max_retries=MODEL_REQUEST_MAX_RETRIES) as client:
            response = await client.responses.create(**request)
        return _openai_response(response, model, time.perf_counter() - start)
    response = await acompletion(**request)
    return _litellm_response(response, model, time.perf_counter() - start)


def _request(
    *, model: str, messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float | None = None, api_base: str | None = None,
    request_timeout_seconds: float = MODEL_REQUEST_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> dict[str, Any]:
    kwargs.pop("timeout", None)
    kwargs.pop("num_retries", None)
    if model.startswith("gpt-") and api_base is None:
        return _openai_request(model=model, messages=messages, tools=tools,
                               tool_choice=tool_choice, temperature=temperature,
                               request_timeout_seconds=request_timeout_seconds, **kwargs)

    # Direct Claude requests opt into automatic prompt caching.
    if model.startswith(("claude-", "anthropic/")):
        kwargs.setdefault("cache_control", {"type": "ephemeral"})
    if "deepseek" in model.lower():
        allowed = list(kwargs.get("allowed_openai_params") or [])
        if "reasoning_effort" not in allowed:
            allowed.append("reasoning_effort")
        kwargs["allowed_openai_params"] = allowed
    request = {
        "model": model, "messages": messages, "tools": tools,
        "tool_choice": tool_choice, **kwargs,
        "num_retries": MODEL_REQUEST_MAX_RETRIES, "timeout": request_timeout_seconds,
    }
    if api_base is not None:
        request["api_base"] = api_base
    if temperature is not None:
        request["temperature"] = temperature
    return request


def _litellm_response(response, model: str, latency_seconds: float) -> LLMResponse:
    message = response.choices[0].message
    raw = response.to_dict()

    try:
        cost = completion_cost(completion_response=response)
    except Exception:
        cost = None

    usage = raw.get("usage")
    raw_tool_calls = message.tool_calls or []
    tool_calls = [
        tool_call.to_dict() if hasattr(tool_call, "to_dict") else dict(tool_call)
        for tool_call in raw_tool_calls
    ]
    raw_message = raw["choices"][0]["message"]
    reasoning_details = raw_message.get("reasoning_details") or (
        raw_message.get("provider_specific_fields") or {}
    ).get("reasoning_details")

    return LLMResponse(
        content=message.content or "",
        model=raw.get("model") or model,
        usage=usage,
        cost=cost,
        tool_calls=tool_calls,
        reasoning_details=reasoning_details or [],
        raw=raw,
        latency_seconds=latency_seconds,
    )


def _openai_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    temperature: float | None,
    request_timeout_seconds: float = MODEL_REQUEST_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> dict[str, Any]:
    instructions = "\n\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") in {"system", "developer"}
    )
    input_messages = _openai_input(messages)
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_messages,
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "tools": _openai_tools(tools or []),
        **kwargs,
        "timeout": request_timeout_seconds,
    }
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    if temperature is not None:
        request["temperature"] = temperature
    if "reasoning_effort" in request:
        request["reasoning"] = {"effort": request.pop("reasoning_effort")}
    if "response_format" in request:
        response_format = request.pop("response_format")
        if response_format.get("type") != "json_schema":
            raise ValueError("Responses structured output requires json_schema")
        request["text"] = {"format": {"type": "json_schema", **response_format["json_schema"]}}

    return request


def _openai_response(response, model: str, latency_seconds: float) -> LLMResponse:
    raw = response.model_dump()
    tool_calls = [
        {
            "id": item.call_id,
            "type": "function",
            "function": {"name": item.name, "arguments": item.arguments},
        }
        for item in response.output
        if item.type == "function_call"
    ]

    try:
        cost = completion_cost(
            completion_response=raw,
            model=model,
            call_type="responses",
        )
    except Exception:
        cost = None

    return LLMResponse(
        content=response.output_text or "",
        model=response.model,
        usage=raw.get("usage"),
        cost=cost,
        tool_calls=tool_calls,
        reasoning_details=[],
        raw=raw,
        latency_seconds=latency_seconds,
    )


def _openai_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response_input = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            response_input.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": str(message.get("content") or ""),
                }
            )
        elif role == "assistant" and message.get("response_output"):
            response_input.extend(
                {key: value for key, value in item.items() if key != "status"}
                for item in message["response_output"]
            )
        elif role in {"user", "assistant"}:
            response_input.append(
                {"role": role, "content": str(message.get("content") or "")}
            )
    return response_input


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response_tools = []
    for tool in tools:
        function = tool["function"]
        response_tool = {
            "type": "function",
            "name": function["name"],
            "description": function.get("description", ""),
            "parameters": function["parameters"],
        }
        if "strict" in function:
            response_tool["strict"] = function["strict"]
        response_tools.append(response_tool)
    return response_tools
