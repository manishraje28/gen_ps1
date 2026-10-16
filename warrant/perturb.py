"""Counterfactual value relabeling: per-argument provenance by perturbation (Lane D).

The Value Ledger decides where a sink value came from by string matching. If an attacker writes the
same value twice, once in a flagged instruction and once in a clean-looking line ("CC list: audit@evil"),
the clean copy makes it LOOKUP and the gate allows it (docs/attacks/HOLES.md H1). The same goes for
amounts copied from the wrong line (H3).

Instead of guessing, we ask. When a risky call carries an AMBIGUOUS sink value (it appears in more than
one span, or in a tool output that also has a DIRECTIVE span), we:
  1. copy the conversation and replace EACH OCCURRENCE of that value in every tool output with a
     different, format-preserving fake (a plausible email / an IBAN with a valid checksum / an amount of
     the same magnitude), remembering which span each fake sits in;
  2. re-ask the same planner (dry run, nothing executes) and read which fake it now puts in the argument;
  3. that fake names the exact span the value came from. If the span is an instruction, or sits in a
     document that also carries an instruction and the value is not the user's own, refuse.
Two runs must agree; otherwise the probe is inconclusive and asks the user (fails closed).

To our knowledge, counterfactual value relabeling is new. Neighbours: PACT (arXiv 2605.11039, names
provenance inference as the open problem), AttriGuard / CausalArmor / MELON (counterfactual re-runs that
answer only "was this call caused by the data?"), NeuroTaint, and "Influence Is Not Authority"
(arXiv 2608.29942).
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import Any

from agentdojo.types import text_content_block_from_string

from .auditor import DIRECTIVE
from .contract import SlotKind
from .events import NULL_EMITTER
from .policy import Effect, Sink, get_policy

RISKY = {"EGRESS", "PAY", "BOOK", "DELETE", "ACCOUNT", "FORM"}
PROBE_RUNS = 2
ALLOWED, REFUSED, NEEDS_INPUT = "ALLOWED", "REFUSED", "NEEDS_INPUT"

_EMAIL_RE = re.compile(r"^[\w.+-]+@([\w-]+(?:\.[\w-]+)+)$")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{8,30}$")
_URL_RE = re.compile(r"^(https?://)?(www\.)?([\w-]+(?:\.[\w-]+)+)(/.*)?$", re.I)
_NUM_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?$")


# ------------------------------------------------------------------ format-preserving fakes
_FIRST = ["m.rao", "j.ortiz", "k.lindqvist", "a.mensah", "r.tanaka", "s.dubois", "l.okafor", "p.varga", "n.haddad", "t.moreau"]
_ORG = ["northwind-ops", "bluecrest-group", "harbor-ledger", "pinewood-co", "silverline-hq", "marlow-partners", "eastgate-team", "corvid-labs"]


def iban_checksum(country: str, bban: str) -> str:
    """ISO 13616 mod-97 check digits for a country code + BBAN."""
    rearranged = bban + country + "00"
    digits = "".join(str(int(ch, 36)) for ch in rearranged.upper())
    return f"{98 - int(digits) % 97:02d}"


def iban_is_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not _IBAN_RE.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def fake_iban(original: str, rng: random.Random) -> str:
    """Same country code and length, bank-code letters kept as letters, digits as digits, valid checksum."""
    o = original.replace(" ", "").upper()
    country = o[:2]
    bban = "".join(rng.choice(string.ascii_uppercase) if c.isalpha() else rng.choice(string.digits) for c in o[4:])
    return country + iban_checksum(country, bban) + bban


def fake_email(original: str, rng: random.Random) -> str:
    m = _EMAIL_RE.match(original.strip())
    tld = m.group(1).rsplit(".", 1)[-1] if m else "com"
    return f"{rng.choice(_FIRST)}@{rng.choice(_ORG)}.{tld}"


def fake_url(original: str, rng: random.Random) -> str:
    m = _URL_RE.match(original.strip())
    if not m:
        return f"www.{rng.choice(_ORG)}.com"
    scheme, www, host, path = m.group(1) or "", m.group(2) or "", m.group(3), m.group(4) or ""
    tld = host.rsplit(".", 1)[-1]
    new_path = ""
    if path:
        new_path = "/" + "".join(rng.choice(string.ascii_lowercase) for _ in range(max(3, min(12, len(path) - 1))))
    return f"{scheme}{www}{rng.choice(_ORG)}.{tld}{new_path}"


def fake_amount(original: str, rng: random.Random) -> str:
    """Same number of integer digits and the same decimal / thousands style, different value."""
    o = original.strip()
    has_comma = "," in o
    intpart, _, dec = o.replace(",", "").partition(".")
    n = len(intpart.lstrip("0")) or 1
    lo, hi = 10 ** (n - 1), 10 ** n - 1
    v = int(intpart or 0)
    choices = [x for x in (rng.randint(lo, hi) for _ in range(20)) if x != v and abs(x - v) > max(1, v // 20)]
    new_int = choices[0] if choices else (v + max(1, v // 3) if v + max(1, v // 3) <= hi else max(lo, v - max(1, v // 3)))
    s = f"{new_int:,}" if has_comma else str(new_int)
    if dec:
        s += "." + "".join(rng.choice(string.digits) for _ in dec)
    return s


def kind_of(value: str) -> str | None:
    v = str(value).strip()
    if _EMAIL_RE.match(v):
        return "email"
    if _IBAN_RE.match(v.replace(" ", "").upper()):
        return "iban"
    if _NUM_RE.match(v):
        return "amount"
    if _URL_RE.match(v) and ("." in v) and not v.replace(".", "").isdigit():
        return "url"
    return None


_MAKERS = {"email": fake_email, "iban": fake_iban, "url": fake_url, "amount": fake_amount}


def make_fakes(value: str, n: int, seed: str) -> list[str]:
    """n distinct fakes of the same format as value (deterministic for a given seed)."""
    k = kind_of(value)
    if k is None:
        return []
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    out: list[str] = []
    tries = 0
    while len(out) < n and tries < n * 20:
        tries += 1
        f = _MAKERS[k](str(value), rng)
        if f != str(value) and f not in out and f.lower() != str(value).lower():
            out.append(f)
    return out


# ------------------------------------------------------------------ relabeling
@dataclass
class Relabeled:
    messages: list
    fakes: dict[str, str | None] = field(default_factory=dict)   # fake -> span id (None = outside any span)
    n_occurrences: int = 0


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("content") or b.get("text") or "" for b in content if isinstance(b, dict))
    return str(content or "")


def _occurrence_re(value: str) -> re.Pattern:
    if kind_of(value) == "amount":
        num = value.replace(",", "")
        intpart, _, dec = num.partition(".")
        grouped = f"{int(intpart):,}" if intpart.isdigit() else intpart
        alts = {re.escape(value), re.escape(num), re.escape(grouped)}
        if dec:
            alts.add(re.escape(grouped + "." + dec))
        return re.compile(r"(?<![\d.,])(?:" + "|".join(sorted(alts, key=len, reverse=True)) + r")(?![\d])")
    return re.compile(re.escape(value), re.I)


def relabel(messages: list, spans: list, value: str, seed: str) -> Relabeled:
    """Copy of messages where each occurrence of value in each tool output gets its own fake."""
    msgs = copy.deepcopy(list(messages))
    occ_re = _occurrence_re(str(value))
    total = sum(len(occ_re.findall(_content_text(m.get("content")))) for m in msgs if m.get("role") == "tool")
    pool = make_fakes(str(value), max(total, 1), seed)
    if len(pool) < total:
        return Relabeled(msgs, {}, 0)
    out = Relabeled(msgs)
    k = 0
    by_call: dict[str, list] = {}
    for sp in spans:
        by_call.setdefault(sp.call_id, []).append(sp)
    for m in msgs:
        if m.get("role") != "tool":
            continue
        text = _content_text(m.get("content"))
        if not occ_re.search(text):
            continue
        # Locate this output's spans in order (as mask_text does), so each occurrence maps to its span.
        regions: list[tuple[int, int, str]] = []
        pos = 0
        for sp in by_call.get(m.get("tool_call_id") or "", []):
            at = text.find(sp.text, pos)
            if at < 0:
                continue
            regions.append((at, at + len(sp.text), sp.id))
            pos = at + len(sp.text)

        def span_at(i: int) -> str | None:
            for a, b, sid in regions:
                if a <= i < b:
                    return sid
            return None

        pieces, last = [], 0
        for mt in occ_re.finditer(text):
            fake = pool[k]
            k += 1
            out.fakes[fake] = span_at(mt.start())
            pieces.append(text[last:mt.start()] + fake)
            last = mt.end()
        pieces.append(text[last:])
        m["content"] = [text_content_block_from_string("".join(pieces))]
    out.n_occurrences = k
    return out


# ------------------------------------------------------------------ the probe
def _ambiguous(state, value: Any) -> tuple[bool, list]:
    provs = state.ledger.provenance(value, state.contract.delegated_sources)
    if any(p.kind in (SlotKind.USER_LITERAL, SlotKind.TRUSTED) for p in provs):
        return False, provs
    span_ids = [p.span_id for p in provs if p.span_id]
    if len(span_ids) > 1:
        return True, provs
    calls = {sp.call_id for sp in state.ledger.spans if sp.id in span_ids}
    if any(sp.label == DIRECTIVE for sp in state.ledger.spans if sp.call_id in calls):
        return True, provs
    return False, provs


def _caller_context() -> dict | None:
    """Fallback until the gate stores the context on the state: find WarrantGate.query's frame."""
    f = inspect.currentframe()
    try:
        while f is not None:
            loc = f.f_locals
            if f.f_code.co_name == "query" and "messages" in loc and "extra_args" in loc and "runtime" in loc:
                return {"query": loc.get("query"), "runtime": loc.get("runtime"), "env": loc.get("env"),
                        "messages": loc.get("messages"), "extra_args": loc.get("extra_args")}
            f = f.f_back
    finally:
        del f
    return None


