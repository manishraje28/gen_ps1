"""The LLM key check accepts other OpenAI-compatible providers (e.g. Gemini), not only Groq."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warrant import config  # noqa: E402


def check(monkeypatch, key, url):
    monkeypatch.setattr(config, "GROQ_API_KEY", key)
    monkeypatch.setattr(config, "GROQ_BASE_URL", url)
    return config.has_groq_key()


def test_groq_needs_a_groq_key(monkeypatch):
    assert check(monkeypatch, "gsk_abc", "https://api.groq.com/openai/v1")
    assert not check(monkeypatch, "AQ.abc", "https://api.groq.com/openai/v1")
    assert not check(monkeypatch, "gsk_your_key_here", "https://api.groq.com/openai/v1")
    assert not check(monkeypatch, "", "https://api.groq.com/openai/v1")


def test_other_providers_accept_their_own_keys(monkeypatch):
    assert check(monkeypatch, "AQ.abc", "https://generativelanguage.googleapis.com/v1beta/openai/")
    assert not check(monkeypatch, "", "https://generativelanguage.googleapis.com/v1beta/openai/")
    assert check(monkeypatch, "ollama", "http://localhost:11434/v1")  # local models: dummy key


def test_derived_amount_slot_also_allows_reading_the_number():
    from warrant.contract import SlotKind, normalize_slot
    from warrant.policy import Sink

    kinds = normalize_slot(Sink.AMOUNT, [SlotKind.DERIVED])
    assert SlotKind.LOOKUP in kinds and SlotKind.DERIVED in kinds


def test_contract_llm_can_be_switched_off(monkeypatch):
    import warrant.contract as wc
    import warrant.llm as wl

    monkeypatch.setattr(wc.config, "has_groq_key", lambda: True)
    monkeypatch.setattr(wc.config, "CONTRACT_USE_LLM", False)

    def boom(*a, **k):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(wl, "chat_json", boom)
    c = wc.compile_contract("Pay the bill in bill-oct.txt", ["read_file", "send_money"], use_llm=True)
    assert c.compiled_by == "rules"
