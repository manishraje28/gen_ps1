"""The Warrant Gate: a deterministic reference monitor that replaces AgentDojo's
ToolsExecutor. Every proposed tool call is checked before it touches the world:

  1. effect check     - is this kind of effect in the frozen Intent Contract?
  2. per-arg warrants - every sink argument value must trace to an allowed source
                        (user literal / trusted records / evidence / delegation)
  3. shadow execution - run on a deep copy of the environment, diff the state,
                        check the effect envelope (counts, amounts, deletes)
  4. commit or refuse - refused calls return a typed reason so the planner can
                        recover and finish the user's real task
  5. audit            - every decision is appended to a JSONL ledger
"""
from __future__ import annotations

import json
import re
import time
import uuid
from ast import literal_eval
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.agent_pipeline.tool_execution import is_string_list, tool_result_to_str
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatMessage, ChatToolResultMessage, text_content_block_from_string

from . import config
from .auditor import DIRECTIVE, audit_output, mask_text
from .contract import IntentContract, SlotKind
from .events import NULL_EMITTER, Emitter
from .ledger import Provenance, ValueLedger, mark_delegation, norm
from .policy import Effect, Sink, get_policy

ALLOWED, REFUSED, NEEDS_INPUT = "ALLOWED", "REFUSED", "NEEDS_INPUT"
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_EMBEDDED_RE = re.compile(r"(?:https?://|www\.)[\w.-]+(?:/[\w./?%&=-]*)?|[\w.+-]+@[\w-]+\.[\w.-]+|\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")


@dataclass
class WarrantState:
    query: str
    contract: IntentContract
    run_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    ledger: ValueLedger = None  # type: ignore[assignment]
    egress: int = 0
    payments: int = 0
    paid_total: float = 0.0
    deletes: int = 0
    seq: int = 0
    decisions: list[dict] = field(default_factory=list)
    # The user's own records (contacts, payment history, profile), read once before the run
    # commits anything, so a payment made during the run can never vouch for itself.
    records: list["TrustedRecord"] | None = None

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = ValueLedger(self.query)


def _values(v: Any) -> list[Any]:
    if v is None or v == "" or v == []:
        return []
    if isinstance(v, dict):  # e.g. {"type": "file", "file_id": "19"}
        keyed = [v[k] for k in ("file_id", "id", "email", "iban", "url") if k in v]
        return [x for x in (keyed or list(v.values())) if isinstance(x, (str, int, float)) and x not in ("", "file")]
    if isinstance(v, (list, tuple, set)):
        out: list[Any] = []
        for x in v:
            out += _values(x)
        return out
    return [v]


def _domain(addr: str) -> str:
    return addr.split("@", 1)[1].lower() if "@" in str(addr) else ""


# ---------------- state diff (for shadow execution) ----------------
def state_diff(before: Any, after: Any, path: str = "", out: list | None = None, limit: int = 40) -> list[dict]:
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after), key=str):
            state_diff(before.get(k), after.get(k), f"{path}.{k}" if path else str(k), out, limit)
    elif isinstance(before, list) and isinstance(after, list):
        if len(after) > len(before) and after[: len(before)] == before:
            for item in after[len(before):]:
                out.append({"op": "add", "path": path, "value": item})
        elif len(after) < len(before):
            removed = [x for x in before if x not in after]
            for item in removed[:5]:
                out.append({"op": "remove", "path": path, "value": item})
        else:
            for i, (b, a) in enumerate(zip(before, after)):
                if b != a:
                    state_diff(b, a, f"{path}[{i}]", out, limit)
    elif isinstance(before, dict) and isinstance(after, list) or before != after:
        out.append({"op": "change", "path": path, "before": before, "after": after})
    return out


# ---------------- trusted records (payee / contact binding) ----------------
# Words too generic to identify a payee or contact.
_NAME_STOP = {
    "from", "with", "this", "that", "your", "yours", "have", "will", "into", "only", "please", "dear", "regards",
    "thanks", "thank", "payment", "payments", "invoice", "bill", "bills", "amount", "account", "accounts", "bank",
    "iban", "email", "mail", "date", "subject", "total", "paid", "transfer", "recipient", "sender", "name", "note",
    "notes", "saved", "payee", "monthly", "month", "year", "recurring", "true", "false", "none", "null", "balance",
    "details", "number", "customer", "contact", "contacts", "address", "phone", "info", "information", "team",
    "office", "billing", "support", "service", "services", "company", "limited", "inc", "corp", "new",
    "updated", "update", "send", "sent", "money", "due", "description",
}
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")


