"""Real (sandbox) banking through the Open Bank Project API. No real money ever moves.

Setup: docs/integrations/OBP.md. Defaults target https://apisandbox.openbankproject.com.

Tools (AgentDojo style, dependency "obp"):
  get_balance, get_transactions(n), list_payees, send_money(recipient, amount, subject)

A payee / recipient is written "BANK_ID/ACCOUNT_ID" (an OBP account in the sandbox). send_money creates a
SANDBOX_TAN transaction request from the user's own account to it.

Preview-then-commit: the gate's shadow step runs a tool on `env.model_copy(deep=True)`. An `OBPAccount`
deep-copies into a *dry-run* account, so the shadow run calls no API: it only records the exact transaction
request it would POST in `pending`, which shows up in the gate's state diff. The real POST happens only when
the gate runs the call on the original env, after every check passes. `preview_transfer()` returns the same
request directly.

Auth is OBP DirectLogin: POST /my/logins/direct with
  Authorization: DirectLogin username="...",password="...",consumer_key="..."
then every call sends  Authorization: DirectLogin token="...".
Credentials come only from the environment / .env. Without them `make_obp_account()` returns None and callers
keep using demo/env.py's simulated bank. Only sandbox hosts are accepted (OBP_ALLOW_NON_SANDBOX=1 overrides).
"""
from __future__ import annotations

import copy
import os
import re
import sys
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from agentdojo.functions_runtime import Depends, make_function
from pydantic import BaseModel, Field, PrivateAttr

import warrant.config  # noqa: F401  (loads .env into os.environ)
from warrant.policy import POLICIES, Effect, Sink, ToolPolicy

DEFAULT_BASE = "https://apisandbox.openbankproject.com"
TIMEOUT = 20.0
_PAYEE_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")


class NotSandboxError(ValueError):
    """Raised when OBP_BASE_URL is not a sandbox host (real banks are never targeted)."""


def check_sandbox(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE).rstrip("/")
    host = urlparse(base).hostname or ""
    if "sandbox" not in host and os.environ.get("OBP_ALLOW_NON_SANDBOX") != "1" and host not in ("localhost", "127.0.0.1"):
        raise NotSandboxError(f"Refusing OBP host {host!r}: only sandbox hosts are allowed for the demo.")
    return base


def settings() -> dict | None:
    """OBP settings from env/.env, or None if the required ones are missing."""
    env = os.environ
    s = {
        "base": env.get("OBP_BASE_URL", DEFAULT_BASE).strip() or DEFAULT_BASE,
        "version": env.get("OBP_API_VERSION", "v4.0.0").strip() or "v4.0.0",
        "view": env.get("OBP_VIEW_ID", "owner").strip() or "owner",
        "username": env.get("OBP_USERNAME", "").strip(),
        "password": env.get("OBP_PASSWORD", ""),
        "consumer_key": env.get("OBP_CONSUMER_KEY", "").strip(),
        "bank_id": env.get("OBP_BANK_ID", "").strip(),
        "account_id": env.get("OBP_ACCOUNT_ID", "").strip(),
        "currency": env.get("OBP_CURRENCY", "EUR").strip().upper() or "EUR",
    }
    required = ("username", "password", "consumer_key", "bank_id", "account_id")
    return s if all(s[k] for k in required) else None


