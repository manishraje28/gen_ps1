"""Lane F: the held-out harness itself must be valid: against the undefended agent every attack lands
and every benign task completes. (Otherwise a "blocked" result for WARRANT would mean nothing.)"""
import pytest

from bench.heldout import CASES, run_case


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case_is_valid_against_undefended_agent(case):
    r = run_case(case, "none", "scripted")
    assert "error" not in r, r
    if case.benign:
        assert r["utility"], f"{case.id}: benign task not completed by the undefended agent"
    else:
        assert r["attack_succeeded"], f"{case.id}: attack does not land even without a defence"


def test_case_ids_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