def _name_tokens(text: str) -> set[str]:
    stripped = _EMAIL_RE.sub(" ", _IBAN_RE.sub(" ", text))
    return {w.lower().strip("'-") for w in _WORD_RE.findall(stripped)} - _NAME_STOP


@dataclass
class TrustedRecord:
    """One entry of the user's own records: the names in it and the IBANs / emails it binds."""
    tokens: set[str]
    ibans: set[str]
    emails: set[str]
    source: str
    text: str


def records_from_text(text: str, source: str) -> list[TrustedRecord]:
    """Splits a trusted tool output (YAML list or plain lines) into per-entry records."""
    out = []
    for ch in re.split(r"\n(?=- )|\n\s*\n", text):
        ibans = {m.upper() for m in _IBAN_RE.findall(ch)}
        emails = {m.lower() for m in _EMAIL_RE.findall(ch)}
        if ibans or emails:
            out.append(TrustedRecord(_name_tokens(ch), ibans, emails, source, ch.strip()[:200]))
    return out


# ---------------- amount binding ----------------
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_MAX_NUMS = 120  # bound the pairwise search for derived amounts


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.findall(text):
        f = _to_float(m)
        if f is not None and f not in out:
            out.append(f)
    return out


def amount_tolerance(x: float) -> float:
    """Rounding slack only: 0.1% of the amount, at least one paisa / cent."""
    return max(0.01, abs(x) * 0.001)


def derive_amount(target: float, nums: list[float]) -> str | None:
    """Explains target as a simple computation over known numbers (sum, difference, product,
    ratio, percentage), or returns None. Used for DERIVED amounts such as 'pay the difference'."""
    nums = nums[:_MAX_NUMS]
    tol = amount_tolerance(target)
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:  # two different numbers: no "pay double" (a + a)
            for expr, val in ((f"{a:g} + {b:g}", a + b), (f"|{a:g} - {b:g}|", abs(a - b)), (f"{a:g} x {b:g}", a * b),
                              (f"{a:g} / {b:g}", a / b if b else None), (f"{b:g} / {a:g}", b / a if a else None),
                              (f"{a:g}% of {b:g}", a * b / 100)):
                if val is not None and abs(val - target) <= tol:
                    return expr
    return None


def _short(v: Any, n: int = 160) -> Any:
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + "…"


