"""Pipeline builders. WARRANT is a drop-in AgentDojo pipeline: it swaps the
ToolsExecutor for the WarrantGate and adds a ContractCompiler after the query.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentdojo.agent_pipeline import AgentPipeline, BasePipelineElement, InitQuery, SystemMessage, ToolsExecutionLoop
from agentdojo.agent_pipeline.tool_execution import tool_result_to_str

from .contract import ContractCompiler
from .events import NULL_EMITTER, Emitter
from .gate import EmittingToolsExecutor, WarrantGate


def build_undefended(llm: BasePipelineElement, system_message: str, emit: Emitter = NULL_EMITTER,
                     formatter: Callable[[Any], str] = tool_result_to_str, max_iters: int = 12) -> AgentPipeline:
    pipe = AgentPipeline([
        SystemMessage(system_message),
        InitQuery(),
        llm,
        ToolsExecutionLoop([EmittingToolsExecutor(emit, formatter), llm], max_iters=max_iters),
    ])
    pipe.name = f"{getattr(llm, 'name', 'llm')}-undefended"
    return pipe


def build_warrant(llm: BasePipelineElement, system_message: str, emit: Emitter = NULL_EMITTER, mask_directives: bool = True,
                  contract_llm: bool = True, auditor_llm: bool | None = None, internal_domains: list[str] | None = None,
                  formatter: Callable[[Any], str] = tool_result_to_str, max_iters: int = 12) -> AgentPipeline:
    pipe = AgentPipeline([
        SystemMessage(system_message),
        InitQuery(),
        ContractCompiler(use_llm=contract_llm, emit=emit, internal_domains=internal_domains),
        llm,
        ToolsExecutionLoop([WarrantGate(emit=emit, mask_directives=mask_directives, auditor_llm=auditor_llm, output_formatter=formatter), llm], max_iters=max_iters),
    ])
    pipe.name = f"{getattr(llm, 'name', 'llm')}-warrant"
    return pipe
