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