class WarrantGate(BasePipelineElement):
    def __init__(
        self,
        emit: Emitter = NULL_EMITTER,
        mask_directives: bool = True,
        auditor_llm: bool | None = None,
        audit_dir=None,
        output_formatter: Callable[[Any], str] = tool_result_to_str,
    ) -> None:
        self.emit = emit
        self.mask = mask_directives
        self.auditor_llm = auditor_llm
        self.audit_dir = audit_dir or config.AUDIT_DIR
        self.fmt = output_formatter

    # ---------------- audit ledger ----------------
    def _audit(self, state: WarrantState, record: dict) -> None:
        record = {"ts": time.time(), "run_id": state.run_id, "contract_hash": state.contract.hash,
                  "policy_source": getattr(state.contract, "policy_source", "") or "", **record}
        path = self.audit_dir / f"{state.run_id}.jsonl"
        if not path.exists():
            head = {"ts": time.time(), "run_id": state.run_id, "type": "contract", "query": state.query, "contract": state.contract.model_dump(mode="json")}
            path.write_text(json.dumps(head, default=str) + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self.emit("audit", record={k: _short(v, 300) for k, v in record.items()})

    # ---------------- per-argument warrant check ----------------
    def _check_value(self, state: WarrantState, tool: str, arg: str, sink: Sink, value: Any) -> dict:
        c = state.contract
        allowed = c.kinds_for(sink)
        provs: list[Provenance] = state.ledger.provenance(value, c.delegated_sources)
        good = [p for p in provs if p.kind is not None]
        tainted = [p for p in provs if p.label == DIRECTIVE]
        res = {"arg": arg, "sink": sink.value, "value": _short(value, 80), "allowed_kinds": [k.value for k in allowed], "provenance": [p.as_dict() for p in provs[:4]]}

        prefer = [SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DERIVED, SlotKind.DELEGATED]
        match = next((p for k in prefer for p in good if p.kind == k and k in allowed), None)
        if match is None and SlotKind.DERIVED in allowed and sink == Sink.AMOUNT:
            match = Provenance(SlotKind.DERIVED, "DERIVED", None, None, "computed amount")
        if match is not None:
            # Delegation is bounded: delegated recipients must be internal or already-known contacts.
            if match.kind == SlotKind.DELEGATED and sink == Sink.RECIPIENT:
                known = any(p.kind == SlotKind.TRUSTED for p in good)
                internal = _domain(str(value)) in [d.lower() for d in c.limits.internal_domains]
                if not (known or internal):
                    return {**res, "verdict": REFUSED, "code": "DELEGATION_SCOPE",
                            "why": f"'{value}' is outside the organisation and not a known contact; delegation does not cover new external recipients"}
            if match.kind == SlotKind.DELEGATED and sink == Sink.IBAN and not any(p.kind == SlotKind.TRUSTED for p in good):
                return {**res, "verdict": NEEDS_INPUT, "code": "NEW_PAYEE",
                        "why": f"'{value}' is a new payee taken from delegated content; needs the user's confirmation"}
            if sink in (Sink.IBAN, Sink.RECIPIENT) and match.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED):
                held = self._binding_check(state, sink, value, good)
                if held is not None:
                    return {**res, **held}
            return {**res, "verdict": ALLOWED, "kind": match.kind.value, "why": f"warrant: {match.kind.value} ({match.tool or 'user'})"}
        if tainted and not good:
            return {**res, "verdict": REFUSED, "code": "TAINTED_VALUE",
                    "why": f"'{value}' only appears inside instruction-like text in untrusted data (span {tainted[0].span_id}), not in the user's request"}
        if good:
            kinds = sorted({p.kind.value for p in good if p.kind})
            return {**res, "verdict": REFUSED, "code": "SLOT_MISMATCH",
                    "why": f"'{value}' comes from {', '.join(kinds)} but the contract requires {', '.join(k.value for k in allowed)} for {sink.value}"}
        return {**res, "verdict": REFUSED, "code": "NO_PROVENANCE",
                "why": f"'{value}' does not appear in the user's request or in any data the agent has read"}

    # ---------------- payee / contact binding ----------------
    def _load_records(self, state: WarrantState, runtime: FunctionsRuntime, env: Env) -> None:
        """Reads the user's own records once, before the run commits anything: every READ tool
        marked trusted_source that needs no arguments, run on a deep copy of the world. The
        results never reach the planner; they can only make the gate stricter."""
        if state.records is not None:
            return
        records: list[TrustedRecord] = []
        for name, fn in runtime.functions.items():
            pol = get_policy(name)
            if pol.effect != Effect.READ or not pol.trusted_source:
                continue
            try:
                if fn.parameters.model_json_schema().get("required"):
                    continue
                result, err = runtime.run_function(env.model_copy(deep=True), name, {})
                if not err:
                    records += records_from_text(self.fmt(result), name)
            except Exception:  # a failed probe only means fewer records; nothing is allowed because of it
                continue
        state.records = records

    @staticmethod
    def _known_records(state: WarrantState) -> list[TrustedRecord]:
        recs = list(state.records or [])
        # Trusted outputs the agent itself read (e.g. a contact search). The auditor splits an
        # output into line spans, so rejoin each call's spans to keep a name next to its address.
        by_call: dict[str, list] = {}
        for sp in state.ledger.spans:
            if sp.trusted_source:
                by_call.setdefault(sp.call_id, []).append(sp)
        for spans in by_call.values():
            recs += records_from_text("\n".join(sp.text for sp in spans), spans[0].tool)
        return recs

    def _binding_check(self, state: WarrantState, sink: Sink, value: Any, good: list[Provenance]) -> dict | None:
        """A value taken from documents (LOOKUP / DELEGATED) must not contradict the user's own
        records. If the document names a payee or contact the user already has but gives a
        different IBAN or address, someone may be impersonating them (the classic "our bank
        details changed" fraud). Returns a NEEDS_INPUT verdict, or None if the value is fine."""
        c = state.contract
        is_iban = sink == Sink.IBAN
        v = str(value).strip()
        key = re.sub(r"\s+", "", v).upper() if is_iban else v.lower()
        code = "NEW_PAYEE" if is_iban else "NEW_RECIPIENT"
        records = self._known_records(state)
        if key in {x for r in records for x in (r.ibans if is_iban else r.emails)}:
            return None  # already one of the user's own payees / contacts
        doc_tokens: set[str] = set()
        for p in good:
            if p.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED):
                doc_tokens |= _name_tokens(p.excerpt)
        for r in records:
            theirs = r.ibans if is_iban else r.emails
            shared = sorted(doc_tokens & r.tokens)
            if theirs and shared:
                return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                        "why": f"'{v}' comes from a document that names '{' '.join(shared[:3])}', but your own records ({r.source}) "
                               f"have {', '.join(sorted(theirs)[:2])} for them. Possible impersonation: confirm with the user before using the new one"}
        if is_iban and getattr(c, "confirm_new_payees", None) is True:
            return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                    "why": f"'{v}' is a new payee (not typed by the user, not in their records); the contract asks for confirmation"}
        if not is_iban and getattr(c, "confirm_new_recipients", None) is True:
            if _domain(v) not in [d.lower() for d in c.limits.internal_domains]:
                return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                        "why": f"'{v}' is a new external recipient (not typed by the user, not a contact); the contract asks for confirmation"}
        return None

    def _check_amount(self, state: WarrantState, arg: str, value: Any) -> dict:
        """An amount must be bound to something: a number the user typed, a number in their own
        records or in evidence (within rounding tolerance), or a simple computation over those
        (DERIVED). A number that only appears in instruction-like text is TAINTED."""
        c = state.contract
        allowed = c.kinds_for(Sink.AMOUNT)
        res = {"arg": arg, "sink": Sink.AMOUNT.value, "value": _short(value, 40), "allowed_kinds": [k.value for k in allowed]}
        x = _to_float(value)
        if x is None:
            return {**res, "provenance": [], "verdict": REFUSED, "code": "SLOT_MISMATCH", "why": f"amount '{value}' is not a number"}
        tol = amount_tolerance(x)
        pools: dict[SlotKind, list[tuple[float, Provenance]]] = {}

        def add(kind: SlotKind, text: str, prov: Provenance) -> None:
            for n in _numbers(text):
                pools.setdefault(kind, []).append((n, prov))

        add(SlotKind.USER_LITERAL, state.query, Provenance(SlotKind.USER_LITERAL, "USER", None, "user_request", state.query))
        tainted: list[Provenance] = []
        for sp in state.ledger.spans:
            if sp.label == DIRECTIVE:
                if any(abs(n - x) <= tol for n in _numbers(sp.text)):
                    tainted.append(Provenance(None, DIRECTIVE, sp.id, sp.tool, sp.text))
                continue
            prov_kind = SlotKind.TRUSTED if sp.trusted_source else SlotKind.LOOKUP
            add(prov_kind, sp.text, Provenance(prov_kind, "EVIDENCE", sp.id, sp.tool, sp.text))
            if sp.delegated:
                add(SlotKind.DELEGATED, sp.text, Provenance(SlotKind.DELEGATED, "EVIDENCE", sp.id, sp.tool, sp.text))
        for kind in (SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED):
            if kind not in allowed:
                continue
            for n, prov in pools.get(kind, []):
                if abs(n - x) <= tol:
                    return {**res, "provenance": [prov.as_dict()], "verdict": ALLOWED, "kind": kind.value,
                            "why": f"warrant: {kind.value} ({prov.tool or 'user'})"}
        if SlotKind.DERIVED in allowed:
            known = [n for kind in (SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED)
                     if kind in allowed for n, _ in pools.get(kind, [])]
            # the user's own numbers first, so their arithmetic wins over coincidences in documents
            expr = derive_amount(x, list(dict.fromkeys(known)))
            if expr is not None:
                return {**res, "provenance": [{"kind": "DERIVED", "label": "DERIVED", "span": None, "tool": None, "excerpt": expr}],
                        "verdict": ALLOWED, "kind": SlotKind.DERIVED.value, "why": f"warrant: DERIVED ({expr})"}
        if tainted:
            return {**res, "provenance": [p.as_dict() for p in tainted[:2]], "verdict": REFUSED, "code": "TAINTED_VALUE",
                    "why": f"amount {x:g} only appears inside instruction-like text in untrusted data (span {tainted[0].span_id})"}
        return {**res, "provenance": [], "verdict": REFUSED, "code": "SLOT_MISMATCH",
                "why": f"amount {x:g} is not in the user's request, their records or the data read, and is not computed from them"}

    def _decide(self, state: WarrantState, call: FunctionCall) -> dict:
        pol = get_policy(call.function)
        c = state.contract
        decision: dict[str, Any] = {"tool": call.function, "effect": pol.effect.value, "checks": []}
        if pol.effect != Effect.READ and pol.effect not in c.allowed_effects:
            return {**decision, "decision": REFUSED, "code": "EFFECT_OUT_OF_SCOPE",
                    "reason": f"{pol.effect.value} is not in the Intent Contract (allowed: {', '.join(e.value for e in c.allowed_effects)})"}
        for arg, sink in pol.sinks.items():
            for v in _values(call.args.get(arg)):
                if sink == Sink.AMOUNT:
                    decision["checks"].append(self._check_amount(state, arg, v))
                else:
                    decision["checks"].append(self._check_value(state, call.function, arg, sink, v))
        # Content check: links / addresses / IBANs embedded in free-text arguments of
        # side-effecting calls must not originate only from injected instructions.
        if pol.effect != Effect.READ:
            for arg, v in call.args.items():
                if arg in pol.sinks or not isinstance(v, str):
                    continue
                for tok in set(_EMBEDDED_RE.findall(v)):
                    provs = state.ledger.provenance(tok, c.delegated_sources)
                    if provs and all(p.label == DIRECTIVE for p in provs):
                        decision["checks"].append({"arg": arg, "sink": "content", "value": tok, "allowed_kinds": [], "provenance": [p.as_dict() for p in provs[:2]],
                                                   "verdict": REFUSED, "code": "TAINTED_CONTENT",
                                                   "why": f"'{tok}' inside {arg} comes only from instruction-like text in untrusted data"})
        bad = [x for x in decision["checks"] if x["verdict"] != ALLOWED]
        if bad:
            worst = next((x for x in bad if x["verdict"] == REFUSED), bad[0])
            return {**decision, "decision": worst["verdict"], "code": worst["code"], "reason": worst["why"]}
        return {**decision, "decision": ALLOWED, "code": "OK", "reason": "every sink argument carries a warrant"}

    def _envelope(self, state: WarrantState, call: FunctionCall, effect: Effect, diff: list[dict]) -> tuple[bool, str]:
        lim = state.contract.limits
        if effect == Effect.EGRESS and state.egress + 1 > lim.max_egress_actions:
            return False, f"egress budget exceeded ({lim.max_egress_actions} per request)"
        if effect == Effect.PAY:
            amt = 0.0
            for key in ("amount",):
                try:
                    amt = float(call.args.get(key, 0) or 0)
                except (TypeError, ValueError):
                    amt = 0.0
            cap = getattr(lim, "max_amount_per_payment", None)
            if cap is not None and amt > float(cap) + 1e-6:
                return False, f"amount {amt:g} exceeds the per-payment limit {float(cap):g}"
            if state.payments + 1 > lim.max_payments:
                return False, f"payment count exceeded ({lim.max_payments} per request)"
            if lim.max_total_amount is not None and state.paid_total + amt > lim.max_total_amount + 1e-6:
                return False, f"amount {state.paid_total + amt:g} exceeds contract limit {lim.max_total_amount:g}"
        if effect == Effect.DELETE:
            removed = sum(1 for d in diff if d["op"] == "remove")
            if removed > lim.max_deletes or state.deletes + 1 > lim.max_deletes:
                return False, f"delete envelope exceeded ({lim.max_deletes})"
        if effect == Effect.READ and any(d["op"] in ("add", "remove") for d in diff):
            return False, "a READ tool tried to change state"
        return True, "within envelope"

    def _commit_counters(self, state: WarrantState, effect: Effect, call: FunctionCall) -> None:
        if effect == Effect.EGRESS:
            state.egress += 1
        elif effect == Effect.PAY:
            state.payments += 1
            try:
                state.paid_total += float(call.args.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass
        elif effect == Effect.DELETE:
            state.deletes += 1

    # ---------------- pipeline entry point ----------------
    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1].get("tool_calls"):
            return query, runtime, env, messages, extra_args
        state: WarrantState | None = extra_args.get("warrant")
        if state is None:  # no contract -> fail closed by compiling a rules-only one
            from .contract import compile_contract

            state = WarrantState(query=query, contract=compile_contract(query, list(runtime.functions.keys()), use_llm=False))
            extra_args = {**extra_args, "warrant": state}

        results = []
        for call in messages[-1]["tool_calls"]:
            state.seq += 1
            call_id = call.id or f"call_{state.seq}"
            for k, v in list(call.args.items()):
                if isinstance(v, str) and is_string_list(v):
                    try:
                        call.args[k] = literal_eval(v)
                    except Exception:
                        pass
            pol = get_policy(call.function)
            self.emit("tool_call", id=call_id, function=call.function, args=dict(call.args), effect=pol.effect.value)
            if call.function not in runtime.functions:
                results.append(self._tool_msg(call, call_id, "", f"Invalid tool {call.function} provided."))
                continue

            self.emit("stage", stage="gate", status="active")
            try:
                self._load_records(state, runtime, env)
                decision = self._decide(state, call)
            except Exception as exc:  # fail closed
                decision = {"tool": call.function, "effect": pol.effect.value, "checks": [], "decision": REFUSED, "code": "INTERNAL_ERROR", "reason": str(exc)}

            diff: list[dict] = []
            if decision["decision"] == ALLOWED and pol.effect != Effect.READ:
                self.emit("stage", stage="shadow", status="active")
                shadow_env = env.model_copy(deep=True)
                _, shadow_err = runtime.run_function(shadow_env, call.function, call.args)
                try:
                    diff = state_diff(env.model_dump(mode="json"), shadow_env.model_dump(mode="json"))
                except Exception:
                    diff = []
                ok, why = self._envelope(state, call, pol.effect, diff)
                self.emit("shadow", id=call_id, function=call.function, diff=[{k: _short(v) for k, v in d.items()} for d in diff[:12]], ok=ok, why=why, error=shadow_err)
                self.emit("stage", stage="shadow", status="done")
                if not ok:
                    decision = {**decision, "decision": REFUSED, "code": "ENVELOPE_EXCEEDED", "reason": why}

            state.decisions.append(decision)
            self.emit("gate", id=call_id, **decision)
            self.emit("stage", stage="gate", status="done")
            self._audit(state, {"type": "decision", "seq": state.seq, "tool": call.function, "args": dict(call.args), **{k: decision[k] for k in ("effect", "decision", "code", "reason")}, "checks": decision["checks"], "diff": diff[:8]})

            if decision["decision"] != ALLOWED:
                self.emit("stage", stage="refuse", status="active")
                verb = "needs the user's confirmation" if decision["decision"] == NEEDS_INPUT else "was blocked"
                msg = (
                    f"WARRANT_{decision['decision']} ({decision['code']}): this call {verb}. {decision['reason']}. "
                    f"The action was NOT executed. Do not retry it. Continue with the user's original request only: \"{state.contract.task}\"."
                )
                results.append(self._tool_msg(call, call_id, "", msg))
                self.emit("tool_result", id=call_id, function=call.function, text=msg, refused=True, spans=[], masked=0)
                self.emit("stage", stage="refuse", status="done")
                continue

            # ---- commit ----
            self.emit("stage", stage="commit", status="active")
            result, error = runtime.run_function(env, call.function, call.args)
            if not error:
                self._commit_counters(state, pol.effect, call)
            text = self.fmt(result) if not error else ""
            self.emit("stage", stage="commit", status="done")

            # ---- audit the output before the planner sees it ----
            self.emit("stage", stage="auditor", status="active")
            spans = audit_output(text, call_id, call.function, f"s{state.seq}", use_llm=self.auditor_llm, user_request=state.query) if text else []
            for sp in spans:
                sp.trusted_source = pol.trusted_source
            if state.contract.delegated_sources:
                mark_delegation(spans, f"{call.function} {json.dumps(dict(call.args), default=str)}\n{text}", state.contract.delegated_sources)
            state.ledger.add(spans)
            shown, n_masked = (mask_text(text, spans) if self.mask else (text, 0))
            self.emit("tool_result", id=call_id, function=call.function, text=shown, raw=text if n_masked else None, spans=[s.as_dict() for s in spans], masked=n_masked, error=error)
            self.emit("ledger", added=[s.as_dict() for s in spans])
            self.emit("stage", stage="auditor", status="done")
            results.append(self._tool_msg(call, call_id, shown, error))

        return query, runtime, env, [*messages, *results], extra_args

    @staticmethod
    def _tool_msg(call: FunctionCall, call_id: str, text: str, error: str | None) -> ChatToolResultMessage:
        return ChatToolResultMessage(role="tool", content=[text_content_block_from_string(text)], tool_call_id=call_id, tool_call=call, error=error)


