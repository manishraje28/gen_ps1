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
_local_client: openai.OpenAI | None = None
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "local_calls": 0, "failovers": 0}
# Groq models that are out of budget (daily limit) or unreachable: model -> unix time until we stop trying them.
# While a model is here, calls skip Groq at once instead of waiting, so nothing stalls when credit runs out.
_GROQ_DOWN_UNTIL: dict[str, float] = {}
LAST_PROVIDER: dict[str, str] = {}  # model -> "groq" | "local:<model>", for the UI


def get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set. Put it in .env (see .env.example).")
        # A dropped connection must fail fast and be retried below, not hang for the SDK default of 10 minutes.
        _client = openai.OpenAI(api_key=config.GROQ_API_KEY, base_url=config.GROQ_BASE_URL, max_retries=0, timeout=60.0)
    return _client


def get_local_client() -> openai.OpenAI:
    global _local_client
    if _local_client is None:
        _local_client = openai.OpenAI(api_key="local", base_url=config.LOCAL_BASE_URL, max_retries=0, timeout=180.0)
    return _local_client


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


def _rate_limit_wait(err: Exception) -> float | None:
    """Seconds Groq asks us to wait, from the retry-after header or the message ('try again in 4m33.4s')."""
    try:
        headers = getattr(getattr(err, "response", None), "headers", {}) or {}
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except Exception:
        pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)(ms|s)", str(err))
    if not m:
        return None
    h, mins, val, unit = m.groups()
    secs = float(val) / (1000 if unit == "ms" else 1)
    return secs + 60 * int(mins or 0) + 3600 * int(h or 0)


def _is_daily_limit(err: Exception) -> bool:
    s = str(err)
    return "per day" in s or "(TPD)" in s or "(RPD)" in s


def _sleep_for_rate_limit(err: Exception, attempt: int) -> None:
    wait = _rate_limit_wait(err)
    time.sleep(min(60.0, (wait or 2.0 * (attempt + 1)) + 0.5))


def _local_completion(kwargs: dict[str, Any]):
    local = dict(kwargs)
    local["model"] = config.LOCAL_PLANNER_MODEL
    local.pop("response_format", None)
    resp = get_local_client().chat.completions.create(**local)
    USAGE["local_calls"] += 1
    if resp.usage:
        USAGE["prompt_tokens"] += resp.usage.prompt_tokens or 0
        USAGE["completion_tokens"] += resp.usage.completion_tokens or 0
    return resp.choices[0].message


def chat_completion(model: str, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.0,
                    json_mode: bool = False, max_tokens: int = 1024, allow_local: bool = False):
    """Calls Groq with retries. Returns (message, raw_failed_generation_or_None).

    With allow_local=True (the planner), the call never stalls on credit: if Groq hits its daily limit, asks us to
    wait longer than config.LLM_MAX_WAIT, is unreachable, or the planner provider is "local", the same request goes
    to the local model (Ollama). Without it (contract compiler, auditor), it fails fast so their callers fall back
    to WARRANT's rules.
    """
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    can_local = allow_local and (config.LOCAL_FALLBACK or config.PLANNER_PROVIDER == "local")
    use_groq = config.has_groq_key() and not (allow_local and config.PLANNER_PROVIDER == "local") \
        and _GROQ_DOWN_UNTIL.get(model, 0) <= time.time()
    last_err: Exception | None = None
    if use_groq:
        client = get_client()
        for attempt in range(7):
            try:
                resp = client.chat.completions.create(**kwargs)
                USAGE["calls"] += 1
                if resp.usage:
                    USAGE["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    USAGE["completion_tokens"] += resp.usage.completion_tokens or 0
                LAST_PROVIDER[model] = "groq"
                return resp.choices[0].message, None
            except openai.RateLimitError as e:
                last_err = e
                wait = _rate_limit_wait(e)
                if _is_daily_limit(e) or (wait is not None and wait > config.LLM_MAX_WAIT):
                    # Out of budget for a while: remember it so the next calls skip Groq without waiting.
                    _GROQ_DOWN_UNTIL[model] = time.time() + max(wait or 0.0, 900.0 if _is_daily_limit(e) else 60.0)
                    break
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
                if attempt >= 1:
                    _GROQ_DOWN_UNTIL[model] = time.time() + 60.0
                    break
                time.sleep(2 * (attempt + 1))
    elif not config.has_groq_key():
        last_err = RuntimeError("GROQ_API_KEY is not set")
    else:
        last_err = RuntimeError(f"Groq model {model} is out of budget until "
                                f"{time.strftime('%H:%M', time.localtime(_GROQ_DOWN_UNTIL.get(model, 0)))}")
    if can_local:
        try:
            msg = _local_completion(kwargs)
            USAGE["failovers"] += 1 if use_groq or config.has_groq_key() else 0
            LAST_PROVIDER[model] = f"local:{config.LOCAL_PLANNER_MODEL}"
            return msg, None
        except Exception as e:  # local model down too: report both
            raise RuntimeError(f"Groq unavailable ({last_err}) and local model failed ({e})") from e
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
        msg, failed = chat_completion(self.model, oa_messages, tools_to_openai(runtime), self.temperature, max_tokens=2048, allow_local=True)  # gpt-oss reasoning counts toward this
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
            provider=LAST_PROVIDER.get(self.model, "groq"),
            text=text,
            tool_calls=[{"id": c.id, "function": c.function, "args": dict(c.args)} for c in calls],
        )
        self.emit("stage", stage="planner", status="done")
        return query, runtime, env, [*messages, output], extra_args
