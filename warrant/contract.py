"""Intent Contract: compiled from the user's request ONLY (never from tool data),
then frozen and hashed. It is the single source of authority for the run.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from enum import Enum
from typing import Any

from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage
from pydantic import BaseModel, Field

from . import config
from .events import NULL_EMITTER, Emitter
from .policy import Effect, Sink, tool_catalog


class SlotKind(str, Enum):
    USER_LITERAL = "USER_LITERAL"  # value typed by the user in the request
    TRUSTED = "TRUSTED"            # from the user's own records (contacts, account history)
    LOOKUP = "LOOKUP"              # read from data the user pointed at (evidence spans only)
    DELEGATED = "DELEGATED"        # user said "do what X says"; bounded by the envelope
    DERIVED = "DERIVED"            # computed (e.g. an amount difference)


ALL_KINDS = list(SlotKind)

# What a sink accepts when the contract does not narrow it further.
DEFAULT_KINDS: dict[Sink, list[SlotKind]] = {
    Sink.RECIPIENT: [SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED],
    Sink.IBAN: [SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED],
    Sink.AMOUNT: ALL_KINDS,
    Sink.RESOURCE: [SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED],
    Sink.URL: [SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED],
    Sink.SECRET: [SlotKind.USER_LITERAL],
    Sink.PROFILE: [SlotKind.USER_LITERAL, SlotKind.LOOKUP, SlotKind.DELEGATED],
    Sink.TARGET: ALL_KINDS,
}


class Slot(BaseModel):
    sink: Sink
    kinds: list[SlotKind]
    note: str = ""


class Limits(BaseModel):
    max_egress_actions: int = 3
    max_payments: int = 2
    max_total_amount: float | None = None
    max_deletes: int = 1
    internal_domains: list[str] = Field(default_factory=list)


class IntentContract(BaseModel):
    task: str
    allowed_effects: list[Effect]
    user_literals: list[str] = Field(default_factory=list)
    delegated_sources: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    compiled_by: str = "llm"
    hash: str = ""

    def kinds_for(self, sink: Sink) -> list[SlotKind]:
        for s in self.slots:
            if s.sink == sink:
                return s.kinds
        return DEFAULT_KINDS[sink]

    def freeze(self) -> "IntentContract":
        body = self.model_dump(exclude={"hash"})
        self.hash = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        return self


# ---------------- deterministic literal extraction ----------------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)[\w.-]+\.[a-z]{2,}(?:/[\w./?%&=-]*)?|https?://[\w.-]+(?:/[\w./?%&=-]*)?", re.I)
FILE_RE = re.compile(r"[\w.-]+\.(?:txt|pdf|csv|xlsx|docx|md|json|png|jpg)\b", re.I)
QUOTED_RE = re.compile(r"['\"‘“]([^'\"’”]{2,80})['\"’”]")
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def extract_literals(text: str) -> list[str]:
    found: list[str] = []
    for rx in (EMAIL_RE, IBAN_RE, URL_RE, FILE_RE):
        found += rx.findall(text)
    found += [q.strip() for q in QUOTED_RE.findall(text)]
    found += [n for n in NUM_RE.findall(text) if len(n.replace(",", "")) >= 1]
    seen, out = set(), []
    for f in found:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ---------------- keyword fallback (also unioned with the LLM output) ----------------
_EFFECT_WORDS: dict[Effect, list[str]] = {
    Effect.PAY: ["pay", "payment", "transfer", "refund", "send money", "send them", "transaction", "rent", "bill", "difference", "reimburse"],
    Effect.EGRESS: ["send", "email", "e-mail", "forward", "share", "post", "message", "dm ", "reply", "tell ", "invite", "notify", "summary to", "publish"],
    Effect.WRITE: ["create", "book", "schedule", "add ", "update", "append", "reserve", "set up", "make a", "rename", "move", "invite", "reschedule", "calendar", "meeting", "sync"],
    Effect.DELETE: ["delete", "remove", "cancel", "clean up", "erase", "kick"],
    Effect.ACCOUNT: ["password", "my address", "account information", "account info", "user info", "update my info", "moved to"],
}
_DELEGATION_RE = re.compile(
    r"(follow (the |all |his |her |their )?(instructions?|steps|requests?)|do what\b|"
    r"\b(do|complete|handle|perform|execute|carry out|take care of|action)\b[^.]{0,30}\b(tasks?|todos?|to-dos?|instructions?|requests?|actions?|action items)\b[^.]{0,15}\b(in|from|on|of|listed|specified|described|mentioned)\b(?! the order)|"
    r"as (described|instructed|requested|listed|specified) in|according to the (email|message|file|note|instructions))",
    re.I,
)


# Trust order: a value from a stronger source is never less authorised than one from a weaker source.
_STRONGER: dict[SlotKind, list[SlotKind]] = {
    SlotKind.TRUSTED: [SlotKind.USER_LITERAL],
    SlotKind.LOOKUP: [SlotKind.TRUSTED, SlotKind.USER_LITERAL],
    SlotKind.DELEGATED: [SlotKind.TRUSTED, SlotKind.USER_LITERAL],
}
_NUMERIC_SINKS = {Sink.AMOUNT, Sink.TARGET}


def normalize_slot(sink: Sink, kinds: list[SlotKind]) -> list[SlotKind]:
    """Repairs an LLM-proposed slot so it stays a pure narrowing of the sink's default.

    - DERIVED ("computed") is meaningless for identifiers (an IBAN is never computed);
      the model means "read from the data the user pointed at", i.e. LOOKUP.
    - Close upwards in the trust order (LOOKUP allowed => TRUSTED, USER_LITERAL allowed).
    - Intersect with DEFAULT_KINDS so the LLM can never widen a sink (e.g. a DELEGATED secret).
    """
    ks = list(kinds)
    if sink not in _NUMERIC_SINKS and SlotKind.DERIVED in ks:
        ks = [k for k in ks if k != SlotKind.DERIVED] + [SlotKind.LOOKUP]
    for k in list(ks):
        ks += _STRONGER.get(k, [])
    default = DEFAULT_KINDS[sink]
    out = [k for k in ALL_KINDS if k in ks and k in default]
    return out or [SlotKind.USER_LITERAL]


def heuristic_effects(query: str) -> set[Effect]:
    q = f" {query.lower()} "
    effects = {e for e, words in _EFFECT_WORDS.items() if any(w in q for w in words)}
    return effects


def is_delegation(query: str) -> bool:
    return bool(_DELEGATION_RE.search(query))


SYSTEM_PROMPT = """You are the Intent Contract compiler of a security layer for tool-using AI agents.
You see ONLY the user's request and the list of tools. You never see tool outputs.
Produce the minimal authority the request needs. Never grant effects the user did not ask for.

