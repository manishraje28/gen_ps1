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
from .ledger import Provenance, ValueLedger, _text_has_value, mark_delegation, norm
from .policy import Effect, Sink, get_policy

ALLOWED, REFUSED, NEEDS_INPUT = "ALLOWED", "REFUSED", "NEEDS_INPUT"
# Refusals caused by tool-output data (the planner only gets a generic message for these, E5).
DATA_CODES = {"TAINTED_VALUE", "TAINTED_CONTENT", "TAINTED_DOCUMENT", "NO_PROVENANCE", "DELEGATION_SCOPE", "SLOT_MISMATCH",
              "SECRET_EGRESS", "NEW_PAYEE", "NEW_RECIPIENT", "NEW_LINK", "REPEATED_REFUSALS"}
QUIET_ENV = "WARRANT_QUIET_REFUSALS"
QUIET_STRIKES = 3
REFUSAL_STREAK_HALT = 3  # back-to-back repeated/duplicate calls before the loop stops the run
QUIET_MESSAGE = ("This action was not authorised by the user's request and was NOT executed. Do not retry it, not with other "
                 "wording, a different reference or a different format. Tell the user it was blocked and continue with the rest of the task.")


def config_flag(name: str, default: bool) -> bool:
    import os

    v = os.environ.get(name, "").strip().lower()
    return default if not v else v not in ("0", "false", "no", "off")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,30}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_URL_TOKEN_RE = re.compile(r"(?:https?://|www\.)", re.I)
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
    # Lane E. Documents the agent read (one per tool output item), keyed by Span.doc.
    docs: dict[str, "DocInfo"] = field(default_factory=dict)
    # Origin stamps (H8): identifier -> the warrant kind it was written into the user's world with during
    # a run (or CONFIRMED). Share one dict across turns so a value the agent wrote from a document can
    # never come back as TRUSTED by being read back from the user's own history.
    origins: dict[str, str] = field(default_factory=dict)
    refusals: dict[str, int] = field(default_factory=dict)  # data-caused refusal code -> count in this run
    blocked_effects: set = field(default_factory=set)       # effects refused for the rest of the run
    asked_user: int = 0  # NEEDS_INPUT outcomes (reported as their own category, not as failures)
    # Long tasks: fingerprints of calls already refused / already committed, the current run of
    # back-to-back refusals, and a halt flag the loop checks so a stuck planner cannot burn the budget.
    refused_calls: set = field(default_factory=set)
    done_calls: set = field(default_factory=set)
    refusal_streak: int = 0
    halted: str = ""

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = ValueLedger(self.query)


CONFIRMED = "CONFIRMED"


def call_fingerprint(call: FunctionCall) -> str:
    return call.function + json.dumps(dict(call.args), sort_keys=True, default=str)


# Refusals caused by *who / where* (payee, recipient, link): rewording the rest of the call does not
# change them, so they block any retry to the same party. Others (body content, secrets, limits) only
# block the exact same call: the user may still legitimately send something else to that party.
SINK_CODES = {"SLOT_MISMATCH", "TAINTED_VALUE", "NO_PROVENANCE", "NEW_PAYEE", "NEW_LINK", "DELEGATION_SCOPE"}


def sink_fingerprint(call: FunctionCall) -> str | None:
    """Only the authority sinks (payee, amount, recipients, file ...), normalised. The planner rewording
    a payment reference or email subject is still the same action to the same party."""
    sinks = get_policy(call.function).sinks
    if not sinks:
        return None
    norm = lambda v: sorted(origin_key(x) for x in v) if isinstance(v, (list, tuple)) else origin_key(v)
    picked = {k: norm(v) for k, v in call.args.items() if k in sinks and v not in (None, "", [])}
    return "sink:" + call.function + json.dumps(picked, sort_keys=True, default=str) if picked else None


def origin_key(value: Any) -> str:
    v = str(value).strip()
    return re.sub(r"\s+", "", v).upper() if _IBAN_RE.fullmatch(re.sub(r"\s+", "", v).upper()) else v.lower()


def confirm_value(state: WarrantState, value: Any, by: str = "user", audit_dir=None, emit: Emitter = NULL_EMITTER) -> None:
    """The user confirmed a new payee / recipient (answering a NEEDS_INPUT). The confirmation is
    stamped on the value, so it counts as the user's own record from now on, and it is logged."""
    state.origins[origin_key(value)] = CONFIRMED
    append_audit(audit_dir or config.AUDIT_DIR, state, {"type": "confirmation", "value": str(value), "by": by}, emit)


@dataclass
class DocInfo:
    """One document inside a tool output (a single email, file or list item)."""
    key: str
    tool: str
    text: str
    sender: str = ""      # email sender, if the document has one
    handle: str = ""      # workspace sender handle ("sender: Bob" in a chat message), set by the platform
    owner: str = ""       # owner of a file ("owner: ..."), if the document has one
    name: str = ""        # file name / title, if any
    secret: str = ""      # why it is confidential ("" = not)
    labelled: set = field(default_factory=set)  # numbers written next to a due-amount label


