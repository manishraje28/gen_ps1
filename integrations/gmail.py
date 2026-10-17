"""Real Gmail tools for the live demo (dedicated demo account only).

Same tool names, signatures and output format as demo/env.py, so the WarrantGate
treats them identically (policies below mirror warrant/policy.py).

Safety model ("preview then commit"). A real mailbox cannot be deep-copied, so:
  * send_email always stages the exact message in ``inbox.sent`` first (that is
    what the gate's shadow run diffs and checks against the envelope);
  * the Gmail API is called ONLY when the inbox belongs to the env bound with
    :func:`attach`. The gate's shadow copy (``env.model_copy(deep=True)``) is a
    different object, so a shadow run can never send, and an env nobody bound
    never sends either (fail closed);
  * an optional recipient-domain allow-list (GMAIL_ALLOWED_DOMAINS) is enforced
    right before the API call, independent of the gate.

Credentials: GMAIL_CREDENTIALS_FILE (OAuth desktop client) and GMAIL_TOKEN_FILE
(created by ``python -m integrations.gmail --consent``) from .env. Missing either
-> :func:`available` is False and :func:`tools` returns the simulated tools.
"""
from __future__ import annotations

import threading

import base64
import os
import sys
import time
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Any

from agentdojo.functions_runtime import Depends

import warrant.config  # noqa: F401  (loads .env)
from demo import env as sim
from demo.env import Email, Inbox
from warrant.policy import POLICIES, Effect, Sink, ToolPolicy

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
# One consent covers Gmail, Calendar and Drive (integrations/gcal.py, integrations/gdrive.py share this token).
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
ALL_SCOPES = SCOPES + [CALENDAR_SCOPE, DRIVE_SCOPE]
ROOT = Path(__file__).resolve().parent.parent

POLICIES.update({
    "list_emails": ToolPolicy(Effect.READ),
    "search_emails": ToolPolicy(Effect.READ),
    "read_email": ToolPolicy(Effect.READ),
    "send_email": ToolPolicy(Effect.EGRESS, {"recipients": Sink.RECIPIENT, "cc": Sink.RECIPIENT,
                                             "bcc": Sink.RECIPIENT, "attachments": Sink.RESOURCE}),
})


def _path(var: str, default: str) -> Path:
    p = Path(os.environ.get(var, default))
    return p if p.is_absolute() else ROOT / p


def credentials_file() -> Path:
    return _path("GMAIL_CREDENTIALS_FILE", "secrets/gmail_credentials.json")


def token_file() -> Path:
    return _path("GMAIL_TOKEN_FILE", "secrets/gmail_token.json")


def allowed_domains() -> set[str]:
    raw = os.environ.get("GMAIL_ALLOWED_DOMAINS", "")
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


# ------------------------------------------------------------------ service / live binding
_service: Any = None
_local = threading.local()
_live_ids: set[int] = set()


def set_service(service: Any) -> None:
    """Inject a Gmail API service (tests pass a mock); None resets. Clears the shared read cache."""
    global _service
    _service = service
    if "clear_cache" in globals():
        clear_cache()


def available() -> bool:
    return _service is not None or (credentials_file().exists() and token_file().exists())


def granted_scopes() -> set[str]:
    """Scopes the saved token actually holds (empty without a token)."""
    import json
    try:
        return set(json.loads(token_file().read_text(encoding="utf-8")).get("scopes") or [])
    except Exception:
        return set()


def credentials() -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_file()), sorted(granted_scopes()) or SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file().write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError("Google token invalid; run: python -m integrations.gmail --consent")
    return creds


def service() -> Any:
    """One client per thread: googleapiclient's httplib2 transport is not thread-safe, and the two lanes
    run in parallel (a shared client garbles TLS: SSLError WRONG_VERSION_NUMBER)."""
    if _service is not None:  # injected (tests)
        return _service
    svc = getattr(_local, "service", None)
    if svc is None:
        from googleapiclient.discovery import build

        svc = _local.service = build("gmail", "v1", credentials=credentials(), cache_discovery=False)
    return svc


def attach(env: Any, owner: str | None = None) -> Any:
    """Bind ``env`` (the one the gate COMMITS to) as live: its sends really go out.
    Without credentials the env is returned untouched (simulated inbox)."""
    if not available():
        return env
    if owner:
        env.inbox.owner = owner
    _live_ids.add(id(env.inbox))
    return env


def detach(env: Any) -> None:
    _live_ids.discard(id(env.inbox))


def is_live(inbox: Inbox) -> bool:
    # Identity, not equality: a deep copy (shadow run) is never live.
    return id(inbox) in _live_ids


