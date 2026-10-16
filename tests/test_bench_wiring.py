"""Lane F: pipeline wiring (AnswerGuard last, planner handle for the perturbation probe, --perturb flag)."""
from agentdojo.agent_pipeline import AgentPipeline

from bench.run_agentdojo import build
from warrant.gate import AnswerGuard
from warrant.pipeline import PlannerHandle, build_warrant


def _names(pipe: AgentPipeline) -> list[str]:
    return [type(e).__name__ for e in pipe.elements]


def test_build_warrant_ends_with_answer_guard_and_exposes_planner():
    pipe = build_warrant(llm=PlannerHandle(None), system_message="x", contract_llm=False)
    names = _names(pipe)
    assert names[-1] == "AnswerGuard"
    assert "PlannerHandle" in names
    assert names.index("PlannerHandle") < names.index("ToolsExecutionLoop")


def test_bench_warrant_pipelines_have_answer_guard():
    for defense in ("warrant", "warrant_nomask"):
        pipe, llm = build(defense, "oracle", "x", contract_llm=False)
        assert isinstance(pipe.elements[-1], AnswerGuard)
        handle = next(e for e in pipe.elements if isinstance(e, PlannerHandle))
        assert handle.llm is llm


def test_planner_handle_passes_planner_in_extra_args():
    sentinel = object()
    out = PlannerHandle(sentinel).query("q", None, messages=[], extra_args={"a": 1})
    assert out[4] == {"a": 1, "warrant_planner": sentinel}
