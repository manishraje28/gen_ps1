"""Real payments in Stripe TEST mode (no real money ever moves).

Provider choice and setup: docs/integrations/PAYMENTS.md.

Tools (AgentDojo style, dependency "payments"):
  get_balance, get_transactions, list_payees, send_money(recipient, amount, subject)

"Payees" are Stripe Connect test accounts (acct_...); send_money is a Stripe
Transfer from the platform's test balance to one of them.

Preview-then-commit: the gate's shadow step runs a tool on
`env.model_copy(deep=True)`. A `PaymentsAccount` deep-copies into a *dry-run*
account, so the shadow run calls no API: it only records the exact transfer
it would make in `pending`, which shows up in the gate's state diff. The real
transfer happens only when the gate runs the call on the original env.
`preview_transfer()` returns the same record directly.

Keys come only from the environment / .env (STRIPE_TEST_SECRET_KEY). Live keys
are refused. Without a key, `make_payments_env()` returns None and callers keep
using demo/env.py's simulated bank.
"""
from __future__ import annotations

import copy
import os
from typing import Annotated, Any

from agentdojo.functions_runtime import Depends, make_function
from pydantic import BaseModel, Field, PrivateAttr

import warrant.config  # noqa: F401  (loads .env into os.environ)
from warrant.policy import POLICIES, Effect, Sink, ToolPolicy

API = "https://api.stripe.com/v1"
KEY_VAR = "STRIPE_TEST_SECRET_KEY"
CURRENCY_VAR = "STRIPE_TEST_CURRENCY"
TIMEOUT = 20


class LiveKeyError(ValueError):
    """Raised for any key that is not a Stripe test-mode secret/restricted key."""


def check_test_key(key: str) -> str:
    key = (key or "").strip()
    if key.startswith(("sk_live_", "rk_live_", "pk_live_")) or "live" in key.split("_")[:2]:
        raise LiveKeyError("Refusing a LIVE Stripe key: the demo only runs in test mode.")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise LiveKeyError("Not a Stripe test secret key (expected sk_test_... or rk_test_...).")
    return key


def load_key() -> str | None:
    """Test key from env/.env, or None if unset. Raises LiveKeyError for live keys."""
    key = os.environ.get(KEY_VAR, "").strip()
    return check_test_key(key) if key else None


class StripeClient:
    """Thin HTTP layer (mocked in tests)."""

    def __init__(self, key: str) -> None:
        self.key = check_test_key(key)

    def request(self, method: str, path: str, data: dict | None = None, params: dict | None = None) -> dict:
        import requests

        r = requests.request(method, f"{API}{path}", auth=(self.key, ""), data=data, params=params, timeout=TIMEOUT)
        body = r.json()
        if r.status_code >= 400:
            raise ValueError(f"Stripe error: {body.get('error', {}).get('message', r.status_code)}")
        if body.get("livemode"):  # belt and braces: never act on a live object
            raise LiveKeyError("Stripe returned a livemode object; aborting.")
        return body


class PaymentsAccount(BaseModel):
    currency: str = "usd"
    dry_run: bool = False
    pending: list[dict] = Field(default_factory=list)   # transfers previewed by a dry run
    committed: list[dict] = Field(default_factory=list)  # transfers actually made this session
    _client: Any = PrivateAttr(default=None)

    def __deepcopy__(self, memo=None):
        # Shadow execution deep-copies the env: the copy must never touch the API.
        clone = PaymentsAccount(currency=self.currency, dry_run=True,
                                pending=copy.deepcopy(self.pending), committed=copy.deepcopy(self.committed))
        clone._client = self._client
        return clone

    @property
    def client(self) -> StripeClient:
        if self._client is None:
            raise ValueError("Payments client not configured")
        return self._client


