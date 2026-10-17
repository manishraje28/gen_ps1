"""Pipeline builders. WARRANT is a drop-in AgentDojo pipeline: it swaps the
ToolsExecutor for the WarrantGate and adds a ContractCompiler after the query.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentdojo.agent_pipeline import AgentPipeline, BasePipelineElement, InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str
from agentdojo.functions_runtime import EmptyEnv, FunctionsRuntime
from agentdojo.types import text_content_block_from_string

from .contract import ContractCompiler
from .events import NULL_EMITTER, Emitter
from .gate import AnswerGuard, EmittingToolsExecutor, WarrantGate


class PlannerHandle(BasePipelineElement):
    """Puts the planner into extra_args["warrant_planner"] so the gate's perturbation probe
    (Lane D, warrant/perturb.py) can re-ask the same planner on relabeled data. Passes everything else through."""

    def __init__(self, llm: BasePipelineElement) -> None:
        self.llm = llm

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        return query, runtime, env, messages, {**extra_args, "warrant_planner": self.llm}


DEFAULT_MAX_STEPS = 24
COMPACT_KEEP = 6        # newest tool outputs the planner always sees in full
COMPACT_CHARS = 1500    # older tool outputs are cut to this many characters
FINAL_PROMPT = ("[system] The step budget for this request is used up ({why}). Do not call any more tools. "
                "Reply to the user: say which parts of the request were completed and which were not, and why.")


def _text(msg) -> str:
    return "".join(b.get("content") or "" for b in (msg.get("content") or []) if isinstance(b, dict))


def compact_history(messages, keep: int = COMPACT_KEEP, limit: int = COMPACT_CHARS) -> list:
    """Cut old, long tool outputs so a long task stays inside the planner's token budget. Only what the
    planner is shown shrinks; the Value Ledger already holds every span, so warrants still trace."""
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    old = set(tool_idx[:-keep]) if keep else set(tool_idx)
    out = []
    for i, m in enumerate(messages):
        t = _text(m) if i in old else ""
        if len(t) > limit:
            m = {**m, "content": [text_content_block_from_string(t[:limit] + f"\n[... {len(t) - limit} more characters trimmed from an earlier step]")]}
        out.append(m)
    return out


class WarrantLoop(BasePipelineElement):
    """ToolsExecutionLoop for long, multi-call tasks: a step budget, progress events, history
    compaction, a stop when the gate halts a stuck planner, and a final no-tools answer instead of
    ending silently when the budget runs out."""

    def __init__(self, elements: list[BasePipelineElement], llm: BasePipelineElement, max_steps: int = DEFAULT_MAX_STEPS,
                 emit: Emitter = NULL_EMITTER, compact: bool = True, should_stop: Callable[[], bool] | None = None) -> None:
        self.elements, self.llm, self.max_steps, self.emit, self.compact = elements, llm, max_steps, emit, compact
        self.should_stop = should_stop or (lambda: False)  # the user pressed Stop: checked before every element
        self.name = "ToolsExecutionLoop"

    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        calls = 0
        for step in range(1, self.max_steps + 1):
            last = messages[-1] if messages else None
            if not last or last["role"] != "assistant" or not last.get("tool_calls"):
                return query, runtime, env, messages, extra_args
            calls += len(last["tool_calls"])
            self.emit("step", step=step, max_steps=self.max_steps, calls=calls, tools=[c.function for c in last["tool_calls"]])
            for el in self.elements:
                if self.should_stop():
                    return self._stopped(query, runtime, env, messages, extra_args)
                if el is self.llm and self.compact:
                    messages = compact_history(messages)
                query, runtime, env, messages, extra_args = el.query(query, runtime, env, messages, extra_args)
                state = extra_args.get("warrant")
                if el is not self.llm and state is not None and getattr(state, "halted", ""):
                    return self._finish(query, runtime, env, messages, extra_args, state.halted)
        last = messages[-1] if messages else None
        if last and last["role"] == "assistant" and last.get("tool_calls"):
            return self._finish(query, runtime, env, messages, extra_args, f"{self.max_steps} steps")
        return query, runtime, env, messages, extra_args

    def _stopped(self, query, runtime, env, messages, extra_args):
        """Stop requested by the user: run nothing more (pending calls are dropped, not executed)."""
        self.emit("stopped", by="user")
        state = extra_args.get("warrant")
        if state is not None:
            from .gate import append_audit
            from . import config
            append_audit(config.AUDIT_DIR, state, {"type": "stopped", "by": "user"}, self.emit)
        msgs = list(messages)
        if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls"):
            msgs[-1] = {**msgs[-1], "tool_calls": None}
        msgs.append({"role": "assistant", "content": [text_content_block_from_string("Stopped: you stopped this task. Nothing further was done.")], "tool_calls": None})
        return query, runtime, env, msgs, extra_args

    def _finish(self, query, runtime, env, messages, extra_args, why: str):
        self.emit("budget_exhausted", why=why)
        state = extra_args.get("warrant")
        if state is not None:
            from .gate import append_audit
            from . import config
            append_audit(config.AUDIT_DIR, state, {"type": "budget_exhausted", "why": why}, self.emit)
        msgs = list(messages)
        if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls"):
            # unanswered tool calls would make the next planner call invalid: drop them
            msgs[-1] = {**msgs[-1], "tool_calls": None}
        msgs.append({"role": "user", "content": [text_content_block_from_string(FINAL_PROMPT.format(why=why))]})
        try:
            _, _, env, msgs, extra_args = self.llm.query(query, FunctionsRuntime([]), env, compact_history(msgs) if self.compact else msgs, extra_args)
        except Exception as exc:  # never lose the run over the summary
            self.emit("error", where="final_summary", error=str(exc))
            return query, runtime, env, messages, extra_args
        if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls"):
            msgs[-1] = {**msgs[-1], "tool_calls": None}
        return query, runtime, env, msgs, extra_args


def build_undefended(llm: BasePipelineElement, system_message: str, emit: Emitter = NULL_EMITTER,
                     formatter: Callable[[Any], str] = tool_result_to_str, max_iters: int = DEFAULT_MAX_STEPS) -> AgentPipeline:
    pipe = AgentPipeline([
        SystemMessage(system_message),
        InitQuery(),
        llm,
        WarrantLoop([EmittingToolsExecutor(emit, formatter), llm], llm, max_steps=max_iters, emit=emit),
    ])
    pipe.name = f"{getattr(llm, 'name', 'llm')}-undefended"
    return pipe


def build_warrant(llm: BasePipelineElement, system_message: str, emit: Emitter = NULL_EMITTER, mask_directives: bool = True,
                  contract_llm: bool = True, auditor_llm: bool | None = None, internal_domains: list[str] | None = None,
                  formatter: Callable[[Any], str] = tool_result_to_str, max_iters: int = DEFAULT_MAX_STEPS) -> AgentPipeline:
    pipe = AgentPipeline([
        SystemMessage(system_message),
        InitQuery(),
        ContractCompiler(use_llm=contract_llm, emit=emit, internal_domains=internal_domains),
        PlannerHandle(llm),
        llm,
        WarrantLoop([WarrantGate(emit=emit, mask_directives=mask_directives, auditor_llm=auditor_llm, output_formatter=formatter), llm], llm, max_steps=max_iters, emit=emit),
        AnswerGuard(emit=emit),
    ])
    pipe.name = f"{getattr(llm, 'name', 'llm')}-warrant"
    return pipe
