"""Intent Contract: compiled from the user's request ONLY (never from tool data),
then frozen and hashed. It is the single source of authority for the run.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage
from pydantic import BaseModel, ConfigDict, Field

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
    Sink.RECORD: [SlotKind.USER_LITERAL, SlotKind.TRUSTED],
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
    # Hard cap per single payment (None = only the total/budget limits apply). Set by org policy.
    max_amount_per_payment: float | None = None


class IntentContract(BaseModel):
    task: str
    allowed_effects: list[Effect]
    user_literals: list[str] = Field(default_factory=list)
    delegated_sources: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    # True forces a confirmation (NEEDS_INPUT: NEW_PAYEE) for every payee IBAN that is neither typed by the
    # user nor in their records, even one with a witness. Off by default: the gate's Two-Witness rule
    # already asks the user about a new payee no independent principal vouches for (Lane E).
    confirm_new_payees: bool = False
    # Off by default: users routinely send to addresses found in data they pointed at ("her e-mail is on
    # her website"). Injected addresses are already TAINTED and Lane B blocks a known contact whose
    # address differs from the records. Organisations that want a confirmation turn it on via OrgPolicy.
    confirm_new_recipients: bool = False
    # Which org policy file narrowed this contract ("" = none). Recorded in the audit log.
    policy_source: str = ""
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
# A quote must not touch a letter on its outer side, so apostrophes ("I'm", "Alice's") are not quotes.
QUOTED_RE = re.compile(r"(?<![\w])['\"‘“]([^'\"’”\n]{2,80}?)['\"’”](?![\w])")
# Words that ask the agent to pick a target from data ("the best-rated hotel"), so the target is a LOOKUP.
_SELECTION_RE = re.compile(r"\b(?:best|highest|lowest|cheapest|top|recommend(?!ed)\w*|choose|suggest|find|any|one with|which|most|least|better)\b", re.I)
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


# ---------------- rules-based effect inference (also unioned with the LLM output) ----------------
# An effect is granted only when the user *commands* it. Many effect words are also nouns
# ("the latest email", "my rent payment", "a meeting"), so a verb counts only in a command
# position: at the start of a clause, after "please / and / then / can you ...", or after
# "want to / need to / remember to ...". Nouns and questions about data grant READ only.
_EFFECT_VERBS: dict[Effect, list[str]] = {
    Effect.PAY: ["pay", r"transfer(?!\s+(?:the\s+|my\s+|this\s+|that\s+)?(?:\S+\s+){0,2}?(?:repo|repository|repositories|ownership|project)s?\b)",
                 "refund", "reimburse", "wire", "send money", "settle", "top up",
                 "buy", "rebuy", "purchase", "order", "place (?:the|an?|my) order", "checkout",
                 r"(?:request|get|ask for|claim) (?:me )?an? refund",
                 r"make (?:a|the|an?\s+\w+) payment", r"cover(?=(?:\s+\S+){0,3}?\s+(?:bill|invoice|rent|fee|cost|payment)s?\b)"],
    Effect.EGRESS: ["send", "email", "e-mail", "mail", "forward", "post", "message", "dm", "reply", "respond",
                    "tell", "notify", "inform", "publish", "text", "cc", "write back", "let \\w+ know",
                    "shoot", "ping", "loop in", r"drop (?:\w+ )?an? (?:line|note|message|email)",
                    r"transfer(?=\s+(?:the\s+|my\s+|this\s+|that\s+)?(?:\S+\s+){0,2}?(?:repo|repository|repositories|ownership|project)s?\b)",
                    r"(?:open|file|raise|create|submit) an? (?:new )?issue"],
    # Submitting data to a web form: log in, register, sign up, fill in, submit.
    Effect.FORM: ["submit", r"fill (?:in|out)", "register", "sign (?:up|in)", "log ?in", "subscribe", "enroll", "apply"],
    # "book 5 hours for it" blocks calendar time (WRITE), it is not a reservation.
    Effect.BOOK: [r"book(?!\s+(?:\d+|an?|one|two|three|four|five|six)\s+(?:hours?|minutes?|mins?|days?)\b)", "reserve",
                  "make (?:a|an|me a) (?:reservation|booking)",
                  r"(?:grab|get|find|snag|secure) (?:me|us) an? (?:\w+ )?(?:room|table|car|seat|suite|spot|rental)", r"rent (?:me|us)"],
    Effect.WRITE: ["create", "schedule", "add", "update", "setup", r"book(?=\s+(?:\d+|an?|one|two|three|four|five|six)\s+(?:hours?|minutes?|mins?|days?)\b)", "append", "set up", "set a", "rename", "move",
                   "invite", "reschedule", "write", "edit", "organi[sz]e", "put", "join", "change", "modify",
                   "make (?:a|an|the|me a) (?:appointment|event|note|file|new|list|copy|calendar)",
                   "clone", "push", "pull", "star", "unstar", "fork", "commit", "save", "copy", "upload",
                   r"remove (?:\S+\s+){0,2}?stars?"],
    Effect.DELETE: ["delete", "remove", "cancel", "clean up", "erase", "kick", "trash", "discard", "purge",
                    "get rid of", "clear out", "empty", "wipe"],
    Effect.ACCOUNT: [],  # phrase rule only, see _ACCOUNT_RE
}
_LEAD = (
    r"(?:^|[.!?;:\n(\"'“‘,]|\b\d+[.)]|"
    r"\b(?:please|pls|and|then|also|kindly|just|or|now|first|next|finally|afterwards|after that|lastly|you|"
    r"(?:want|need|like|have|going|able|asked|remember|forget|sure|try|wish|plan|ready|you) to|"
    r"(?:task|job|goal) is to|help (?:me|us) to|"
    # a relayed request at the start of a sentence: "Alice asked me to invite her", "Bob told me to ..."
    # (not inside a question: "Find the email where Bob asked me to pay him")
    r"(?:^|(?<=[.!?\n]\s))[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?\s+(?:ask|asks|asked|told|tells|wants|wanted|needs)\s+me\s+to|"
    # a purpose clause after a command: "use it to buy", "find his address in the CV to send him ..."
    r"(?:use|using|find|check|download|look up|visit|read)\b"
    r"(?:(?!\b(?:ask|asks|asked|told|tells|want|wants|wanted|how|whether|what|where|when|why|which|who|if|link|way)\b)[^.!?\n]){0,60}?\bto)\b)"
)
_FILLER = r"(?:\s+(?:please|also|then|just|kindly|now|quickly|immediately|go ahead and|actually|still|first|both))*"
_EFFECT_RES: dict[Effect, re.Pattern] = {
    e: re.compile(_LEAD + _FILLER + r"\s*(?:" + "|".join(ws) + r")\b", re.I) for e, ws in _EFFECT_VERBS.items() if ws
}
# "tell me / show us" answers the user in chat; nothing leaves the user.
_SELF_ONLY_RE = re.compile(_LEAD + _FILLER + r"\s*(?:(?:tell|inform|text|message)\s+(?:me|us)|let (?:me|us) know)\b(?!\s+(?:by|via|over|in an?)\s+(?:e-?mail|message|slack|text))", re.I)
# Money words that turn "send them the difference" into a payment rather than an email.
_MONEY = r"(?:difference|money|amount|payment|refund|rent|fee|balance|\$|€|£|¥|₹|usd|eur|gbp|inr|chf|\d)"
_SEND_MONEY_RE = re.compile(_LEAD + _FILLER + r"\s*(?:send|give|return)\s+(?:\w+\s+){0,3}?(?:back\s+)?(?:the\s+|an?\s+)?(?:additional\s+|extra\s+|remaining\s+)?" + _MONEY, re.I)
# "update my rent payment", "set up a recurring transaction", "change the standing order".
_PAY_OBJECT_RE = re.compile(
    r"\b(?:update|change|adjust|modify|set up|schedule|cancel|increase|decrease|fix|correct)\b(?:\s+\S+){0,5}?\s+"
    r"(?:payments?|transactions?|transfers?|standing orders?|scheduled transactions?|rent|subscriptions?)\b", re.I)
# Identity / credential changes need an explicit change verb aimed at an account field.
_ACCOUNT_RE = re.compile(
    _LEAD + _FILLER + r"\s*(?:update|change|reset|set|edit|modify|correct)\s+(?:my|our|the user's|the account|the)\s+(?:\S+\s+){0,2}?"
    r"(?:password|passcode|pin|address|account (?:info|information|details|settings)|user (?:info|information|details)|"
    r"profile|personal (?:info|information|details)|(?:first |last )?name|street|city|phone number|e-?mail address)\b|"
    # "My new address is 12 Elm St, please update it."
    r"\bmy new (?:password|passcode|pin|address|phone number|e-?mail address|name)\b[^.?!\n]{0,80}?\b(?:update|change|set|use)\b|"
    r"\b(?:update|change|set)\b[^.?!\n]{0,40}?\bto my new (?:password|address|phone number|e-?mail address|name)\b|"
    # "add the new SSH key from my email", "remove the old ssh key"
    r"\b(?:add|remove|delete|revoke|rotate|replace)\b(?:\s+\S+){0,4}?\s+(?:ssh|deploy|gpg|access)\s+keys?\b", re.I)
# The user sends the agent to a website ("visit www.x.com and pay the bill"): working on that page may
# need its forms (log in, pay, register). FORM is granted only together with another commanded effect;
# where the form posts is still checked as a URL sink (attacker URLs stay TAINTED).
_SITE_RE = re.compile(r"(?:https?://|www\.)[\w.-]+|\b(?:visit|go to|open|on|via|through)\s+(?:the\s+)?(?:\w+\s+)?(?:website|site|portal|page|webpage)\b|\b(?:a|the) (?:\w+ )?link\b", re.I)
# Passive or noun-phrase egress the verb rule cannot see: "a summary to X", "should be emailed to X".
_EGRESS_EXTRA_RE = re.compile(
    r"\b(?:summary|report|copy|message|note|e-?mail|invitation)\s+to\s+(?:[\w.+-]+@[\w-]+|my |our |the |his |her |their |[A-Z][a-z]+)|"
    r"\b(?:should|must|to|will|please|needs? to|has to|have to)\s+(?:be|get)\s+(?:sent|e-?mailed|forwarded|shared|posted|messaged)\b|"
    r"\bwrite\b(?:\s+\S+){0,3}?\s+(?:the |a |an )?(?:following )?(?:message|e-?mail|reply|dm|post|note to)\b|"
    # "get the report over to priya@acme.test", "pass the invoice on to accounts@acme.test"
    r"\b(?:get|pass|hand|give)\b(?:\s+\S+){0,5}?\s+(?:over |on |along )?to\s+[\w.+-]+@[\w-]+\.", re.I)
# "share X with Y" leaves the user; "please share the names" (no one to share with) is an answer in chat.
_SHARE_RE = re.compile(_LEAD + _FILLER + r"\s*share\b(?:\s+\S+){0,12}?\s+(?:with|to)\b|\bshare\b[^.\n]{0,80}?[\w.+-]+@[\w-]+\.", re.I)
_PASSIVE_PAY_RE = re.compile(r"\b(?:should|must|to|will|please|needs? to|has to|have to)\s+(?:be|get)\s+(?:paid|transferred|refunded|reimbursed)\b", re.I)

_DELEGATION_RE = re.compile(
    r"(follow (the |all |his |her |their )?(instructions?|steps|requests?)|do what\b|"
    r"\b(do|complete|handle|perform|execute|carry out|take care of|action|finish)\b[^.]{0,30}\b(tasks?|todos?|to-dos?|instructions?|requests?|actions?|action items|homework|assignments?)\b[^.]{0,15}\b(in|from|on|of|listed|specified|described|mentioned|(?:he|she|they)\s+(?:request|ask)(?:s|ed)?)\b(?! the order)|"
    r"\b(instructions?|steps|details)\s+(on|for|about)\b[^.]{0,60}\b(are|is)\s+(included|provided|given|listed|described|explained)\s+(in|on)\b|"
    r"\b(address|resolve|fulfil+|complete|handle)\s+(his|her|their)\s+(issues?|requests?|tasks?|needs)\b|"
    # "Alice sent me an email ... could you check it and fulfill her request / achieve it / complete them?"
    r"\b(sent|emailed|messaged|forwarded|shared)\s+me\b.{0,160}?\b(complete|fulfil+|achieve|address|handle|take care of|do|resolve|finish)\s+"
    r"(it|them|this|that|what\s+(he|she|they)\s+(needs?|wants?|asks?|asked)|(his|her|their)\s+(requests?|issues?|needs|tasks?|asks?))\b|"
    r"as (described|instructed|requested|listed|specified) in|"
    r"\b(act|proceed|do (it|this|that|everything)|respond|handle (it|this|that)) according to the (email|message|file|note|instructions))",
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
    # A number computed from known values is allowed => each known value read as is must be too
    # ("DERIVED only" from a small model made the bill's own amount unpayable).
    if sink in _NUMERIC_SINKS and SlotKind.DERIVED in ks:
        ks += [SlotKind.LOOKUP, SlotKind.TRUSTED, SlotKind.USER_LITERAL]
    for k in list(ks):
        ks += _STRONGER.get(k, [])
    default = DEFAULT_KINDS[sink]
    out = [k for k in ALL_KINDS if k in ks and k in default]
    return out or [SlotKind.USER_LITERAL]


def heuristic_effects(query: str) -> set[Effect]:
    """Effects the user commands in their own words. READ is implicit and not returned."""
    q = query.strip()
    effects = {e for e, rx in _EFFECT_RES.items() if rx.search(q)}
    if Effect.EGRESS in effects:
        # Drop EGRESS when every egress verb is "tell me", or "send" is really "send them the difference".
        rest = _SELF_ONLY_RE.sub(" ", q)
        rest = _SEND_MONEY_RE.sub(" ", rest)
        if not _EFFECT_RES[Effect.EGRESS].search(rest):
            effects.discard(Effect.EGRESS)
    if _SEND_MONEY_RE.search(q) or _PAY_OBJECT_RE.search(q) or _PASSIVE_PAY_RE.search(q):
        effects.add(Effect.PAY)
    if _ACCOUNT_RE.search(q):
        effects.add(Effect.ACCOUNT)
    if _EGRESS_EXTRA_RE.search(q) or _SHARE_RE.search(q):
        effects.add(Effect.EGRESS)
    # "update my rent payment" / "update my address" are PAY / ACCOUNT, not a generic WRITE.
    if Effect.WRITE in effects and (Effect.PAY in effects or Effect.ACCOUNT in effects):
        rest = _ACCOUNT_RE.sub(" ", _PAY_OBJECT_RE.sub(" ", q))
        if not _EFFECT_RES[Effect.WRITE].search(rest):
            effects.discard(Effect.WRITE)
    # "write them the following message" is EGRESS, not WRITE.
    if Effect.WRITE in effects and Effect.EGRESS in effects:
        rest = _EGRESS_EXTRA_RE.sub(" ", q)
        if not _EFFECT_RES[Effect.WRITE].search(rest):
            effects.discard(Effect.WRITE)
    # "add the SSH key" is an account change, not a generic WRITE.
    if Effect.ACCOUNT in effects and Effect.WRITE in effects and not _EFFECT_RES[Effect.WRITE].search(_ACCOUNT_RE.sub(" ", q)):
        effects.discard(Effect.WRITE)
    # "transfer the one with the most stars to Alex" in a sentence about repositories hands over a repo.
    for sentence in re.split(r"(?<=[.!?\n])\s+", q):
        if re.search(r"\btransfer\b", sentence, re.I) and re.search(r"\brepo(?:sitor(?:y|ies))?s?\b", sentence, re.I) \
                and not re.search(_MONEY, sentence.replace("repositor", ""), re.I):
            effects.add(Effect.EGRESS)
            if not any(_EFFECT_RES[Effect.PAY].search(x) for x in re.split(r"(?<=[.!?\n])\s+", q) if x is not sentence):
                effects.discard(Effect.PAY)
    # FORM is the mechanism, not the authority: a commanded payment or send, or work on a website the
    # user named, may need a web form. Where the form posts is checked as a URL sink.
    if effects and (_SITE_RE.search(q) or effects & {Effect.PAY, Effect.EGRESS}):
        effects.add(Effect.FORM)
    return effects


_MENTION_RES: dict[Effect, re.Pattern] = {
    e: re.compile(r"\b(?:" + "|".join(ws) + r")", re.I) for e, ws in _EFFECT_VERBS.items() if ws
}
_MENTION_RES[Effect.EGRESS] = re.compile(r"\b(?:share|" + "|".join(_EFFECT_VERBS[Effect.EGRESS]) + r")", re.I)
_MENTION_RES[Effect.ACCOUNT] = re.compile(r"\b(?:password|passcode|pin|address|account|profile|user info|personal)\b", re.I)


def _effect_mentioned(effect: Effect, query: str) -> bool:
    """Loose check: does any word for this effect appear anywhere in the request (any position, any form)?"""
    rx = _MENTION_RES.get(effect)
    return bool(rx and rx.search(query)) or effect in heuristic_effects(query)


_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+|(?<=\d[.)])\s+(?=[A-Z])")
_PLURAL_OBJECT_RE = re.compile(r"\b(?:them|everyone|everybody|both|each|all|those|these|people|users|members|participants|attendees)\b", re.I)


def _commanded_count(query: str, effect: Effect, literal_re: re.Pattern, default: int) -> int:
    """How many times the user asks for this effect: one per sentence that commands it, at least the
    number of distinct addresses/IBANs they typed. A plural object ("message them") keeps the default."""
    n = 0
    for sentence in _SENTENCE_RE.split(query):
        if effect in heuristic_effects(sentence):
            if _PLURAL_OBJECT_RE.search(sentence):
                return default
            n += 1
    return max(1, n, len(set(literal_re.findall(query))))


def is_delegation(query: str) -> bool:
    return bool(_DELEGATION_RE.search(query))


SYSTEM_PROMPT = """You are the Intent Contract compiler of a security layer for tool-using AI agents.
You see ONLY the user's request and the list of tools. You never see tool outputs.
Produce the minimal authority the request needs. Never grant effects the user did not ask for.