# ------------------------------------------------------------------ message helpers
def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _date(msg: dict) -> str:
    try:
        return parsedate_to_datetime(_header(msg, "Date")).date().isoformat()
    except Exception:
        return ""


def _body(part: dict) -> str:
    if part.get("mimeType", "").startswith("text/plain") and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    for p in part.get("parts", []) or []:
        text = _body(p)
        if text:
            return text
    return ""


def _attachments(part: dict) -> list[str]:
    out = [part["filename"]] if part.get("filename") else []
    for p in part.get("parts", []) or []:
        out += _attachments(p)
    return out


def _addrs(value: str) -> list[str]:
    return [a for _, a in getaddresses([value]) if a]


def to_email(msg: dict) -> Email:
    """Gmail API message (format=full) -> the demo's Email model."""
    sender = _addrs(_header(msg, "From"))
    return Email(id=msg["id"], sender=sender[0] if sender else _header(msg, "From"),
                 recipients=_addrs(_header(msg, "To")) + _addrs(_header(msg, "Cc")),
                 subject=_header(msg, "Subject"), body=_body(msg.get("payload", {})),
                 date=_date(msg), attachments=_attachments(msg.get("payload", {})))


def _line(e: Email) -> str:
    return f"{e.id} | {e.date} | from {e.sender} | {e.subject}"


LIST_TTL = float(os.environ.get("GMAIL_LIST_TTL", "30"))  # seconds a listing is reused by every lane and the UI


def max_results() -> int:
    """Emails fetched per listing (default 10). Every listed email costs one full message read the first time."""
    try:
        return max(1, min(50, int(os.environ.get("GMAIL_MAX_RESULTS", "10"))))
    except ValueError:
        return 10


# Shared by both agents and the UI panels. Messages never change once received, so each one is downloaded once
# per process; a listing is reused for LIST_TTL seconds. A per-key lock makes concurrent identical requests (the
# two lanes run in parallel) wait for one fetch instead of each calling Gmail.
_MSG_CACHE: dict[str, Email] = {}
_LIST_CACHE: dict[tuple[str, int], tuple[float, list[str]]] = {}
_CACHE_LOCK = threading.Lock()
_KEY_LOCKS: dict[tuple[str, int], threading.Lock] = {}
API_CALLS = {"list": 0, "get": 0}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _MSG_CACHE.clear()
        _LIST_CACHE.clear()


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc)
    return "Quota exceeded" in s or "rateLimitExceeded" in s or "userRateLimitExceeded" in s or " 429 " in s


def _call(fn):
    """One Gmail API call; on a per-minute quota error, wait briefly and try once more."""
    try:
        return fn()
    except Exception as exc:
        if not _is_quota_error(exc):
            raise
        time.sleep(float(os.environ.get("GMAIL_QUOTA_BACKOFF", "3")))
        return fn()


def _get_message(msg_id: str) -> Email:
    with _CACHE_LOCK:
        cached = _MSG_CACHE.get(msg_id)
    if cached is not None:
        return cached
    msgs = service().users().messages()
    API_CALLS["get"] += 1
    email = to_email(_call(lambda: msgs.get(userId="me", id=msg_id, format="full").execute()))
    with _CACHE_LOCK:
        _MSG_CACHE[msg_id] = email
    return email


def _fetch(query: str = "", n: int | None = None) -> list[Email]:
    n = n or max_results()
    key = (query or "in:inbox", n)
    with _CACHE_LOCK:
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        with _CACHE_LOCK:
            hit = _LIST_CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] <= LIST_TTL:
            ids = hit[1]
        else:
            msgs = service().users().messages()
            try:
                API_CALLS["list"] += 1
                listing = _call(lambda: msgs.list(userId="me", q=key[0], maxResults=n).execute())
                ids = [r["id"] for r in listing.get("messages", []) or []]
                with _CACHE_LOCK:
                    _LIST_CACHE[key] = (time.monotonic(), ids)
            except Exception as exc:
                if hit is None or not _is_quota_error(exc):
                    raise
                ids = hit[1]  # over quota: serve the last good listing instead of an error
        out = []
        for msg_id in ids:
            try:
                out.append(_get_message(msg_id))
            except Exception as exc:
                if not _is_quota_error(exc):
                    raise
                break  # over quota mid-way: return what we already have
    return sorted(out, key=lambda e: e.date, reverse=True)


_snapshot: tuple[float, list[Email]] = (0.0, [])


def snapshot(ttl: float = 30.0) -> list[Email]:
    """The live inbox for the UI's panels, cached briefly so page refreshes don't hit the API each time."""
    global _snapshot
    if time.monotonic() - _snapshot[0] > ttl:
        _snapshot = (time.monotonic(), _fetch())
    return _snapshot[1]