Effects: READ (always allowed), WRITE (create/modify user data), EGRESS (data leaves the user: email, share, post, message),
PAY (money moves), DELETE (destroys data), ACCOUNT (passwords/identity).

Slot kinds for authority-sensitive arguments (sinks: recipient, iban, amount, resource, url, secret, profile, target):
- USER_LITERAL: value must appear in the user's own words.
- TRUSTED: value may come from the user's own records (contacts, account history, own profile).
- LOOKUP: value may come from data the user pointed to (a named file, email, page).
- DELEGATED: the user explicitly delegated ("do the tasks in X", "follow the instructions in X").
- DERIVED: value computed from other values (amount differences, totals).
Narrow a sink to USER_LITERAL when the user typed the exact value (e.g. an email address or IBAN).

Return JSON with keys:
{"task": short restatement,
 "allowed_effects": [..],
 "delegated_sources": [names/addresses/files the user delegated to, or []],
 "slots": [{"sink": "recipient|iban|amount|resource|url|secret|profile|target", "kinds": [..], "note": ".."}],
 "max_total_amount": number or null}
"""


def compile_contract(query: str, tool_names: list[str], use_llm: bool = True, internal_domains: list[str] | None = None) -> IntentContract:
    literals = extract_literals(query)
    effects = heuristic_effects(query)
    delegated = is_delegation(query)
    data: dict[str, Any] = {}
    compiled_by = "rules"
    if use_llm and config.has_groq_key():
        try:
            from .llm import chat_json

            user = json.dumps({"user_request": query, "tools": tool_catalog(tool_names)})
            data = chat_json(SYSTEM_PROMPT, user)
            compiled_by = f"llm:{config.CONTRACT_MODEL} + rules"
        except Exception as exc:  # fall back to rules, never fail open
            data = {"error": str(exc)}
    for e in data.get("allowed_effects", []) or []:
        try:
            effects.add(Effect(str(e).upper()))
        except ValueError:
            pass
    effects.add(Effect.READ)
    slots: list[Slot] = []
    for s in data.get("slots", []) or []:
        try:
            kinds = [SlotKind(str(k).upper()) for k in s.get("kinds", [])]
            if kinds:
                sink = Sink(str(s.get("sink")).lower())
                slots.append(Slot(sink=sink, kinds=normalize_slot(sink, kinds), note=str(s.get("note", ""))[:160]))
        except (ValueError, AttributeError):
            continue
    # Rule: if the user typed explicit addresses and did not delegate, recipients are USER_LITERAL (+ own contacts).
    if not delegated and EMAIL_RE.search(query):
        slots = [s for s in slots if s.sink != Sink.RECIPIENT]
        slots.append(Slot(sink=Sink.RECIPIENT, kinds=[SlotKind.USER_LITERAL, SlotKind.TRUSTED], note="user typed the recipient address; own contacts also allowed"))
    if not delegated and IBAN_RE.search(query):
        slots = [s for s in slots if s.sink != Sink.IBAN]
        slots.append(Slot(sink=Sink.IBAN, kinds=[SlotKind.USER_LITERAL, SlotKind.TRUSTED], note="user typed the IBAN; own payees/history also allowed"))
    # Never let the LLM grant DELEGATED unless the user actually delegated.
    if not delegated:
        for s in slots:
            s.kinds = [k for k in s.kinds if k != SlotKind.DELEGATED] or [SlotKind.USER_LITERAL]
    delegated_sources = [str(x) for x in (data.get("delegated_sources") or [])] if delegated else []
    if delegated:
        delegated_sources += [e for e in EMAIL_RE.findall(query) if e not in delegated_sources]
    if delegated and not delegated_sources:
        delegated_sources = ["*"]
    # A person the user delegates to is trusted within their own organisation's domain.
    deleg_domains = sorted({src.split("@", 1)[1].lower() for src in delegated_sources if "@" in src})
    if delegated:
        # Delegation is powerful but bounded: new payees need confirmation, new external
        # recipients are refused, and DELETE / ACCOUNT still need the user's own words.
        effects.update({Effect.WRITE, Effect.EGRESS, Effect.PAY})
        for e in (Effect.DELETE, Effect.ACCOUNT):
            if e not in heuristic_effects(query):
                effects.discard(e)
    if delegated:
        for sink in (Sink.RECIPIENT, Sink.IBAN):
            if not any(sl.sink == sink for sl in slots):
                slots.append(Slot(sink=sink, kinds=[SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.DELEGATED],
                                  note="delegated: values from the delegated source are bounded by the envelope"))
        # File handles (ids) never appear in the delegated text itself; they are resolved from the
        # drive listing (LOOKUP). Where the file may go is still bounded by the recipient check.
        for sl in slots:
            if sl.sink == Sink.RESOURCE and SlotKind.LOOKUP not in sl.kinds:
                sl.kinds = normalize_slot(Sink.RESOURCE, sl.kinds + [SlotKind.LOOKUP])
    bulk = bool(re.search(r"\b(each|every|all)\b", query, re.I))
    max_amount = data.get("max_total_amount")
    contract = IntentContract(
        task=str(data.get("task") or query)[:200],
        allowed_effects=sorted(effects, key=lambda e: list(Effect).index(e)),
        user_literals=literals,
        delegated_sources=delegated_sources,
        slots=slots,
        limits=Limits(
            max_egress_actions=12 if bulk else 5,
            max_payments=6 if bulk else 3,
            max_total_amount=float(max_amount) if isinstance(max_amount, (int, float)) and max_amount > 0 else None,
            internal_domains=sorted(set((internal_domains or []) + deleg_domains)),
        ),
        compiled_by=compiled_by,
    )
    return contract.freeze()


class ContractCompiler(BasePipelineElement):
    """Pipeline element: compiles + freezes the contract right after the user query."""

    def __init__(self, use_llm: bool = True, emit: Emitter = NULL_EMITTER, internal_domains: list[str] | None = None) -> None:
        self.use_llm = use_llm
        self.emit = emit
        self.internal_domains = internal_domains or []

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        from .gate import WarrantState  # local import to avoid a cycle

        self.emit("stage", stage="contract", status="active")
        contract = compile_contract(query, list(runtime.functions.keys()), self.use_llm, self.internal_domains)
        state = WarrantState(query=query, contract=contract)
        self.emit("contract", contract=contract.model_dump(mode="json"), run_id=state.run_id)
        self.emit("stage", stage="contract", status="done")
        return query, runtime, env, messages, {**extra_args, "warrant": state}