_ITEM_RE = re.compile(r"^- ", re.M)
_SENDER_RE = re.compile(r"^\s*(?:-\s+)?(?:sender|from)\s*:\s*['\"]?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.I | re.M)
_OWNER_RE = re.compile(r"^\s*(?:-\s+)?owner\s*:\s*['\"]?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.I | re.M)
_HANDLE_RE = re.compile(r"^\s*(?:-\s+)?sender\s*:\s*['\"]?([A-Za-z][\w .'-]{0,40}?)['\"]?\s*$", re.I | re.M)
_DOCNAME_RE = re.compile(r"^\s*(?:-\s+)?(?:filename|file_name|name|title|subject)\s*:\s*['\"]?([^\n'\"]{1,120})", re.I | re.M)
# Confidential sources (H5, H6): a flagged file, HR / payroll / salary files, passwords and security codes.
_SECRET_NAME_RE = re.compile(r"payroll|salar(?:y|ies)|/hr\b|\bhr/|password|passcode|credential|secret", re.I)
_SECRET_TEXT_RE = re.compile(r"confidential\s*:\s*true|\bconfidential\b|\b(?:password|passcode|security code|verification code|one[- ]time (?:code|password)|otp)\b", re.I)
_SECRET_WORDS = ("payroll", "salary", "salaries", "password", "passcode", "security code", "verification code", "confidential")



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


# ---------------- H10: free text inside trusted outputs ----------------
# A trusted tool (the user's bank history, contacts) vouches for its structured fields only: who was paid,
# how much, a contact's name / email / IBAN. Free-text fields are written by whoever created the entry
# (an incoming transaction's subject is chosen by its sender), so they are ordinary data: LOOKUP, audited.
FREE_TEXT_FIELDS = {"subject", "note", "notes", "memo", "message", "messages", "body", "description", "content", "text",
                    "comment", "comments", "reference", "purpose", "details", "bio", "about", "review", "reviews", "summary"}
_KEY_LINE_RE = re.compile(r"^(\s*(?:-\s+)?)([A-Za-z_][\w-]*)\s*:(?:\s|$)")


def _yaml_load(text: str) -> Any:
    try:
        import yaml  # noqa: PLC0415

        return yaml.safe_load(text)
    except Exception:
        return None


def _free_values(obj: Any, out: list[str]) -> list[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FREE_TEXT_FIELDS and v not in (None, ""):
                out.append(v if isinstance(v, str) else json.dumps(v, default=str))
            else:
                _free_values(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _free_values(x, out)
    return out


def _free_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of free-text field values in a YAML-like output: a free-text key line and every
    following line that is not a key of the same or a shallower item (multi-line scalars)."""
    out: list[tuple[int, int]] = []
    pos, cur, indent = 0, None, -1
    for line in text.splitlines(keepends=True):
        m = _KEY_LINE_RE.match(line)
        ind = len(m.group(1).replace("-", " ")) if m else -1
        if m and (cur is None or ind <= indent):
            if cur is not None:
                out.append((cur, pos))
                cur = None
            if m.group(2).lower() in FREE_TEXT_FIELDS:
                cur, indent = pos, ind
        pos += len(line)
    if cur is not None:
        out.append((cur, pos))
    return out


def split_free_text(text: str) -> list[str]:
    """The free-text field values of a trusted output (parsed YAML when possible, else by key lines)."""
    data = _yaml_load(text)
    if isinstance(data, (dict, list)):
        return _free_values(data, [])
    return [text[a:b] for a, b in _free_ranges(text)]


def strip_free_text(text: str) -> str:
    """The output without its free-text fields (what a trusted record may vouch for)."""
    keep, last = [], 0
    for a, b in _free_ranges(text):
        keep.append(text[last:a])
        last = b
    keep.append(text[last:])
    out = "".join(keep)
    for v in split_free_text(text):  # values the line scan missed (flow-style YAML, odd quoting)
        if v.strip():
            out = out.replace(v, " ")
    return out


def untrust_free_text(text: str, spans: list) -> int:
    """H10: spans of a trusted output that lie in a free-text field (or repeat its text) lose TRUSTED."""
    ranges = _free_ranges(text)
    free = [norm(v) for v in split_free_text(text)]
    n, pos = 0, 0
    for sp in spans:
        at = text.find(sp.text, pos)
        if at >= 0:
            pos = at + len(sp.text)
        m = _KEY_LINE_RE.match(sp.text)
        if m and m.group(2).lower() not in FREE_TEXT_FIELDS:
            continue  # a structured field ("recipient: ...", "amount: ...") stays trusted
        body = norm(sp.text[m.end():] if m else sp.text)
        inside = at >= 0 and any(a <= at < b for a, b in ranges)
        if m is not None or inside or (len(body) >= 12 and any(body in f for f in free)):
            sp.trusted_source = False
            sp.reasons = sp.reasons + ["free text inside a trusted record (written by whoever created the entry)"]
            n += 1
    return n


# Trusted tools that describe the user themself, not their payees or contacts.
_SELF_TOOLS = {"get_user_info", "get_user_information", "get_logged_in_github_user_information",
               "get_shopping_account_user_information"}


def records_from_text(text: str, source: str) -> list[TrustedRecord]:
    """Splits a trusted tool output (YAML list or plain lines) into per-entry records. Free-text fields
    (subject, note, message...) are dropped first: an IBAN in a transaction subject is not a payee (H10)."""
    out = []
    text = strip_free_text(text)
    for ch in re.split(r"\n(?=- )|\n\s*\n", text):
        ibans = {m.upper() for m in _IBAN_RE.findall(ch)}
        emails = {m.lower() for m in _EMAIL_RE.findall(ch)}
        if ibans or emails:
            out.append(TrustedRecord(_name_tokens(ch), ibans, emails, source, ch.strip()[:200]))
    return out


# ---------------- amount binding ----------------
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_MAX_NUMS = 120  # bound the pairwise search for derived amounts
_MAX_NUMS_STEP2 = 40  # bound the three-number search


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
    steps: list[tuple[str, float, int, int]] = []
    for i, a in enumerate(nums):
        for j in range(i + 1, len(nums)):  # two different numbers: no "pay double" (a + a)
            b = nums[j]
            for expr, val in ((f"{a:g} + {b:g}", a + b), (f"|{a:g} - {b:g}|", abs(a - b)), (f"{a:g} x {b:g}", a * b),
                              (f"{a:g} / {b:g}", a / b if b else None), (f"{b:g} / {a:g}", b / a if a else None),
                              (f"{a:g}% of {b:g}", a * b / 100), (f"{b:g}% of {a:g}", a * b / 100)):
                if val is None:
                    continue
                if abs(val - target) <= tol:
                    return expr
                if i < _MAX_NUMS_STEP2 and j < _MAX_NUMS_STEP2:
                    steps.append((expr, val, i, j))
    # One more step with a third, different number, e.g. "19.5% of what we paid + a fee of 5.29".
    for expr, val, i, j in steps:
        for k, c in enumerate(nums[:_MAX_NUMS_STEP2]):
            if k in (i, j):
                continue
            if abs(val + c - target) <= tol:
                return f"({expr}) + {c:g}"
            if abs(abs(val - c) - target) <= tol:
                return f"|({expr}) - {c:g}|"
    return None


# Labels that bind a document number to "what is owed" (H3). Anything else in a document (a late-fee
# schedule, a surcharge, a reference number) is not an amount to pay unless the user names it.
_DUE_LABEL_RE = re.compile(
    r"\b(?:amount\s+(?:due|payable|owed)|total(?:\s+(?:due|payable|amount))?|grand\s+total|balance(?:\s+due)?|payable|"
    r"outstanding|rent|amount|due|price|cost|fee|invoice\s+(?:total|amount))\b", re.I)
_LABEL_LINE_RE = re.compile(r"\n\s*[A-Za-z][\w ()/.'-]{0,50}:")  # "Late payment schedule:" starts a new field
# The user asked for arithmetic (DERIVED is allowed only then, H4).
_COMPUTE_RE = re.compile(
    r"%|\b(?:difference|total|sum|split|percent(?:age)?|plus|minus|add(?:ing)?|subtract|includ(?:e|ing)|inclusive|half|double|"
    r"twice|remaining|rest|increase[sd]?|decrease[sd]?|less|deduct|combined|together|each|per|times|multipl\w*|divid\w*|"
    r"extra|additional|adjust\w*|accordingly|vat|tax|tip|interest|discount|share)\b", re.I)


def _context_words(query: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(query)} - _NAME_STOP - {"bill", "pay", "please", "using", "from"}


def labelled_numbers(text: str, query: str = "") -> set[float]:
    """Numbers in a document that sit next to a due-amount label (in the same field or sentence),
    or next to a word the user used in their request."""
    user_words = _context_words(query)
    out: set[float] = set()
    for m in _NUM_RE.finditer(text):
        start = max(0, m.start() - 120)
        before = text[start:m.start()]
        cut = max([before.rfind(ch) for ch in ".!?"] + [x.start() for x in _LABEL_LINE_RE.finditer(before)] + [before.rfind("\n\n")])
        ctx = before[cut + 1:] if cut >= 0 else before
        ctx_words = {w.lower() for w in _WORD_RE.findall(ctx)}
        if _DUE_LABEL_RE.search(ctx) or ctx_words & user_words:
            f = _to_float(m.group(0))
            if f is not None:
                out.add(f)
    return out


def _split_docs(text: str) -> list[tuple[int, str]]:
    """A YAML list output is one document per top-level item; anything else is one document."""
    starts = [m.start() for m in _ITEM_RE.finditer(text)]
    if len(starts) < 2 or starts[0] != 0:
        return [(0, text)]
    bounds = [*starts, len(text)]
    return [(a, text[a:b]) for a, b in zip(bounds, bounds[1:])]


# Values distinctive enough to identify confidential content: long numbers (not years), account
# fragments such as XXXX4471, and codes mixing letters and digits.
_SECRET_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]{3,}(?:\.\d+)?(?![\w])")
_SECRET_CODE_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{3,}\b")


def secret_values(text: str) -> set[str]:
    out = set()
    for m in _SECRET_NUM_RE.findall(text):
        digits = m.replace(",", "").split(".")[0]
        if len(digits) >= 5 or (len(digits) == 4 and not re.fullmatch(r"(?:19|20)\d\d", digits)):
            out.add(digits)
    out |= {m.lower() for m in _SECRET_CODE_RE.findall(text) if not re.fullmatch(r"(?:19|20)\d\d|[a-z]+\d|q\d|fy\d+|l\d", m.lower())}
    return out


def _short(v: Any, n: int = 160) -> Any:
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + "…"


def append_audit(audit_dir, state: WarrantState, record: dict, emit: Emitter = NULL_EMITTER) -> None:
    """Appends one record to the run's JSONL audit log (the first line is the frozen contract)."""
    record = {"ts": time.time(), "run_id": state.run_id, "contract_hash": state.contract.hash,
              "policy_source": getattr(state.contract, "policy_source", "") or "", **record}
    path = audit_dir / f"{state.run_id}.jsonl"
    if not path.exists():
        head = {"ts": time.time(), "run_id": state.run_id, "type": "contract", "query": state.query, "contract": state.contract.model_dump(mode="json")}
        path.write_text(json.dumps(head, default=str) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    emit("audit", record={k: _short(v, 300) for k, v in record.items()})


# ---------------- final-answer guard ----------------
_ANSWER_VALUE_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'`)\]]+"          # links
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                # email addresses
    r"|\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}\b"   # IBANs, also printed in groups of four
)
_REDACT_LABEL = {"url": "link", "email": "email address", "iban": "IBAN"}


def _value_type(v: str) -> str:
    if "@" in v and not v.lower().startswith(("http", "www")):
        return "email"
    if v.lower().startswith(("http", "www")):
        return "url"
    return "iban"


def redact_answer(text: str, state: WarrantState) -> tuple[str, list[dict]]:
    """Removes links, email addresses and IBANs from the agent's final answer unless they carry a
    warrant: the user typed them, or they appear in data the agent read that was NOT flagged as
    instructions. This stops an injection from reaching the user through the answer itself
    (a phishing link, a "call this number / pay this IBAN" message, or a data-exfiltration URL)."""
    redactions: list[dict] = []

    def judge(m: re.Match) -> str:
        v = m.group(0).rstrip(".,;:!?")
        tail = m.group(0)[len(v):]
        if _text_has_value(state.query, v):
            return m.group(0)
        seen = [sp for sp in state.ledger.spans if _text_has_value(sp.text, v)]
        if any(sp.label != DIRECTIVE for sp in seen):
            return m.group(0)
        kind = _value_type(v)
        why = (f"only appears in instruction-like text (span {seen[0].id})" if seen
               else "does not appear in your request or in any data the agent read")
        redactions.append({"value": v, "type": kind, "why": why})
        return f"[{_REDACT_LABEL[kind]} removed by WARRANT: {why.split(' (')[0]}]" + tail

    return _ANSWER_VALUE_RE.sub(judge, text), redactions


class AnswerGuard(BasePipelineElement):
    """Last pipeline element: checks the final answer before the user sees it.

    Place it after the ToolsExecutionLoop of a WARRANT pipeline. Without a WarrantState
    (no contract) it redacts every link, address and IBAN that the user did not type: fail closed.
    """

    def __init__(self, emit: Emitter = NULL_EMITTER, audit_dir=None) -> None:
        self.emit = emit
        self.audit_dir = audit_dir or config.AUDIT_DIR

    def query(self, query, runtime, env=EmptyEnv(), messages=[], extra_args={}):
        if not messages or messages[-1]["role"] != "assistant" or messages[-1].get("tool_calls"):
            return query, runtime, env, messages, extra_args
        state: WarrantState | None = extra_args.get("warrant")
        if state is None:
            from .contract import compile_contract

            state = WarrantState(query=query, contract=compile_contract(query, list(runtime.functions.keys()), use_llm=False))
        last = messages[-1]
        blocks, found = [], []
        for block in last.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("content"), str):
                new_text, red = redact_answer(block["content"], state)
                found += red
                block = {**block, "content": new_text}
            blocks.append(block)
        if not found:
            return query, runtime, env, messages, extra_args
        self.emit("answer_guard", redactions=found)
        append_audit(self.audit_dir, state, {"type": "answer", "decision": "REDACTED", "code": "UNWARRANTED_IN_ANSWER",
                                             "redactions": found}, self.emit)
        return query, runtime, env, [*messages[:-1], {**last, "content": blocks}], extra_args


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
        append_audit(self.audit_dir, state, record, self.emit)

    # ---------------- documents (Lane E) ----------------
    @staticmethod
    def label_read(state: WarrantState, tool: str, args: dict, text: str, spans: list) -> None:
        """Splits a tool output into documents (one per list item) and records, per document, its
        sender, its name, whether it is confidential and which numbers carry a due-amount label.
        Each span is tagged with its document (Span.doc) and secrecy (Span.secret)."""
        if not spans:
            return
        call = spans[0].call_id
        arg_name = " ".join(str(v) for k, v in (args or {}).items() if isinstance(v, (str, int)) and k not in ("query",))
        docs = []
        for k, (start, body) in enumerate(_split_docs(text)):
            sender = (_SENDER_RE.search(body) or [None, ""])[1].lower()
            handle = (_HANDLE_RE.search(body) or [None, ""])[1].strip()
            if handle.lower() == "me" or _IBAN_RE.fullmatch(handle):
                handle = ""
            name = (_DOCNAME_RE.search(body) or [None, ""])[1].strip() or (arg_name if len(_split_docs(text)) == 1 else "")
            secret = ""
            hit = _SECRET_NAME_RE.search(name) or _SECRET_TEXT_RE.search(body)
            if hit:
                secret = hit.group(0)
            owner = (_OWNER_RE.search(body) or [None, ""])[1].lower()
            info = DocInfo(key=f"{call}#{k}", tool=tool, text=body, sender=sender, handle=handle, owner=owner, name=name, secret=secret,
                           labelled=labelled_numbers(body, state.query))
            state.docs[info.key] = info
            docs.append((start, info))
        pos = 0
        for sp in spans:
            at = text.find(sp.text, pos)
            if at < 0:
                at = pos
            else:
                pos = at + len(sp.text)
            info = [d for s, d in docs if s <= at][-1]
            sp.doc, sp.secret = info.key, bool(info.secret)

    @staticmethod
    def _span(state: WarrantState, span_id: str | None):
        return next((sp for sp in state.ledger.spans if sp.id == span_id), None) if span_id else None

    @staticmethod
    def _doc_tainted(state: WarrantState, doc: str) -> bool:
        return bool(doc) and any(sp.doc == doc and sp.label == DIRECTIVE for sp in state.ledger.spans)

    def _doc_of(self, state: WarrantState, p: Provenance) -> str:
        sp = self._span(state, p.span_id)
        return (sp.doc or sp.call_id) if sp is not None else ""

    @staticmethod
    def _stamped(state: WarrantState, value: Any) -> str:
        """The origin stamp of a value written during a run ('' if none). LOOKUP / DELEGATED stamps
        mean the user's own history cannot vouch for it."""
        return state.origins.get(origin_key(value), "")

    def _record_domains(self, state: WarrantState) -> set[str]:
        return {_domain(e) for r in self._known_records(state) for e in r.emails
                if self._stamped(state, e) not in (SlotKind.LOOKUP.value, SlotKind.DELEGATED.value)}

    def _record_names(self, state: WarrantState) -> list[set[str]]:
        """Names of the user's payees / contacts (from name fields of their records)."""
        out = []
        for r in self._known_records(state):
            if r.source in _SELF_TOOLS:
                continue  # the user's own name is no witness for a payee ("given that I am Emma Johnson, ...")
            for m in re.finditer(r"(?:^|\n)\s*-?\s*(?:name|recipient_name|payee|first_name|last_name)\s*:\s*['\"]?([^\n'\"]+)", r.text, re.I):
                toks = _name_tokens(m.group(1))
                if toks:
                    out.append(toks)
        return out

    @staticmethod
    def _principal(doc: DocInfo | None, domains: set[str]) -> str:
        """The known principal a document comes from ('' if none): a sender whose email domain is in the
        user's records, or a workspace member's handle (chat messages carry a platform-set sender). Web
        pages, files and other sender-less documents have none."""
        if doc is None:
            return ""
        if doc.sender and _domain(doc.sender) in domains:
            return doc.sender
        return doc.handle

    def _bound_names(self, state: WarrantState, domains: set[str]) -> set[str]:
        """Names the user typed, plus names in clean text from known principals ("can you invite Dora")."""
        names = _name_tokens(_LINK_RE.sub(" ", state.query))
        for sp in state.ledger.spans:
            if sp.label != DIRECTIVE and not sp.trusted_source and self._principal(state.docs.get(sp.doc), domains):
                names |= _name_tokens(_LINK_RE.sub(" ", sp.text))
        return {re.sub(r"'s$", "", n) for n in names}

    @staticmethod
    def _value_tokens(sink: Sink, value: Any) -> set[str]:
        v = str(value).strip().lower()
        if sink == Sink.URL:
            host = re.sub(r"^(?:https?://)?(?:www\.)?", "", v).split("/")[0]
            parts = host.split(".")[:-1]
        else:
            parts = [v.split("@", 1)[0]] if "@" in v else [v]
        toks = {t for p in parts for t in re.split(r"[^a-z]+", p) if len(t) >= 3}
        return toks - _NAME_STOP - {"www", "http", "https", "website", "site", "blog", "page", "mail", "gmail"}

    def _witness(self, state: WarrantState, sink: Sink, value: Any, provs: list[Provenance], principals: bool = False) -> str | None:
        """Two-Witness (H2, H11): one independent principal, other than the document that supplies the
        value, must vouch for it. Counted by principal, not by document: repeats inside a document,
        or several documents from the same unknown sender, are still no witness. Returns why the
        value is witnessed, or None. With ``principals`` (H11: links and new recipients taken from
        clean data) a value written by a known principal also counts, as does one bound to a name the
        user (or a known principal) gave; a link on a web page vouches for nothing."""
        domains = self._record_domains(state) | {d.lower() for d in state.contract.limits.internal_domains}
        if sink in (Sink.RECIPIENT, Sink.URL):
            if sink == Sink.RECIPIENT:
                dom = _domain(str(value))
                if dom and dom in domains:
                    return f"its domain {dom} is already in your records"
            else:
                host = re.sub(r"^(?:https?://)?(?:www\.)?", "", str(value).lower()).split("/")[0]
                if host and any(host == d or host.endswith("." + d) for d in domains):
                    return f"{host} is already in your records"
            if not principals:
                return None
            v = str(value).strip().lower()
            for p in provs:
                sp = self._span(state, p.span_id)
                if sp is None or sp.label == DIRECTIVE or sp.trusted_source:
                    continue
                doc = state.docs.get(sp.doc)
                who = self._principal(doc, domains)
                if who:
                    return f"it was written by {who}, a known principal"
                if doc is not None and doc.owner and _domain(doc.owner) in domains and                         _text_has_value(strip_free_text(doc.text), value):
                    # e.g. the user's own file lists the person under shared_with; its free-text content does not count
                    return f"it is in a structured field of a document owned by {doc.owner}"
                senders = {doc.sender, doc.handle.lower()} if doc is not None else                     {m.lower() for m in _SENDER_RE.findall(p.excerpt)} | {m.lower() for m in _HANDLE_RE.findall(p.excerpt)}
                if sink == Sink.RECIPIENT and v in senders - {""}:
                    return "it is the document's own sender (a reply)"
            # Name binding is for addresses only: a link's host words prove nothing ("true-informations").
            hit = self._value_tokens(sink, value) & self._bound_names(state, domains) if sink == Sink.RECIPIENT else set()
            if hit:
                return f"it is bound to the name '{sorted(hit)[0]}' given by you or a known principal"
            return None
        names = self._record_names(state)
        for p in provs:
            sp = self._span(state, p.span_id)
            if sp is not None and sp.trusted_source:
                continue  # the user's history holding a value the agent wrote there (origin stamp) is no witness
            doc = state.docs.get(sp.doc) if sp is not None else None
            text = doc.text if doc is not None else p.excerpt
            toks = _name_tokens(text)
            # (a) the payee's name or email domain is in the user's own records
            for nm in names:
                if nm <= toks:
                    return f"the payee '{' '.join(sorted(nm))}' is in your records"
            for e in _EMAIL_RE.findall(text):
                if _domain(e) in domains and (doc is None or _domain(e) != _domain(doc.sender) or doc.sender == ""):
                    return f"the payee's domain {_domain(e)} is in your records"
            # (b) the document's sender is a known principal whose domain names the payee
            if doc is not None and doc.sender and _domain(doc.sender) in domains:
                label = _domain(doc.sender).split(".")[0]
                if any(len(t) >= 4 and t in label for t in toks):
                    return f"the document comes from {doc.sender}, a sender already in your records, and names the payee"
        return None

    # ---------------- per-argument warrant check ----------------
    def _check_value(self, state: WarrantState, tool: str, arg: str, sink: Sink, value: Any) -> dict:
        c = state.contract
        allowed = c.kinds_for(sink)
        provs: list[Provenance] = state.ledger.provenance(value, c.delegated_sources)
        if self._stamped(state, value) in (SlotKind.LOOKUP.value, SlotKind.DELEGATED.value):
            # Origin stamp (H8): the agent itself wrote this value into the user's world from a document,
            # so reading it back from their history is not a TRUSTED source.
            provs = [Provenance(SlotKind.LOOKUP, p.label, p.span_id, p.tool, p.excerpt) if p.kind == SlotKind.TRUSTED else p for p in provs]
        if sink == Sink.URL:
            # A trusted output vouches for the user's payees and contacts, not for links: a link in a channel
            # name or in a record's text is ordinary data (H11).
            provs = [Provenance(SlotKind.LOOKUP, p.label, p.span_id, p.tool, p.excerpt) if p.kind == SlotKind.TRUSTED else p for p in provs]
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
            if sink in (Sink.IBAN, Sink.RECIPIENT, Sink.URL) and match.kind == SlotKind.LOOKUP:
                held = self._taint_check(state, sink, value, good)
                if held is not None:
                    return {**res, **held}
            if (sink in (Sink.IBAN, Sink.RECIPIENT) and match.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED)) or \
                    (sink == Sink.URL and match.kind == SlotKind.LOOKUP):
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
                schema = fn.parameters.model_json_schema()
                required = schema.get("required") or []
                props = schema.get("properties", {})
                # A search over the user's own records ("search_contacts(query)") with an empty query lists them all.
                if any(props.get(r, {}).get("type") != "string" for r in required):
                    continue
                result, err = runtime.run_function(env.model_copy(deep=True), name, {r: "" for r in required})
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

    def _in_records(self, state: WarrantState, sink: Sink, value: Any) -> bool:
        """The value is one of the user's own payees / contacts (or they confirmed it). A value the
        agent wrote into their world from a document during a run does not count (origin stamp)."""
        stamp = self._stamped(state, value)
        if stamp == CONFIRMED:
            return True
        if stamp in (SlotKind.LOOKUP.value, SlotKind.DELEGATED.value):
            return False
        key = origin_key(value)
        return key in {x for r in self._known_records(state) for x in (r.ibans if sink == Sink.IBAN else r.emails)}

    def _taint_check(self, state: WarrantState, sink: Sink, value: Any, good: list[Provenance]) -> dict | None:
        """Document taint (H1): if a document contains instruction-like text, none of its addresses,
        IBANs or links is a LOOKUP warrant on its own: the attacker may have written the value
        twice, once in the flagged instruction and once in a clean-looking line. The value is
        still fine if a clean document also has it, if it is the user's own record, if it is the
        poisoned document's own sender (a reply), or if an independent witness vouches for it."""
        if any(p.kind in (SlotKind.USER_LITERAL, SlotKind.TRUSTED) for p in good):
            return None
        lookups = [p for p in good if p.kind == SlotKind.LOOKUP]
        tainted = [p for p in lookups if self._doc_tainted(state, self._doc_of(state, p))]
        if not tainted or len(tainted) < len(lookups):
            return None
        if self._in_records(state, sink, value):
            return None
        docs = [state.docs.get(self._doc_of(state, p)) for p in tainted]
        if sink == Sink.RECIPIENT and any(d is not None and d.sender == str(value).strip().lower() for d in docs):
            return None
        if self._witness(state, sink, value, tainted) is not None:
            return None
        flagged = next((sp for sp in state.ledger.spans if sp.doc == self._doc_of(state, tainted[0]) and sp.label == DIRECTIVE), None)
        return {"verdict": NEEDS_INPUT, "code": "TAINTED_DOCUMENT", "kind": "LOOKUP",
                "why": f"'{value}' only comes from a document that also contains instruction-like text"
                       f"{f' (span {flagged.id})' if flagged else ''}; it is not typed by you, not in your records and no "
                       f"independent source vouches for it. Confirm with the user before using it"}

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
        if sink == Sink.URL:
            return self._new_value_witness(state, sink, value, good, "NEW_LINK")
        records = self._known_records(state)
        if self._in_records(state, sink, value):
            return None  # already one of the user's own payees / contacts (or confirmed by them)
        doc_tokens: set[str] = set()
        for p in good:
            if p.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED):
                doc_tokens |= _name_tokens(p.excerpt)
        for r in records:
            theirs = r.ibans if is_iban else r.emails
            shared = sorted(doc_tokens & r.tokens)
            if theirs and shared and key not in theirs:
                return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                        "why": f"'{v}' comes from a document that names '{' '.join(shared[:3])}', but your own records ({r.source}) "
                               f"have {', '.join(sorted(theirs)[:2])} for them. Possible impersonation: confirm with the user before using the new one"}
        if is_iban and getattr(c, "confirm_new_payees", None) is True:
            return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                    "why": f"'{v}' is a new payee (not typed by the user, not in their records); the organisation policy asks for confirmation"}
        if is_iban:
            # Two-Witness (H2): a payee taken from a document needs one independent witness.
            docs = [p for p in good if p.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED)]
            if self._witness(state, Sink.IBAN, value, docs) is None:
                senders = sorted({d.sender for p in docs if (d := state.docs.get(self._doc_of(state, p)))
                                  is not None and d.sender})
                stamp = " It was paid earlier by the agent from a document, which does not make it a known payee." \
                    if self._stamped(state, value) else ""
                return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                        "why": f"'{v}' is a new payee: not typed by you and not in your records, and no independent witness vouches for it "
                               f"(missing: the payee's name or domain in your contacts or payment history, or a known sender "
                               f"{'(sender ' + ', '.join(senders) + ' is unknown)' if senders else '(the document has no sender)'}).{stamp} "
                               f"Confirm with the user before paying"}
        if not is_iban:
            held = self._new_value_witness(state, sink, value, good, code)
            if held is not None:
                return held
        if not is_iban and getattr(c, "confirm_new_recipients", None) is True:
            if _domain(v) not in [d.lower() for d in c.limits.internal_domains]:
                return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                        "why": f"'{v}' is a new external recipient (not typed by the user, not a contact); the contract asks for confirmation"}
        return None

    @staticmethod
    def _host_seen(state: WarrantState, link: str) -> bool:
        """The link's host appears in the request or in data read (a reformatted link, "https://x.com/" vs "x.com")."""
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", link.lower()).split("/")[0]
        return bool(host) and (host in state.query.lower() or any(host in sp.text.lower() for sp in state.ledger.spans))

    def _new_value_witness(self, state: WarrantState, sink: Sink, value: Any, good: list[Provenance], code: str) -> dict | None:
        """Two-Witness for links and new recipients (H11): a value taken only from data needs a witness."""
        docs = [p for p in good if p.kind in (SlotKind.LOOKUP, SlotKind.DELEGATED)]
        if not docs or self._witness(state, sink, value, docs, principals=True) is not None:
            return None
        what = "link" if sink == Sink.URL else "recipient"
        return {"verdict": NEEDS_INPUT, "code": code, "kind": "LOOKUP",
                "why": f"'{_short(value, 80)}' is a new {what} taken from data: not typed by you, not in your records, not written by a "
                       f"known sender and not bound to a name you gave (it comes from {docs[0].tool}). Confirm with the user before using it"}

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
        unlabelled: list[Provenance] = []
        for sp in state.ledger.spans:
            if sp.label == DIRECTIVE:
                if any(abs(n - x) <= tol for n in _numbers(sp.text)):
                    tainted.append(Provenance(None, DIRECTIVE, sp.id, sp.tool, sp.text))
                continue
            if sp.trusted_source:
                add(SlotKind.TRUSTED, sp.text, Provenance(SlotKind.TRUSTED, "EVIDENCE", sp.id, sp.tool, sp.text))
                continue
            # A number from a document binds only if it sits next to a due-amount label or a word the
            # user used (H3): "Amount due: 4,250" yes, "Late payment schedule: 42,500" no.
            doc = state.docs.get(sp.doc)
            labels = doc.labelled if doc is not None else labelled_numbers(sp.text, state.query)
            nums = [n for n in _numbers(sp.text) if n in labels]
            if any(abs(n - x) <= tol for n in _numbers(sp.text) if n not in labels):
                unlabelled.append(Provenance(SlotKind.LOOKUP, "EVIDENCE", sp.id, sp.tool, sp.text))
            kinds = [SlotKind.LOOKUP] + ([SlotKind.DELEGATED] if sp.delegated else [])
            for kind in kinds:
                prov = Provenance(kind, "EVIDENCE", sp.id, sp.tool, sp.text)
                for n in nums:
                    pools.setdefault(kind, []).append((n, prov))
        for kind in (SlotKind.USER_LITERAL, SlotKind.TRUSTED, SlotKind.LOOKUP, SlotKind.DELEGATED):
            if kind not in allowed:
                continue
            for n, prov in pools.get(kind, []):
                if abs(n - x) <= tol:
                    return {**res, "provenance": [prov.as_dict()], "verdict": ALLOWED, "kind": kind.value,
                            "why": f"warrant: {kind.value} ({prov.tool or 'user'})"}
        # DERIVED only when the user asked for arithmetic, over the user's numbers, their records and
        # labelled document numbers: "total payable is base plus surcharge" in a bill does not count (H4).
        # A delegation ("follow the instructions in the notice") hands the arithmetic to the delegated source.
        if SlotKind.DERIVED in allowed and (_COMPUTE_RE.search(state.query) or c.delegated_sources):
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
        if unlabelled:
            return {**res, "provenance": [p.as_dict() for p in unlabelled[:2]], "verdict": REFUSED, "code": "SLOT_MISMATCH",
                    "why": f"amount {x:g} appears in a document but not as the amount due (no 'amount due' / 'total' / 'balance' "
                           f"label next to it, and not named in your request) (span {unlabelled[0].span_id})"}
        return {**res, "provenance": [], "verdict": REFUSED, "code": "SLOT_MISMATCH",
                "why": f"amount {x:g} is not in the user's request, their records or the data read, and is not computed from them"}

    def _secret_check(self, state: WarrantState, call: FunctionCall, pol) -> list[dict]:
        q = state.query.lower()
        secrets: dict[str, list] = {}
        for sp in state.ledger.spans:
            if sp.secret:
                secrets.setdefault(sp.doc or sp.call_id, []).append(sp)
        if not secrets:
            return []
        public = norm(state.query) + " " + " ".join(norm(sp.text) for sp in state.ledger.spans if not sp.secret)
        out = []
        for doc_key, spans in secrets.items():
            doc = state.docs.get(doc_key)
            name = (doc.name if doc else "").strip().lower()
            stem = re.sub(r"\.[a-z0-9]{2,5}$", "", name.split("/")[-1])
            why_secret = (doc.secret if doc else "confidential").lower()
            # the user named this confidential source (its file name, or the kind of secret) in the request
            if (stem and len(stem) >= 4 and stem in q) or (why_secret and why_secret in q and why_secret != "confidential"):
                continue
            values = set()
            texts = []
            for sp in spans:
                values |= secret_values(sp.text)
                if len(norm(sp.text)) >= 16:
                    texts.append(norm(sp.text))
            values = {v for v in values if v not in public}
            texts = [t for t in texts if t not in public]
            for arg, v in call.args.items():
                for s in _values(v):
                    if not isinstance(s, str):
                        continue
                    ns = norm(s)
                    hits = sorted(v2 for v2 in values if v2 in ns) + [t[:30] for t in texts if t in ns]
                    if hits:
                        out.append({"arg": arg, "sink": "content", "value": _short(s, 60), "allowed_kinds": [], "provenance": [],
                                    "verdict": REFUSED, "code": "SECRET_EGRESS",
                                    "why": f"{arg} carries confidential data ({', '.join(hits[:3])}) from "
                                           f"{doc.name or doc.tool if doc else 'a confidential source'}, which your request did not name"})
                        break
        return out

    def _decide(self, state: WarrantState, call: FunctionCall) -> dict:
        pol = get_policy(call.function)
        c = state.contract
        decision: dict[str, Any] = {"tool": call.function, "effect": pol.effect.value, "checks": []}
        if pol.effect != Effect.READ and pol.effect not in c.allowed_effects:
            return {**decision, "decision": REFUSED, "code": "EFFECT_OUT_OF_SCOPE",
                    "reason": f"{pol.effect.value} is not in the Intent Contract (allowed: {', '.join(e.value for e in c.allowed_effects)})"}
        if pol.effect != Effect.READ and pol.effect.value in state.blocked_effects:
            return {**decision, "decision": REFUSED, "code": "REPEATED_REFUSALS",
                    "reason": f"{pol.effect.value} calls are blocked for the rest of this run after {QUIET_STRIKES} refusals of the same kind"}
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
                    elif pol.effect == Effect.EGRESS and _URL_TOKEN_RE.match(tok) and not provs and \
                            not self._host_seen(state, tok):
                        decision["checks"].append({"arg": arg, "sink": "content", "value": tok, "allowed_kinds": [], "provenance": [],
                                                   "verdict": NEEDS_INPUT, "code": "NEW_LINK", "kind": None,
                                                   "why": f"'{tok}' inside {arg} is a link that is not in your request or in any data read"})
                    elif pol.effect == Effect.EGRESS and _URL_TOKEN_RE.match(tok) and provs and \
                            not any(p.kind == SlotKind.USER_LITERAL for p in provs):
                        # H11: a link sent on ("send this link to Alice") needs the same witness as a link visited.
                        good = [Provenance(SlotKind.LOOKUP, p.label, p.span_id, p.tool, p.excerpt) for p in provs if p.kind is not None]
                        held = self._new_value_witness(state, Sink.URL, tok, good, "NEW_LINK")
                        if held is not None:
                            decision["checks"].append({"arg": arg, "sink": "content", "value": tok, "allowed_kinds": [],
                                                       "provenance": [p.as_dict() for p in provs[:2]], **held})
        # Secrecy (H5, H6): confidential values must not leave in free text (a body, a subject, a file
        # name, a URL's query string) unless the user named that confidential source in the request.
        if pol.effect != Effect.READ or any(s == Sink.URL for s in pol.sinks.values()):
            decision["checks"] += self._secret_check(state, call, pol)
        # --- perturbation hook (lane D) ---
        import os as _os  # noqa: PLC0415
        if _os.environ.get("WARRANT_PERTURB") == "1":
            from .perturb import check as _perturb_check  # noqa: PLC0415

            _probe = _perturb_check(state, call, decision, planner=getattr(state, "planner", None),
                                    context=getattr(state, "perturb_ctx", None))
            if _probe:
                decision["checks"].append(_probe["check"])
                self.emit("perturb", **_probe["event"])
                append_audit(self.audit_dir, state, _probe["audit"], self.emit)
        # --- end perturbation hook ---
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
            fp, sfp = call_fingerprint(call), sink_fingerprint(call)
            if state.halted:
                decision = {"tool": call.function, "effect": pol.effect.value, "checks": [], "decision": REFUSED, "code": "RUN_HALTED", "reason": state.halted}
            elif fp in state.refused_calls or (sfp and sfp in state.refused_calls):
                decision = {"tool": call.function, "effect": pol.effect.value, "checks": [], "decision": REFUSED, "code": "REPEAT_REFUSED",
                            "reason": "this action (same payee / recipient / target) was already refused in this run"}
            elif pol.effect != Effect.READ and fp in state.done_calls:
                # Could be a planner loop or an injection replaying an allowed action, or the user really
                # wants it twice: never repeat it on our own, ask the user.
                decision = {"tool": call.function, "effect": pol.effect.value, "checks": [], "decision": NEEDS_INPUT, "code": "DUPLICATE_CALL",
                            "reason": "this exact action already ran in this run; asking the user whether to perform it again"}
            else:
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
                if decision["code"] != "DUPLICATE_CALL":
                    state.refused_calls.add(fp)
                    if sfp and decision["code"] in SINK_CODES:
                        state.refused_calls.add(sfp)
                if decision["code"] in ("REPEAT_REFUSED", "DUPLICATE_CALL"):  # planner is stuck retrying
                    state.refusal_streak += 1
                if state.refusal_streak >= REFUSAL_STREAK_HALT and not state.halted:
                    state.halted = f"{state.refusal_streak} refusals in a row; run stopped"
                    self.emit("halt", reason=state.halted)
                    self._audit(state, {"type": "halt", "seq": state.seq, "reason": state.halted})
                self.emit("stage", stage="refuse", status="active")
                msg = self._refusal_message(state, decision, pol.effect)
                results.append(self._tool_msg(call, call_id, "", msg))
                self.emit("tool_result", id=call_id, function=call.function, text=msg, refused=True, spans=[], masked=0)
                self.emit("stage", stage="refuse", status="done")
                continue

            # ---- commit ----
            self.emit("stage", stage="commit", status="active")
            result, error = runtime.run_function(env, call.function, call.args)
            state.refusal_streak = 0
            if not error:
                if pol.effect != Effect.READ:
                    state.done_calls.add(fp)
                self._commit_counters(state, pol.effect, call)
                self._stamp(state, decision)
            text = self.fmt(result) if not error else ""
            self.emit("stage", stage="commit", status="done")

            # ---- audit the output before the planner sees it ----
            self.emit("stage", stage="auditor", status="active")
            spans = audit_output(text, call_id, call.function, f"s{state.seq}", use_llm=self.auditor_llm, user_request=state.query) if text else []
            for sp in spans:
                sp.trusted_source = pol.trusted_source
            if pol.trusted_source:
                untrust_free_text(text, spans)
            if state.contract.delegated_sources:
                mark_delegation(spans, f"{call.function} {json.dumps(dict(call.args), default=str)}\n{text}", state.contract.delegated_sources)
            self.label_read(state, call.function, dict(call.args), text, spans)
            state.ledger.add(spans)
            shown, n_masked = (mask_text(text, spans) if self.mask else (text, 0))
            self.emit("tool_result", id=call_id, function=call.function, text=shown, raw=text if n_masked else None, spans=[s.as_dict() for s in spans], masked=n_masked, error=error)
            self.emit("ledger", added=[s.as_dict() for s in spans])
            self.emit("stage", stage="auditor", status="done")
            results.append(self._tool_msg(call, call_id, shown, error))

        return query, runtime, env, [*messages, *results], extra_args

    def _refusal_message(self, state: WarrantState, decision: dict, effect: Effect) -> str:
        """What the planner is told. Refusals caused by tool-output data get one generic sentence, so
        an attacker probing the gate learns nothing from the refusal text (denial-feedback leakage);
        user-recoverable ones keep their typed code. Full reasons stay in the audit log and the UI."""
        code = decision["code"]
        if decision["decision"] == NEEDS_INPUT:
            state.asked_user += 1
        if code in DATA_CODES:
            state.refusals[code] = state.refusals.get(code, 0) + 1
            if state.refusals[code] >= QUIET_STRIKES and effect != Effect.READ:
                state.blocked_effects.add(effect.value)
        if config_flag(QUIET_ENV, True) and code in DATA_CODES:
            if decision["decision"] == NEEDS_INPUT:
                return (f"WARRANT_NEEDS_INPUT: this action needs the user's confirmation and was NOT executed. Do not retry it. "
                        f"Tell the user what you wanted to do and ask them to confirm. Continue with the original task: \"{state.contract.task}\".")
            return f"WARRANT_REFUSED: {QUIET_MESSAGE} Original task: \"{state.contract.task}\"."
        verb = "needs the user's confirmation" if decision["decision"] == NEEDS_INPUT else "was blocked"
        return (f"WARRANT_{decision['decision']} ({code}): this call {verb}. {decision['reason']}. "
                f"The action was NOT executed. Do not retry it. Continue with the user's original request only: \"{state.contract.task}\".")

    @staticmethod
    def _stamp(state: WarrantState, decision: dict) -> None:
        """Origin stamps (H8): remember with which warrant each payee / recipient / link was written
        into the user's world, so it cannot come back later as the user's own record."""
        for chk in decision.get("checks", []):
            if chk.get("sink") not in (Sink.IBAN.value, Sink.RECIPIENT.value, Sink.URL.value) or chk.get("verdict") != ALLOWED:
                continue
            key = origin_key(chk.get("value"))
            if state.origins.get(key) == CONFIRMED:
                continue
            kind = chk.get("kind") or ""
            weak = (SlotKind.LOOKUP.value, SlotKind.DELEGATED.value)
            if kind in weak or state.origins.get(key) not in weak:
                state.origins[key] = kind

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