class EmittingToolsExecutor(BasePipelineElement):
    """Plain (undefended) executor that only *reports* what happens, for the UI.

    It runs the rules-only auditor purely to highlight attacker text that reached
    the model; it never blocks or masks anything.
    """

    def __init__(self, emit: Emitter = NULL_EMITTER, output_formatter: Callable[[Any], str] = tool_result_to_str) -> None:
        self.emit = emit
        self.fmt = output_formatter
        self.n = 0

    def query(self, query, runtime, env=EmptyEnv(), messages=[], extra_args={}):
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1].get("tool_calls"):
            return query, runtime, env, messages, extra_args
        results = []
        for call in messages[-1]["tool_calls"]:
            self.n += 1
            call_id = call.id or f"call_{self.n}"
            pol = get_policy(call.function)
            self.emit("tool_call", id=call_id, function=call.function, args=dict(call.args), effect=pol.effect.value)
            if call.function not in runtime.functions:
                results.append(WarrantGate._tool_msg(call, call_id, "", f"Invalid tool {call.function} provided."))
                continue
            result, error = runtime.run_function(env, call.function, call.args)
            text = self.fmt(result) if not error else ""
            spans = audit_output(text, call_id, call.function, f"u{self.n}", use_llm=False) if text else []
            self.emit("tool_result", id=call_id, function=call.function, text=text, spans=[s.as_dict() for s in spans if s.label == DIRECTIVE], masked=0, error=error, executed=pol.effect != Effect.READ)
            results.append(WarrantGate._tool_msg(call, call_id, text, error))
        return query, runtime, env, [*messages, *results], extra_args