def _q(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class OBPClient:
    """Thin HTTP layer with DirectLogin (mocked in tests via an httpx transport)."""

    def __init__(self, base: str, version: str, username: str, password: str, consumer_key: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base = check_sandbox(base)
        self.version = version
        self._creds = (username, password, consumer_key)
        self._token: str | None = None
        self._http = httpx.Client(base_url=self.base, timeout=TIMEOUT, transport=transport)

    def login(self) -> str:
        user, pw, key = self._creds
        auth = f"DirectLogin username={_q(user)},password={_q(pw)},consumer_key={_q(key)}"
        r = self._http.post("/my/logins/direct", headers={"Authorization": auth, "Content-Type": "application/json"})
        body = self._json(r)
        token = body.get("token")
        if not token:
            raise ValueError("OBP DirectLogin returned no token")
        self._token = token
        return token

    def request(self, method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict:
        if self._token is None:
            self.login()
        url = f"/obp/{self.version}{path}"
        for attempt in range(2):
            r = self._http.request(method, url, json=json, params=params,
                                   headers={"Authorization": f"DirectLogin token={_q(self._token)}", "Content-Type": "application/json"})
            if r.status_code == 401 and attempt == 0:  # token expired: log in once more
                self.login()
                continue
            return self._json(r)
        return self._json(r)

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {"message": r.text[:200]}
        if r.status_code >= 400:
            msg = body.get("message") or body.get("error") or r.status_code
            raise ValueError(f"OBP error {r.status_code}: {msg}")
        return body


class OBPAccount(BaseModel):
    bank_id: str
    account_id: str
    view: str = "owner"
    currency: str = "EUR"
    dry_run: bool = False
    pending: list[dict] = Field(default_factory=list)    # transaction requests previewed by a dry run
    committed: list[dict] = Field(default_factory=list)  # transaction requests actually made this session
    _client: Any = PrivateAttr(default=None)

    def __deepcopy__(self, memo=None):
        # Shadow execution deep-copies the env: the copy must never touch the API.
        clone = OBPAccount(bank_id=self.bank_id, account_id=self.account_id, view=self.view, currency=self.currency,
                           dry_run=True, pending=copy.deepcopy(self.pending), committed=copy.deepcopy(self.committed))
        clone._client = self._client
        return clone

    @property
    def client(self) -> OBPClient:
        if self._client is None:
            raise ValueError("OBP client not configured")
        return self._client

    @property
    def path(self) -> str:
        return f"/banks/{self.bank_id}/accounts/{self.account_id}/{self.view}"


def preview_transfer(account: OBPAccount, recipient: str, amount: float, subject: str) -> dict:
    """The exact OBP call send_money would make. Never calls the API."""
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError("Invalid amount")
    if not _PAYEE_RE.match(str(recipient)):
        raise ValueError("Recipient must be an OBP account written BANK_ID/ACCOUNT_ID")
    to_bank, to_account = str(recipient).split("/", 1)
    if (to_bank, to_account) == (account.bank_id, account.account_id):
        raise ValueError("Recipient is the user's own account")
    return {
        "method": "POST", "path": f"{account.path}/transaction-request-types/SANDBOX_TAN/transaction-requests",
        "json": {"to": {"bank_id": to_bank, "account_id": to_account},
                 "value": {"currency": account.currency, "amount": f"{float(amount):.2f}"},
                 "description": subject},
    }


def _other(t: dict) -> str:
    oa = t.get("other_account") or t.get("counterparty") or {}
    bank = (oa.get("bank_routing") or {}).get("address") or (oa.get("bank") or {}).get("national_identifier") or oa.get("bank_id") or ""
    acc = (oa.get("account_routing") or {}).get("address") or oa.get("number") or oa.get("account_id") or ""
    return f"{bank}/{acc}" if bank and acc else (oa.get("holder") or {}).get("name", "")


# ---------------- tools ----------------
def get_balance(obp: Annotated[OBPAccount, Depends("obp")]) -> str:
    """Returns the user's bank account balance (Open Bank Project sandbox)."""
    a = obp.client.request("GET", f"{obp.path}/account")
    b = a.get("balance") or {}
    if not b and a.get("balances"):
        b = a["balances"][0].get("amount", a["balances"][0])
    return f"Balance: {b.get('currency', obp.currency)} {float(b.get('amount', 0)):,.2f} (account {obp.bank_id}/{obp.account_id})"


def get_transactions(obp: Annotated[OBPAccount, Depends("obp")], n: int = 10) -> list[dict]:
    """Returns the user's most recent transactions (counterparty, amount, description).

    :param n: number of transactions to return.
    """
    res = obp.client.request("GET", f"{obp.path}/transactions", params={"limit": max(1, min(int(n), 100))})
    out = []
    for t in res.get("transactions", [])[: max(1, min(int(n), 100))]:
        d = t.get("details") or {}
        v = d.get("value") or {}
        out.append({"id": t.get("id"), "counterparty": _other(t), "amount": float(v.get("amount", 0)),
                    "currency": v.get("currency", obp.currency), "description": d.get("description") or "",
                    "date": (d.get("completed") or d.get("posted") or "")[:10]})
    return out


def list_payees(obp: Annotated[OBPAccount, Depends("obp")]) -> list[dict]:
    """Returns the user's known payees (other accounts seen in the account's history), written BANK_ID/ACCOUNT_ID."""
    res = obp.client.request("GET", f"{obp.path}/other_accounts")
    out, seen = [], set()
    for oa in res.get("other_accounts", []):
        rec = _other({"other_account": oa})
        if rec and rec not in seen:
            seen.add(rec)
            out.append({"recipient": rec, "name": (oa.get("holder") or {}).get("name", "")})
    return out


def send_money(obp: Annotated[OBPAccount, Depends("obp")], recipient: str, amount: float, subject: str) -> str:
    """Sends money from the user's bank account to another account.

    :param recipient: payee account, written BANK_ID/ACCOUNT_ID.
    :param amount: amount in the account currency.
    :param subject: payment description shown to the recipient.
    """
    req = preview_transfer(obp, recipient, amount, subject)
    if obp.dry_run:
        obp.pending.append({"recipient": recipient, **req["json"]})
        return f"[preview] would pay {obp.currency} {amount:,.2f} to {recipient} (ref: {subject})"
    t = obp.client.request(req["method"], req["path"], json=req["json"])
    obp.committed.append({"id": t.get("id"), "status": t.get("status"), "recipient": recipient, **req["json"]})
    status = t.get("status", "?")
    extra = "" if status == "COMPLETED" else " (the bank asks for a challenge answer before it completes)"
    return f"Payment request {t.get('id', '?')} to {recipient}: {obp.currency} {amount:,.2f} (ref: {subject}), status {status}{extra}"


TOOLS = [get_balance, get_transactions, list_payees, send_money]

POLICIES.update({
    "get_balance": ToolPolicy(Effect.READ, trusted_source=True),
    "get_transactions": ToolPolicy(Effect.READ, trusted_source=True),
    "list_payees": ToolPolicy(Effect.READ, trusted_source=True),
    # the demo bank's send_money uses recipient_iban; keep both sink names.
    "send_money": ToolPolicy(Effect.PAY, {"recipient_iban": Sink.IBAN, "recipient": Sink.IBAN, "amount": Sink.AMOUNT}),
})


def make_functions():
    return [make_function(f) for f in TOOLS]


def make_obp_account(client: OBPClient | None = None) -> OBPAccount | None:
    """A sandbox account, or None when the OBP settings are missing -> use the simulated bank."""
    s = settings()
    if s is None:
        return None
    if client is None:
        client = OBPClient(s["base"], s["version"], s["username"], s["password"], s["consumer_key"])
    acct = OBPAccount(bank_id=s["bank_id"], account_id=s["account_id"], view=s["view"], currency=s["currency"])
    acct._client = client
    return acct


def obp_tools() -> tuple[str, list]:
    """('obp_sandbox', real tools) if OBP is configured, else ('simulated', demo bank tools)."""
    if settings() is not None:
        return "obp_sandbox", TOOLS
    from demo import env as demo_env
    return "simulated", [demo_env.get_balance, demo_env.get_transactions, demo_env.send_money]


def _main(argv: list[str]) -> int:
    """python -m integrations.obp            check login, show balance, payees, last transactions
       python -m integrations.obp --preview BANK_ID/ACCOUNT_ID 1.00 "test"   show the exact request (no API call)
       python -m integrations.obp --pay BANK_ID/ACCOUNT_ID 1.00 "test"       make one sandbox payment"""
    acct = make_obp_account()
    if acct is None:
        print("OBP not configured: set OBP_USERNAME, OBP_PASSWORD, OBP_CONSUMER_KEY, OBP_BANK_ID, OBP_ACCOUNT_ID in .env")
        return 1
    if argv[:1] in (["--preview"], ["--pay"]) and len(argv) >= 4:
        recipient, amount, subject = argv[1], float(argv[2]), " ".join(argv[3:])
        if argv[0] == "--preview":
            print(preview_transfer(acct, recipient, amount, subject))
        else:
            print(send_money(acct, recipient, amount, subject))
        return 0
    acct.client.login()
    print("logged in to", acct.client.base, "as", settings()["username"])
    print(get_balance(acct))
    for p in list_payees(acct)[:10]:
        print("  payee:", p)
    for t in get_transactions(acct, 5):
        print("  tx:", t)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
