"""Lane F: honest benchmark metrics (executed calls, attackable denominator, asked-user outcome)."""
from agentdojo.functions_runtime import FunctionCall
from agentdojo.task_suite.load_suites import get_suite

from bench.run_agentdojo import asked_user, empty_ground_truth_injections, executed_trace, rate


def _tool(name: str, error: str | None):
    return {"role": "tool", "content": [], "tool_call_id": name, "tool_call": FunctionCall(function=name, args={}), "error": error}


def test_executed_trace_drops_refused_held_and_failed_calls():
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": []},
        _tool("read_email", None),
        _tool("send_email", "WARRANT_REFUSED (TAINTED_VALUE): this call was blocked."),
        _tool("send_money", "WARRANT_NEEDS_INPUT (NEW_PAYEE): this call needs the user's confirmation."),
        _tool("delete_file", "ValueError: no such file"),
        _tool("share_file", None),
    ]
    assert [c.function for c in executed_trace(msgs)] == ["read_email", "share_file"]
    assert asked_user(msgs) is True
    assert asked_user(msgs[:3]) is False


def test_empty_ground_truth_injections_match_verified_list():
    # Verified on agentdojo==0.1.35 / v1.2.1: these can never succeed in the default environment.
    assert empty_ground_truth_injections(get_suite("v1.2.1", "travel")) == ["injection_task_6"]
    ws = empty_ground_truth_injections(get_suite("v1.2.1", "workspace"))
    assert ws == [f"injection_task_{i}" for i in range(6, 14)]
    assert empty_ground_truth_injections(get_suite("v1.2.1", "banking")) == []


def test_rate_handles_empty_and_missing_keys():
    assert rate([], "attack") == 0
    assert rate([{"attack": True}, {"attack": False}, {}], "attack") == round(1 / 3, 4)
