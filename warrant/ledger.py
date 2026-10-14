"""Value Ledger: provenance for every value the agent has seen.

Each tool output is stored as audited spans. When the planner proposes a value
for a sink argument, the gate asks the ledger *where that value came from*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .auditor import DIRECTIVE, EVIDENCE, Span
from .contract import SlotKind


def norm(value: object) -> str:
    s = str(value).strip().lower()
    return re.sub(r"[\s ]+", "", s)


def _as_number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("₹", "").replace("$", "").replace("inr", "").strip())
    except ValueError:
        return None


_NUM_IN_TEXT = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _text_has_value(text: str, value: object) -> bool:
    v = norm(value)
    if not v:
        return False
    t = norm(text)
    if v in t:
        return True
    num = _as_number(value)
    if num is not None:
        for m in _NUM_IN_TEXT.findall(text):
            try:
                if abs(float(m.replace(",", "")) - num) < 1e-6:
                    return True
            except ValueError:
                continue
    return False


@dataclass
class Provenance:
    kind: SlotKind | None
    label: str             # USER / EVIDENCE / DIRECTIVE / NONE
    span_id: str | None
    tool: str | None
    excerpt: str

    def as_dict(self) -> dict:
        return {"kind": self.kind.value if self.kind else None, "label": self.label, "span": self.span_id, "tool": self.tool, "excerpt": self.excerpt[:220]}


class ValueLedger:
    def __init__(self, query: str) -> None:
        self.query = query
        self.spans: list[Span] = []

    def add(self, spans: list[Span]) -> None:
        self.spans.extend(spans)

    def provenance(self, value: object, delegated_sources: list[str]) -> list[Provenance]:
        """All places this value appears, strongest first."""
        out: list[Provenance] = []
        if _text_has_value(self.query, value):
            out.append(Provenance(SlotKind.USER_LITERAL, "USER", None, "user_request", self.query))
        for sp in self.spans:
            if not _text_has_value(sp.text, value):
                continue
            if sp.label == DIRECTIVE:
                out.append(Provenance(None, DIRECTIVE, sp.id, sp.tool, sp.text))
                continue
            if sp.trusted_source:
                out.append(Provenance(SlotKind.TRUSTED, EVIDENCE, sp.id, sp.tool, sp.text))
            if sp.delegated:
                out.append(Provenance(SlotKind.DELEGATED, EVIDENCE, sp.id, sp.tool, sp.text))
            out.append(Provenance(SlotKind.LOOKUP, EVIDENCE, sp.id, sp.tool, sp.text))
        return out

    def snapshot(self, limit: int = 200) -> list[dict]:
        return [s.as_dict() for s in self.spans[-limit:]]


_STOP = {"latest", "email", "emails", "mail", "file", "files", "the", "from", "message", "messages", "tasks", "task", "todo",
         "todos", "list", "inbox", "last", "recent", "most", "note", "notes", "document", "doc", "and", "his", "her", "their"}


def _source_tokens(src: str) -> list[str]:
    src = src.lower().replace("'s", " ")
    if "@" in src:
        return [src.strip()] + [src.split("@")[0]]
    return [w for w in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", src) if w not in _STOP]


def mark_delegation(spans: list[Span], descriptor: str, delegated_sources: list[str]) -> None:
    """Spans from an output that comes from a delegated source get DELEGATED eligibility.

    ``descriptor`` is "tool args + output"; the source must match the call arguments
    or the output header (sender / file name), not text buried in the body.
    """
    if not delegated_sources:
        return
    head = descriptor[:400].lower()
    hit = "*" in delegated_sources
    for src in delegated_sources:
        for tok in _source_tokens(str(src)):
            if re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", head):
                hit = True
    if hit:
        for sp in spans:
            if sp.label == EVIDENCE:
                sp.delegated = True