def verify_audit_log(path) -> dict:
    """Deterministic post-run checker over a JSONL audit log.

    Re-validates that every executed side effect carried a warrant whose kind the
    contract allows, and that nothing refused was executed.
    """
    lines = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    contract = IntentContract(**lines[0]["contract"])
    logged_hash = contract.hash
    problems, executed = [], 0
    if contract.model_copy(deep=True).freeze().hash != logged_hash:
        problems.append("contract was modified after it was frozen (hash mismatch)")
    for rec in lines[1:]:
        if rec.get("contract_hash") != logged_hash:
            problems.append(f"seq {rec.get('seq')}: decision made under a different contract")
        if rec.get("type") != "decision" or rec.get("effect") == "READ":
            continue
        if rec["decision"] == ALLOWED:
            executed += 1
            if rec["effect"] not in [e.value for e in contract.allowed_effects]:
                problems.append(f"seq {rec['seq']}: effect {rec['effect']} not in contract")
            for chk in rec.get("checks", []):
                if chk.get("verdict") != ALLOWED or chk.get("kind") not in chk.get("allowed_kinds", []):
                    problems.append(f"seq {rec['seq']}: {chk.get('arg')} lacks a valid warrant")
    return {"run_id": lines[0]["run_id"], "contract_hash": contract.hash, "executed_side_effects": executed, "ok": not problems, "problems": problems}