def build_message(sender: str, recipients: list[str], subject: str, body: str,
                  cc: list[str] | None = None, bcc: list[str] | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = ", ".join(recipients)
    if cc:
        m["Cc"] = ", ".join(cc)
    if bcc:
        m["Bcc"] = ", ".join(bcc)
    m["Subject"] = subject
    m.set_content(body)
    return m


def preview(args: dict, sender: str = sim.USER_EMAIL) -> str:
    """The exact message a send_email call with ``args`` would send (for the gate's
    preview step, the UI and the audit log). Pure: no API call."""
    return build_message(sender, list(args.get("recipients") or []), args.get("subject", ""), args.get("body", ""),
                         args.get("cc"), args.get("bcc")).as_string()


def check_allowed(addrs: list[str]) -> None:
    allow = allowed_domains()
    if not allow:
        return
    bad = [a for a in addrs if a.rsplit("@", 1)[-1].lower() not in allow]
    if bad:
        raise ValueError(f"Recipient(s) outside GMAIL_ALLOWED_DOMAINS: {', '.join(bad)}")


# ------------------------------------------------------------------ tools (same names as demo/env.py)
def list_emails(inbox: Annotated[Inbox, Depends("inbox")]) -> list[str]:
    """Lists the emails in the inbox (id, sender, subject, date). Newest first."""
    return [_line(e) for e in _fetch()]


def search_emails(inbox: Annotated[Inbox, Depends("inbox")], query: str) -> list[str]:
    """Searches received emails by sender, subject or body text. Returns id, sender, subject, date.

    :param query: words to search for, e.g. a sender name or subject keyword.
    """
    return [_line(e) for e in _fetch(query)]


def read_email(inbox: Annotated[Inbox, Depends("inbox")], email_id: str) -> Email:
    """Reads the full content of an email.

    :param email_id: the id of the email, e.g. "e1".
    """
    for e in inbox.sent:
        if e.id == email_id:
            return e
    try:
        return _get_message(email_id)
    except Exception as exc:
        raise ValueError(f"No email with id {email_id}") from exc


def send_email(inbox: Annotated[Inbox, Depends("inbox")], recipients: list[str], subject: str, body: str,
               cc: list[str] | None = None, bcc: list[str] | None = None) -> str:
    """Sends an email from the user's account.

    :param recipients: list of recipient email addresses.
    :param subject: subject line.
    :param body: email body text.
    :param cc: optional list of CC addresses.
    :param bcc: optional list of BCC addresses.
    """
    everyone = list(recipients) + list(cc or []) + list(bcc or [])
    # Stage first: the gate's shadow diff shows exactly this message.
    staged = Email(id=f"sent{len(inbox.sent) + 1}", sender=inbox.owner, recipients=everyone,
                   subject=subject, body=body, date=sim.TODAY)
    inbox.sent.append(staged)
    if is_live(inbox):
        check_allowed(everyone)
        raw = base64.urlsafe_b64encode(build_message(inbox.owner, recipients, subject, body, cc, bcc).as_bytes()).decode()
        sent = service().users().messages().send(userId="me", body={"raw": raw}).execute()
        staged.id = sent.get("id", staged.id)
    return f"Email sent to {', '.join(recipients)}"


GMAIL_TOOLS = [list_emails, search_emails, read_email, send_email]


def tools(base: list | None = None) -> list:
    """``base`` (default demo PLAYGROUND_TOOLS) with the email tools swapped for the
    real Gmail ones when credentials exist; unchanged (simulated inbox) otherwise."""
    base = list(base or sim.PLAYGROUND_TOOLS)
    if not available():
        return base
    real = {f.__name__: f for f in GMAIL_TOOLS}
    return [real.get(f.__name__, f) for f in base]


# ------------------------------------------------------------------ one-time consent
def consent() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    cf = credentials_file()
    if not cf.exists():
        sys.exit(f"Missing {cf}. Save the OAuth desktop client JSON there (see docs/integrations/GMAIL.md).")
    creds = InstalledAppFlow.from_client_secrets_file(str(cf), ALL_SCOPES).run_local_server(port=0)
    token_file().parent.mkdir(parents=True, exist_ok=True)
    token_file().write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved token to {token_file()}")
    print("Connected as", service().users().getProfile(userId="me").execute().get("emailAddress"))


if __name__ == "__main__":
    if "--consent" in sys.argv:
        consent()
    else:
        print("available:", available(), "| credentials:", credentials_file(), "| token:", token_file())
