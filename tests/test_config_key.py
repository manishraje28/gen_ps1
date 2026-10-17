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