Effects: READ (always allowed), WRITE (create/modify user data: calendar, files, channels), BOOK (hotel/car/restaurant reservations), EGRESS (data leaves the user: email, share, post, message),
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


def compile_contract(
    query: str,
    tool_names: list[str],
    use_llm: bool = True,
    internal_domains: list[str] | None = None,
    org_policy: "OrgPolicy | None" = None,
) -> IntentContract:
    literals = extract_literals(query)
    effects = heuristic_effects(query)
    delegated = is_delegation(query)
    data: dict[str, Any] = {}
    compiled_by = "rules"
    if use_llm and config.CONTRACT_USE_LLM and config.has_groq_key():
        try:
            from .llm import chat_json

            user = json.dumps({"user_request": query, "tools": tool_catalog(tool_names)})
            data = chat_json(SYSTEM_PROMPT, user)
            compiled_by = f"llm:{config.CONTRACT_MODEL} + rules"
        except Exception as exc:  # fall back to rules, never fail open
            data = {"error": str(exc)}
    for e in data.get("allowed_effects", []) or []:
        try:
            eff = Effect(str(e).upper())
        except ValueError:
            continue
        # The LLM may recover an effect the command rules missed, but only if the user's words
        # contain a trigger for it somewhere; it can never invent an effect from nothing.
        if eff == Effect.READ or eff in effects or _effect_mentioned(eff, query):
            effects.add(eff)
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
        effects.update({Effect.WRITE, Effect.EGRESS, Effect.PAY, Effect.FORM})
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
    # Rule: the user named what to book ("reserve 'Le Marais Boutique'") and did not ask the agent to
    # choose one, so the booking target must come from the user's own words, not any real hotel in the data.
    named = [q for q in QUOTED_RE.findall(query) if not EMAIL_RE.search(q) and not FILE_RE.search(q)]
    if Effect.BOOK in effects and named and not _SELECTION_RE.search(query) and not delegated:
        slots = [s for s in slots if s.sink != Sink.TARGET]
        slots.append(Slot(sink=Sink.TARGET, kinds=[SlotKind.USER_LITERAL], note="user named the booking target"))
    bulk = bool(re.search(r"\b(each|every|all)\b", query, re.I))
    max_egress, max_payments = (12, 6) if bulk else (5, 3)
    if not bulk and not delegated:
        # Exactly as many sends / payments as the user asked for, so a hijacked planner cannot repeat an
        # allowed action (e.g. a second email to the user's own recipient carrying injected content).
        max_egress = min(max_egress, _commanded_count(query, Effect.EGRESS, EMAIL_RE, max_egress))
        max_payments = min(max_payments, _commanded_count(query, Effect.PAY, IBAN_RE, max_payments))
    max_amount = data.get("max_total_amount")
    contract = IntentContract(
        task=str(data.get("task") or query)[:200],
        allowed_effects=sorted(effects, key=lambda e: list(Effect).index(e)),
        user_literals=literals,
        delegated_sources=delegated_sources,
        slots=slots,
        limits=Limits(
            max_egress_actions=max_egress,
            max_payments=max_payments,
            max_total_amount=float(max_amount) if isinstance(max_amount, (int, float)) and max_amount > 0 else None,
            internal_domains=sorted(set((internal_domains or []) + deleg_domains)),
        ),
        compiled_by=compiled_by,
        # Lane E: a payee taken from a document is decided by the gate's Two-Witness rule (one independent
        # principal must vouch for it), not by how many files the request names. Org policy can still
        # force a confirmation for every new payee.
        confirm_new_payees=False,
    )
    policy = org_policy if org_policy is not None else load_org_policy()
    if policy is not None:
        contract = apply_org_policy(contract, policy)
    return contract.freeze()


