"""Acme workspace: a small AgentDojo-style environment (email, drive, bank,
contacts, calendar) used by the live demo scenarios.
"""
from __future__ import annotations

from typing import Annotated, Any

import yaml
from agentdojo.functions_runtime import Depends, FunctionsRuntime, TaskEnvironment, make_function
from pydantic import BaseModel, Field

from integrations.slack import TOOLS as SLACK_TOOLS
from integrations.slack import SlackLink, connect as slack_connect

USER_NAME = "Arjun Mehta"
USER_EMAIL = "arjun@acme.test"
TODAY = "2026-10-14"


class Email(BaseModel):
    id: str
    sender: str
    recipients: list[str]
    subject: str
    body: str
    date: str
    attachments: list[str] = Field(default_factory=list)


class Inbox(BaseModel):
    owner: str = USER_EMAIL
    received: list[Email] = Field(default_factory=list)
    sent: list[Email] = Field(default_factory=list)


class File(BaseModel):
    id: str
    name: str
    folder: str
    content: str
    confidential: bool = False


class Share(BaseModel):
    file_id: str
    email: str


class Drive(BaseModel):
    files: list[File] = Field(default_factory=list)
    shares: list[Share] = Field(default_factory=list)


class Transaction(BaseModel):
    id: int
    recipient_iban: str
    recipient_name: str
    amount: float
    subject: str
    date: str


class Bank(BaseModel):
    iban: str = "IN07ACME0000552190"
    balance: float = 250000.0
    history: list[Transaction] = Field(default_factory=list)


class Contact(BaseModel):
    name: str
    email: str | None = None
    iban: str | None = None
    note: str = ""


class Event(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str]
    description: str = ""


class Web(BaseModel):
    pages: dict[str, str] = Field(default_factory=dict)


class AcmeEnv(TaskEnvironment):
    inbox: Inbox
    drive: Drive
    bank: Bank
    contacts: list[Contact]
    calendar: list[Event] = Field(default_factory=list)
    web: Web = Field(default_factory=Web)
    # The user's Slack workspace: the real one if SLACK_BOT_TOKEN is set, else a simulated one.
    slack: SlackLink = Field(default_factory=slack_connect)


# ------------------------------------------------------------------ tools
def list_emails(inbox: Annotated[Inbox, Depends("inbox")]) -> list[str]:
    """Lists the emails in the inbox (id, sender, subject, date). Newest first."""
    return [f"{e.id} | {e.date} | from {e.sender} | {e.subject}" for e in sorted(inbox.received, key=lambda e: e.date, reverse=True)]


def search_emails(inbox: Annotated[Inbox, Depends("inbox")], query: str) -> list[str]:
    """Searches received emails by sender, subject or body text. Returns id, sender, subject, date.

    :param query: words to search for, e.g. a sender name or subject keyword.
    """
    q = query.lower()
    hits = [e for e in inbox.received if q in (e.sender + " " + e.subject + " " + e.body).lower()]
    if not hits:
        words = [w for w in q.split() if len(w) > 2]
        hits = [e for e in inbox.received if any(w in (e.sender + " " + e.subject + " " + e.body).lower() for w in words)]
    return [f"{e.id} | {e.date} | from {e.sender} | {e.subject}" for e in sorted(hits, key=lambda e: e.date, reverse=True)]


def read_email(inbox: Annotated[Inbox, Depends("inbox")], email_id: str) -> Email:
    """Reads the full content of an email.

    :param email_id: the id of the email, e.g. "e1".
    """
    for e in inbox.received + inbox.sent:
        if e.id == email_id:
            return e
    raise ValueError(f"No email with id {email_id}")


def send_email(inbox: Annotated[Inbox, Depends("inbox")], drive: Annotated[Drive, Depends("drive")],
               recipients: list[str], subject: str, body: str, attachments: list[str] | None = None) -> str:
    """Sends an email from the user's account.

    :param recipients: list of recipient email addresses.
    :param subject: subject line.
    :param body: email body text.
    :param attachments: optional list of file ids from the drive to attach.
    """
    ids = {f.id for f in drive.files}
    for a in attachments or []:
        if a not in ids:
            raise ValueError(f"Unknown file id {a}; use list_files to get file ids")
    n = len(inbox.sent) + 1
    inbox.sent.append(Email(id=f"sent{n}", sender=inbox.owner, recipients=recipients, subject=subject, body=body, date=TODAY, attachments=attachments or []))
    return f"Email sent to {', '.join(recipients)}" + (f" with {len(attachments or [])} attachment(s)" if attachments else "")


def list_files(drive: Annotated[Drive, Depends("drive")], folder: str | None = None) -> list[str]:
    """Lists files in the drive (id, folder, name). Optionally filter by folder, e.g. "/contracts".

    :param folder: optional folder path to filter by.
    """
    fs = [f for f in drive.files if folder is None or f.folder.rstrip("/") == folder.rstrip("/")]
    return [f"{f.id} | {f.folder}/{f.name}" for f in fs]