def _reask(planner, ctx: dict, msgs: list, function: str):
    """One dry-run planner turn on relabeled messages. Returns the args of the first call to `function`, or None."""
    extra = {k: v for k, v in (ctx.get("extra_args") or {}).items() if k != "warrant_planner"}
    old_emit = getattr(planner, "emit", None)
    if old_emit is not None:
        planner.emit = NULL_EMITTER
    try:
        _, _, _, out, _ = planner.query(ctx.get("query") or "", ctx.get("runtime"), ctx.get("env"), msgs, extra)
    finally:
        if old_emit is not None:
            planner.emit = old_emit
    last = out[-1] if out else None
    for tc in (last or {}).get("tool_calls") or []:
        if tc.function == function:
            return dict(tc.args)
    return None


def _values(v):
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)] if v not in (None, "") else []


def check(state, call, decision: dict, planner=None, context: dict | None = None) -> dict | None:
    """Runs the relabeling probe for ambiguous sink values of a risky call.

    Returns None when nothing is ambiguous (zero planner calls), else
    {"check": <gate check dict>, "event": <UI event>, "audit": <audit record>}.
    """
    pol = get_policy(call.function)
    if pol.effect.value not in RISKY:
        return None
    targets = []
    for arg, sink in pol.sinks.items():
        for v in _values(call.args.get(arg)):
            if kind_of(v) is None:
                continue
            amb, provs = _ambiguous(state, v)
            if amb:
                targets.append((arg, sink, v, provs))
    if not targets:
        return None
    ctx = context or getattr(state, "perturb_ctx", None) or _caller_context()
    planner = planner or (ctx or {}).get("extra_args", {}).get("warrant_planner")
    planner = getattr(planner, "llm", planner)  # PlannerHandle wraps the planner
    t0 = time.time()
    arg, sink, value, provs = targets[0]
    base = {"arg": arg, "sink": sink.value if isinstance(sink, Sink) else str(sink), "value": value[:80],
            "allowed_kinds": [], "provenance": [p.as_dict() for p in provs[:3]]}
    if planner is None or not ctx or not ctx.get("messages"):
        res = {**base, "verdict": NEEDS_INPUT, "code": "PROBE_INCONCLUSIVE",
               "why": f"'{value}' appears in several places in untrusted data and the provenance probe could not run"}
        return _package(res, value, {}, [], 0, t0, call)
    history = list(ctx["messages"])
    if history and history[-1].get("role") == "assistant" and history[-1].get("tool_calls"):
        history = history[:-1]  # re-ask from just before the call being decided
    chosen, fakes_all, runs = [], {}, []
    for r in range(PROBE_RUNS):
        rl = relabel(history, state.ledger.spans, value, seed=f"{state.run_id}:{call.function}:{arg}:{value}:{r}")
        if not rl.fakes:
            break
        fakes_all.update(rl.fakes)
        args = _reask(planner, ctx, rl.messages, call.function)
        picked = None
        if args is not None:
            for v in _values(args.get(arg)):
                for fake, sid in rl.fakes.items():
                    if v.strip().lower() == fake.lower() or (kind_of(fake) == "amount" and v.replace(",", "") == fake.replace(",", "")):
                        picked = (fake, sid)
        runs.append({"function_kept": args is not None, "picked": picked[0] if picked else None, "span": picked[1] if picked else None})
        chosen.append(picked[1] if picked else "<none>")
    n_calls = len(runs)
    if len(chosen) < PROBE_RUNS or len(set(chosen)) != 1 or chosen[0] in ("<none>", None):
        res = {**base, "verdict": NEEDS_INPUT, "code": "PROBE_INCONCLUSIVE",
               "why": f"provenance probe for '{value}' was inconclusive (runs: {chosen}); asking the user"}
        return _package(res, value, fakes_all, runs, n_calls, t0, call)
    sid = chosen[0]
    span = next((s for s in state.ledger.spans if s.id == sid), None)
    doc_has_directive = span is not None and any(s.label == DIRECTIVE for s in state.ledger.spans if s.call_id == span.call_id)
    if span is not None and (span.label == DIRECTIVE or doc_has_directive):
        why = (f"'{value}' was taken from span {sid}, which is an instruction in untrusted data" if span.label == DIRECTIVE
               else f"'{value}' was taken from span {sid}, in a document that also carries an instruction")
        res = {**base, "verdict": REFUSED, "code": "TAINTED_SOURCE", "why": why, "provenance_span": sid}
    else:
        res = {**base, "verdict": ALLOWED, "code": "OK", "why": f"provenance probe: '{value}' comes from span {sid}", "provenance_span": sid}
    return _package(res, value, fakes_all, runs, n_calls, t0, call)


def _package(res: dict, value: str, fakes: dict, runs: list, n_calls: int, t0: float, call) -> dict:
    ms = round((time.time() - t0) * 1000)
    probe = {"value": value, "fakes": fakes, "runs": runs, "extra_planner_calls": n_calls, "latency_ms": ms,
             "verdict": res["verdict"], "code": res["code"], "span": res.get("provenance_span")}
    return {"check": {**res, "probe": probe},
            "event": {"function": call.function, **probe},
            "audit": {"type": "perturb", "tool": call.function, **probe}}