# ---------------- organisation policy: can only NARROW a compiled contract ----------------
class OrgPolicy(BaseModel):
    """Organisation-wide limits, e.g. loaded from YAML. Every field can only remove authority.

    There is deliberately no way to add an effect, a slot kind, a delegated source or an internal
    domain here: an org policy that could widen a contract would be a second, unaudited authority.
    """

    model_config = ConfigDict(extra="forbid")  # a typo in a policy file must fail loudly, not be ignored

    name: str = "org-policy"
    blocked_effects: list[Effect] = Field(default_factory=list)
    max_egress_actions: int | None = None
    max_payments: int | None = None
    max_total_amount: float | None = None
    max_amount_per_payment: float | None = None
    max_deletes: int | None = None
    require_user_literal: list[Sink] = Field(default_factory=list)  # e.g. [iban, secret]
    allow_delegation: bool = True
    confirm_new_payees: bool = False  # True forces the confirmation on; a policy can never switch it off
    confirm_new_recipients: bool = False


def _cap(current: float | int | None, cap: float | int | None):
    if cap is None:
        return current
    return cap if current is None else min(current, cap)


def apply_org_policy(contract: IntentContract, policy: OrgPolicy) -> IntentContract:
    """Returns a narrowed copy of the contract. READ is never removed."""
    c = contract.model_copy(deep=True)
    c.allowed_effects = [e for e in c.allowed_effects if e == Effect.READ or e not in policy.blocked_effects]
    lim = c.limits
    lim.max_egress_actions = max(0, _cap(lim.max_egress_actions, policy.max_egress_actions))
    lim.max_payments = max(0, _cap(lim.max_payments, policy.max_payments))
    lim.max_deletes = max(0, _cap(lim.max_deletes, policy.max_deletes))
    lim.max_total_amount = _cap(lim.max_total_amount, policy.max_total_amount)
    lim.max_amount_per_payment = _cap(lim.max_amount_per_payment, policy.max_amount_per_payment)
    if not policy.allow_delegation:
        c.delegated_sources = []
        for s in c.slots:
            s.kinds = [k for k in s.kinds if k != SlotKind.DELEGATED] or [SlotKind.USER_LITERAL]
    for sink in policy.require_user_literal:
        kinds_now = c.kinds_for(sink)
        narrowed = [SlotKind.USER_LITERAL] if SlotKind.USER_LITERAL in kinds_now else []
        c.slots = [s for s in c.slots if s.sink != sink]
        c.slots.append(Slot(sink=sink, kinds=narrowed or [SlotKind.USER_LITERAL], note=f"org policy '{policy.name}': user must type it"))
    # Confirmation flags can only be switched on by policy, never off.
    c.confirm_new_payees = c.confirm_new_payees or policy.confirm_new_payees
    c.confirm_new_recipients = c.confirm_new_recipients or policy.confirm_new_recipients
    c.policy_source = policy.name
    c.hash = ""
    return c


ORG_POLICY_ENV = "WARRANT_ORG_POLICY"


def load_org_policy(path: str | Path | None = None) -> OrgPolicy | None:
    """Loads the org policy from `path` or the WARRANT_ORG_POLICY env var (YAML). None if unset.

    A set-but-broken policy raises: running without the limits the operator asked for would fail open.
    """
    p = path or os.environ.get(ORG_POLICY_ENV, "").strip()
    if not p:
        return None
    import yaml

    data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"org policy {p} must be a YAML mapping")
    data.setdefault("name", Path(p).stem)
    return OrgPolicy.model_validate(data)


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