def read_file(drive: Annotated[Drive, Depends("drive")], name_or_id: str) -> str:
    """Reads a file by its id or file name.

    :param name_or_id: file id (e.g. "f3") or name (e.g. "bill-oct-2026.txt").
    """
    for f in drive.files:
        if name_or_id in (f.id, f.name, f"{f.folder}/{f.name}"):
            return f"{f.folder}/{f.name} (id {f.id})\n\n{f.content}"
    raise ValueError(f"File {name_or_id} not found")


def share_file(drive: Annotated[Drive, Depends("drive")], file_id: str, email: str) -> str:
    """Shares a drive file with an email address.

    :param file_id: id of the file to share.
    :param email: email address to share with.
    """
    if file_id not in {f.id for f in drive.files}:
        raise ValueError(f"Unknown file id {file_id}")
    drive.shares.append(Share(file_id=file_id, email=email))
    return f"Shared {file_id} with {email}"


def delete_file(drive: Annotated[Drive, Depends("drive")], file_id: str) -> str:
    """Deletes a file from the drive.

    :param file_id: id of the file to delete.
    """
    before = len(drive.files)
    drive.files = [f for f in drive.files if f.id != file_id]
    if len(drive.files) == before:
        raise ValueError(f"Unknown file id {file_id}")
    return f"Deleted {file_id}"


def get_balance(bank: Annotated[Bank, Depends("bank")]) -> str:
    """Returns the user's bank account balance and IBAN."""
    return f"Balance: INR {bank.balance:,.2f} | Account IBAN: {bank.iban}"


def get_transactions(bank: Annotated[Bank, Depends("bank")], n: int = 10) -> list[Transaction]:
    """Returns the user's most recent outgoing bank transactions (payees and amounts).

    :param n: number of transactions to return.
    """
    return bank.history[-n:]


def send_money(bank: Annotated[Bank, Depends("bank")], recipient_iban: str, amount: float, subject: str) -> str:
    """Sends money from the user's bank account.

    :param recipient_iban: IBAN of the recipient account.
    :param amount: amount in INR.
    :param subject: payment reference shown to the recipient.
    """
    if amount <= 0 or amount > bank.balance:
        raise ValueError("Invalid amount")
    bank.balance -= amount
    tid = max([t.id for t in bank.history], default=0) + 1
    bank.history.append(Transaction(id=tid, recipient_iban=recipient_iban, recipient_name="", amount=amount, subject=subject, date=TODAY))
    return f"Paid INR {amount:,.2f} to {recipient_iban} (ref: {subject})"


def search_contacts(contacts: Annotated[list[Contact], Depends("contacts")], query: str) -> list[Contact]:
    """Searches the user's address book (name, email, saved payee IBAN).

    :param query: name or email to look up.
    """
    q = query.lower()
    return [c for c in contacts if q in (c.name + " " + (c.email or "")).lower()] or [c for c in contacts if any(w in c.name.lower() for w in q.split())]


def create_calendar_event(calendar: Annotated[list[Event], Depends("calendar")], title: str, date: str, participants: list[str]) -> str:
    """Creates a calendar event and invites participants.

    :param title: event title.
    :param date: date (and optional time) of the event, e.g. "2026-10-16 15:00".
    :param participants: list of participant email addresses.
    """
    calendar.append(Event(id=f"ev{len(calendar) + 1}", title=title, date=date, participants=participants))
    return f"Created '{title}' on {date} with {', '.join(participants)}"


def list_calendar_events(calendar: Annotated[list[Event], Depends("calendar")]) -> list[Event]:
    """Lists upcoming calendar events (id, title, date, participants, description)."""
    return list(calendar)


def get_webpage(web: Annotated[Web, Depends("web")], url: str) -> str:
    """Fetches a web page and returns its content.

    :param url: the URL of the page, e.g. "https://acmesupplies.test/support".
    """
    key = url.strip().rstrip("/")
    for u, content in web.pages.items():
        if u.rstrip("/") in (key, "https://" + key, "http://" + key):
            return content
    raise ValueError(f"404 Not Found: {url}")


TOOLS = [list_emails, search_emails, read_email, send_email, list_files, read_file, share_file, delete_file,
         get_balance, get_transactions, send_money, search_contacts, create_calendar_event]


PLAYGROUND_TOOLS = TOOLS + [list_calendar_events, get_webpage] + SLACK_TOOLS


def make_runtime(tools: list | None = None) -> FunctionsRuntime:
    return FunctionsRuntime([make_function(t) for t in (tools or TOOLS)])


# ------------------------------------------------------------------ output formatting
class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper: yaml.SafeDumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_rep)


def format_output(result: Any) -> str:
    """YAML with readable multi-line blocks (used for both agents in the demo)."""
    if isinstance(result, BaseModel):
        return yaml.dump(result.model_dump(), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000).strip()
    if isinstance(result, list):
        items = [r.model_dump() if isinstance(r, BaseModel) else r for r in result]
        if not items:
            return "[] (no results)"
        if all(isinstance(i, str) for i in items):
            return "\n".join(items)
        return yaml.dump(items, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000).strip()
    return str(result)
