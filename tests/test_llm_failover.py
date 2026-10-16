"""The planner must never stall on credit: Groq daily limits / long waits / outages fail over to the local model,
and the contract compiler / auditor fail fast so they fall back to WARRANT's rules. Offline (fake clients)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warrant import config, llm  # noqa: E402


def _rate_limit(msg):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return openai.RateLimitError(msg, response=httpx.Response(429, request=req), body=None)


DAILY = ("Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): Limit 200000, Used 199041, "
         "Requested 1592. Please try again in 4m33.455s.")
MINUTE_SHORT = "Rate limit reached on tokens per minute (TPM): Limit 8000. Please try again in 480ms."
MINUTE_LONG = "Rate limit reached on tokens per minute (TPM): Limit 8000. Please try again in 45s."


class FakeClient:
    def __init__(self, errors=(), label="groq"):
        self.errors = list(errors)
        self.calls = 0
        self.label = label
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        msg = SimpleNamespace(content=f"answer from {self.label}:{kw['model']}", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


@pytest.fixture
def clients(monkeypatch):
    llm._GROQ_DOWN_UNTIL.clear()
    monkeypatch.setattr(config, "has_groq_key", lambda: True)
    monkeypatch.setattr(config, "LOCAL_FALLBACK", True)
    monkeypatch.setattr(config, "PLANNER_PROVIDER", "groq")
    monkeypatch.setattr(config, "LLM_MAX_WAIT", 8.0)
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))

    def install(groq_errors=(), local_errors=()):
        g, l = FakeClient(groq_errors, "groq"), FakeClient(local_errors, "local")
        monkeypatch.setattr(llm, "get_client", lambda: g)
        monkeypatch.setattr(llm, "get_local_client", lambda: l)
        return g, l, sleeps

    yield install
    llm._GROQ_DOWN_UNTIL.clear()


MSGS = [{"role": "user", "content": "pay the bill"}]


def test_daily_limit_fails_over_to_local_without_waiting(clients):
    g, l, sleeps = clients(groq_errors=[_rate_limit(DAILY)])
    msg, _ = llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert msg.content.startswith("answer from local:") and l.calls == 1 and g.calls == 1
    assert sleeps == []  # no waiting on an exhausted daily budget
    assert llm.LAST_PROVIDER["openai/gpt-oss-20b"].startswith("local:")


def test_exhausted_model_is_skipped_on_the_next_call(clients):
    g, l, _ = clients(groq_errors=[_rate_limit(DAILY)])
    llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert g.calls == 1 and l.calls == 2  # Groq not retried while marked down
    assert llm._GROQ_DOWN_UNTIL["openai/gpt-oss-20b"] > time.time() + 200


def test_long_per_minute_wait_fails_over_short_wait_retries_groq(clients):
    g, l, sleeps = clients(groq_errors=[_rate_limit(MINUTE_SHORT)])
    msg, _ = llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert msg.content.startswith("answer from groq") and l.calls == 0 and len(sleeps) == 1 and sleeps[0] < 2
    g, l, sleeps = clients(groq_errors=[_rate_limit(MINUTE_LONG)])
    llm._GROQ_DOWN_UNTIL.clear()
    sleeps.clear()
    msg, _ = llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert msg.content.startswith("answer from local") and sleeps == []


def test_connection_errors_fail_over(clients):
    req = httpx.Request("POST", "https://api.groq.com")
    g, l, _ = clients(groq_errors=[openai.APIConnectionError(request=req)] * 2)
    msg, _ = llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert msg.content.startswith("answer from local") and g.calls == 2


def test_contract_and_auditor_calls_fail_fast_without_local(clients):
    g, l, sleeps = clients(groq_errors=[_rate_limit(DAILY)])
    with pytest.raises(RuntimeError):
        llm.chat_json("system", "user")  # callers catch this and use the rules
    assert l.calls == 0 and sleeps == []


def test_local_provider_skips_groq(clients, monkeypatch):
    monkeypatch.setattr(config, "PLANNER_PROVIDER", "local")
    g, l, _ = clients()
    msg, _ = llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)
    assert g.calls == 0 and l.calls == 1 and msg.content.startswith("answer from local")


def test_both_down_reports_both(clients):
    g, l, _ = clients(groq_errors=[_rate_limit(DAILY)], local_errors=[RuntimeError("ollama not running")])
    with pytest.raises(RuntimeError, match="local model failed"):
        llm.chat_completion("openai/gpt-oss-20b", MSGS, allow_local=True)


def test_rate_limit_wait_parsing():
    assert llm._rate_limit_wait(_rate_limit(DAILY)) == pytest.approx(273.455, abs=0.01)
    assert llm._rate_limit_wait(_rate_limit(MINUTE_SHORT)) == pytest.approx(0.48)
    assert llm._is_daily_limit(_rate_limit(DAILY)) and not llm._is_daily_limit(_rate_limit(MINUTE_LONG))