def preview_transfer(account: PaymentsAccount, recipient: str, amount: float, subject: str) -> dict:
    """The exact Stripe call send_money would make. Never calls the API."""
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError("Invalid amount")
    if not str(recipient).startswith("acct_"):
        raise ValueError("Recipient must be a Stripe connected account id (acct_...)")
    return {
        "method": "POST", "path": "/transfers",
        "data": {"amount": int(round(amount * 100)), "currency": account.currency,
                 "destination": recipient, "description": subject},
    }


# ---------------- tools ----------------
def get_balance(payments: Annotated[PaymentsAccount, Depends("payments")]) -> str:
    """Returns the user's payment account balance (Stripe test mode)."""
    b = payments.client.request("GET", "/balance")
    fmt = lambda xs: ", ".join(f"{x['currency'].upper()} {x['amount'] / 100:,.2f}" for x in xs) or "0"
    return f"Available: {fmt(b.get('available', []))} | Pending: {fmt(b.get('pending', []))}"


def get_transactions(payments: Annotated[PaymentsAccount, Depends("payments")], n: int = 10) -> list[dict]:
    """Returns the user's most recent outgoing transfers (payee account, amount, subject).

    :param n: number of transfers to return.
    """
    res = payments.client.request("GET", "/transfers", params={"limit": max(1, min(int(n), 100))})
    return [{"id": t["id"], "recipient": t.get("destination"), "amount": t["amount"] / 100,
             "currency": t.get("currency"), "subject": t.get("description") or ""} for t in res.get("data", [])]


def list_payees(payments: Annotated[PaymentsAccount, Depends("payments")]) -> list[dict]:
    """Returns the user's saved payees (Stripe connected test accounts) with their account ids."""
    res = payments.client.request("GET", "/accounts", params={"limit": 100})
    out = []
    for a in res.get("data", []):
        prof = a.get("business_profile") or {}
        out.append({"recipient": a["id"], "name": prof.get("name") or a.get("email") or "", "email": a.get("email") or ""})
    return out


def send_money(payments: Annotated[PaymentsAccount, Depends("payments")], recipient: str, amount: float, subject: str) -> str:
    """Sends money from the user's payment account to a payee.

    :param recipient: payee account id (acct_...).
    :param amount: amount in the account currency.
    :param subject: payment reference shown to the recipient.
    """
    req = preview_transfer(payments, recipient, amount, subject)
    if payments.dry_run:
        payments.pending.append(req["data"])
        return f"[preview] would pay {payments.currency.upper()} {amount:,.2f} to {recipient} (ref: {subject})"
    t = payments.client.request(req["method"], req["path"], data=req["data"])
    payments.committed.append({"id": t["id"], **req["data"]})
    return f"Paid {payments.currency.upper()} {amount:,.2f} to {recipient} (ref: {subject}, transfer {t['id']})"


TOOLS = [get_balance, get_transactions, list_payees, send_money]

POLICIES.update({
    "get_balance": ToolPolicy(Effect.READ, trusted_source=True),
    "get_transactions": ToolPolicy(Effect.READ, trusted_source=True),
    "list_payees": ToolPolicy(Effect.READ, trusted_source=True),
    # demo bank's send_money uses recipient_iban; keep both sink names.
    "send_money": ToolPolicy(Effect.PAY, {"recipient_iban": Sink.IBAN, "recipient": Sink.IBAN, "amount": Sink.AMOUNT}),
})


def make_functions():
    return [make_function(f) for f in TOOLS]


def make_payments_account(client: StripeClient | None = None) -> PaymentsAccount | None:
    """A real (test-mode) account, or None when no key is set -> use the simulated bank."""
    if client is None:
        key = load_key()
        if key is None:
            return None
        client = StripeClient(key)
    acct = PaymentsAccount(currency=os.environ.get(CURRENCY_VAR, "usd").lower() or "usd")
    acct._client = client
    return acct


def payment_tools() -> tuple[str, list]:
    """('stripe_test', real tools) if a test key is configured, else ('simulated', demo bank tools)."""
    if make_payments_account() is not None:
        return "stripe_test", TOOLS
    from demo import env as demo_env
    return "simulated", [demo_env.get_balance, demo_env.get_transactions, demo_env.send_money]
