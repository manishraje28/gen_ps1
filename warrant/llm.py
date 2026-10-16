"""Groq (OpenAI-compatible) LLM as an AgentDojo pipeline element, plus a JSON helper.

Written as a thin custom element instead of AgentDojo's ``OpenAILLM`` so that we
control message formatting (Groq wants plain-string contents), retries on rate
limits, and the recovery path for Llama's occasional malformed tool calls.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

import openai
from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, text_content_block_from_string

from . import config
from .events import NULL_EMITTER, Emitter

_client: openai.OpenAI | None = None
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set. Put it in .env (see .env.example).")
        # A dropped connection must fail fast and be retried below, not hang for the SDK default of 10 minutes.
        _client = openai.OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL, max_retries=0, timeout=60.0)
    return _client


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            parts.append(str(block.get("content") or block.get("text") or ""))
        else:
            parts.append(str(block))
    return "\n".join(p for p in parts if p)


def messages_to_openai(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role in ("system", "user"):
            out.append({"role": role, "content": content_to_text(m["content"])})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": content_to_text(m.get("content")) or None}
            calls = m.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": c.function, "arguments": json.dumps(dict(c.args))},
                    }
                    for c in calls
                ]
                if msg["content"] is None:
                    msg["content"] = ""
            out.append(msg)
        elif role == "tool":
            text = content_to_text(m.get("content"))
            if m.get("error"):
                text = f"ERROR: {m['error']}" + (f"\n{text}" if text else "")
            call = m.get("tool_call")
            call_id = m.get("tool_call_id") or (call.id if call is not None else None) or "call_x"
            out.append({"role": "tool", "tool_call_id": call_id, "content": text or "(empty result)"})
    # Make sure every tool message id matches the preceding assistant call ids
    return out


def tools_to_openai(runtime: FunctionsRuntime) -> list[dict[str, Any]]:
    tools = []
    for f in runtime.functions.values():
        schema = f.parameters.model_json_schema()
        schema.pop("title", None)
        tools.append({"type": "function", "function": {"name": f.name, "description": f.description, "parameters": schema}})
    return tools


_FAILED_CALL_RE = re.compile(r"<function=([\w\-]+)[^{]*(\{.*?\})\s*(?:</function>|>)", re.S)


def _parse_failed_generation(text: str) -> list[FunctionCall]:
    calls = []
    for name, args in _FAILED_CALL_RE.findall(text or ""):
        try:
            calls.append(FunctionCall(function=name, args=json.loads(args), id=f"call_{uuid.uuid4().hex[:8]}"))
        except json.JSONDecodeError:
            continue
    return calls


def _sleep_for_rate_limit(err: Exception, attempt: int) -> None:
    wait = None
    try:
        headers = getattr(getattr(err, "response", None), "headers", {}) or {}
        if headers.get("retry-after"):
            wait = float(headers["retry-after"])
    except Exception:
        pass
    if wait is None:
        m = re.search(r"try again in ([\d.]+)(ms|s)", str(err))
        if m:
            wait = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
    time.sleep(min(60.0, (wait or 2.0 * (attempt + 1)) + 0.5))


def chat_completion(model: str, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.0, json_mode: bool = False, max_tokens: int = 1024):
    """Calls Groq with retries. Returns (message, raw_failed_generation_or_None)."""
    client = get_client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_err: Exception | None = None
    for attempt in range(7):
        try:
            resp = client.chat.completions.create(**kwargs)
            USAGE["calls"] += 1
            if resp.usage:
                USAGE["prompt_tokens"] += resp.usage.prompt_tokens or 0
                USAGE["completion_tokens"] += resp.usage.completion_tokens or 0
            return resp.choices[0].message, None
        except openai.RateLimitError as e:
            last_err = e
            _sleep_for_rate_limit(e, attempt)
        except openai.BadRequestError as e:
            body = getattr(e, "body", None) or {}
            err = body.get("error", body) if isinstance(body, dict) else {}
            if isinstance(err, dict) and err.get("code") == "tool_use_failed":
                failed = err.get("failed_generation", "")
                if attempt < 1:
                    kwargs["temperature"] = 0.2
                    continue
                return None, failed
            raise
        except (openai.APIConnectionError, openai.InternalServerError, openai.APITimeoutError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Groq request failed after retries: {last_err}")


def chat_json(system: str, user: str, model: str | None = None, max_tokens: int = 1200) -> dict[str, Any]:
    msg, _ = chat_completion(
        model or config.CONTRACT_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_mode=True,
        max_tokens=max_tokens,
    )
    text = (msg.content if msg else "") or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


class GroqLLM(BasePipelineElement):
    """Planner LLM element. Emits ``llm`` events so the UI can show each step."""

    def __init__(self, model: str | None = None, temperature: float = 0.0, emit: Emitter = NULL_EMITTER) -> None:
        self.model = model or config.PLANNER_MODEL
        self.temperature = temperature
        self.emit = emit
        self.name = f"groq-{self.model}"

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        self.emit("stage", stage="planner", status="active")
        oa_messages = messages_to_openai(messages)
        msg, failed = chat_completion(self.model, oa_messages, tools_to_openai(runtime), self.temperature, max_tokens=2048)  # gpt-oss reasoning counts toward this
        calls: list[FunctionCall] = []
        text = ""
        if msg is not None:
            text = msg.content or ""
            for tc in msg.tool_calls or []:
                try:
                    args = json.loads(tc.function.arguments or "{}") or {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(FunctionCall(function=tc.function.name, args=args, id=tc.id))
        else:
            calls = _parse_failed_generation(failed or "")
            if not calls:
                text = "I could not produce a valid tool call."
        output = ChatAssistantMessage(
            role="assistant",
            content=[text_content_block_from_string(text)] if text else None,
            tool_calls=calls or None,
        )
        self.emit(
            "llm",
            text=text,
            tool_calls=[{"id": c.id, "function": c.function, "args": dict(c.args)} for c in calls],
        )
        self.emit("stage", stage="planner", status="done")
        return query, runtime, env, [*messages, output], extra_args
