"""Configuration: reads settings from environment variables or a local .env file."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL: str = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Planner = the tool-using agent model (the thing that can be fooled).
PLANNER_MODEL: str = os.environ.get("WARRANT_PLANNER_MODEL", "openai/gpt-oss-20b")
# Contract compiler = sees ONLY the user's request, never tool data.
CONTRACT_MODEL: str = os.environ.get("WARRANT_CONTRACT_MODEL", "openai/gpt-oss-120b")
# Optional LLM pass in the Span Auditor (can only narrow trust, never widen it).
AUDITOR_MODEL: str = os.environ.get("WARRANT_AUDITOR_MODEL", "openai/gpt-oss-120b")
AUDITOR_USE_LLM: bool = os.environ.get("WARRANT_AUDITOR_USE_LLM", "1") == "1"
# 0 = compile the Intent Contract with the rules only (instant, deterministic; what the benchmarks measure).
# Recommended for small local models, which tend to over-narrow slots.
CONTRACT_USE_LLM: bool = os.environ.get("WARRANT_CONTRACT_USE_LLM", "1") == "1"

# Local fallback (Ollama, OpenAI-compatible). When Groq is out of budget (daily token/request limit), would make
# us wait longer than LLM_MAX_WAIT seconds, or is unreachable, the PLANNER switches to this local model instead of
# stopping. The contract compiler and the auditor never use it: they fail fast and fall back to WARRANT's rules.
LOCAL_FALLBACK: bool = os.environ.get("WARRANT_LOCAL_FALLBACK", "1") == "1"
LOCAL_BASE_URL: str = os.environ.get("WARRANT_LOCAL_BASE_URL", "http://localhost:11434/v1")
LOCAL_PLANNER_MODEL: str = os.environ.get("WARRANT_LOCAL_PLANNER_MODEL", "qwen2.5:7b")
# "groq" (default: Groq first, local on failure) or "local" (planner always local, e.g. offline or out of credit).
PLANNER_PROVIDER: str = os.environ.get("WARRANT_PLANNER_PROVIDER", "groq").strip().lower()
LLM_MAX_WAIT: float = float(os.environ.get("WARRANT_LLM_MAX_WAIT", "8"))
# Sent as `reasoning_effort` on every LLM call when set. "none" turns off Qwen3's hidden thinking on Ollama
# (qwen3:8b: ~4x fewer tokens per step, same tool calls). Groq gpt-oss accepts low|medium|high. Empty = not sent.
REASONING_EFFORT: str = os.environ.get("WARRANT_REASONING_EFFORT", "").strip().lower()

TRACES_DIR = ROOT / "traces"
RESULTS_DIR = ROOT / "results"
AUDIT_DIR = ROOT / "audit_logs"
for _d in (TRACES_DIR, RESULTS_DIR, AUDIT_DIR):
    _d.mkdir(exist_ok=True)


def has_groq_key() -> bool:
    """True if an LLM key is configured. Groq keys must look like Groq keys ("gsk_"); for any other
    OpenAI-compatible provider set in GROQ_BASE_URL (e.g. Gemini's endpoint) any non-empty key counts."""
    if not GROQ_API_KEY or GROQ_API_KEY.startswith(("gsk_your", "your_")):
        return False
    if "api.groq.com" in GROQ_BASE_URL:
        return GROQ_API_KEY.startswith("gsk_")
    return True


def local_llm_available(timeout: float = 1.5) -> bool:
    """True if the local OpenAI-compatible server (Ollama) answers. Never raises."""
    if not LOCAL_FALLBACK and PLANNER_PROVIDER != "local":
        return False
    import urllib.request

    try:
        with urllib.request.urlopen(LOCAL_BASE_URL.rstrip("/") + "/models", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def live_llm_available() -> bool:
    """A live planner can run: a Groq key, or the local model (so the app keeps working without credit)."""
    return has_groq_key() or local_llm_available()
