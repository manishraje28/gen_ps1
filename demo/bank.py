"""Simulated bank for the Acme workspace: several accounts (savings, salary, private wealth, joint, business,
fixed deposit), saved payees, standing orders, debit/credit cards, incoming credits, daily limits.

Everything is in memory and deterministic (no network). `Bank.iban`, `Bank.balance` and `Bank.history`
keep their old meaning (primary account, outgoing payments) so the scenarios, tests and benchmarks are
unchanged. Rules a real bank enforces are enforced here too: no overdraft, per-account daily limit, frozen or
closed accounts cannot pay, a fixed deposit cannot pay out before maturity, frozen cards cannot be charged.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from agentdojo.functions_runtime import Depends
from pydantic import BaseModel, Field

TODAY = "2026-10-14"
HOLDER = "Arjun Mehta"
ACCOUNT_KINDS = ("savings", "salary", "private", "joint", "current", "fixed_deposit")
FREQUENCIES = ("once", "weekly", "monthly", "quarterly", "yearly")


class Account(BaseModel):
    id: str
    name: str
    kind: str                       # one of ACCOUNT_KINDS
    iban: str
    balance: float
    holders: list[str] = Field(default_factory=lambda: [HOLDER])
    status: str = "active"          # active | frozen | closed
    daily_limit: float = 200000.0   # max outgoing per day (payments + own transfers)
    interest_rate: float = 0.0      # % per year
    maturity: str = ""              # fixed deposits only: no payouts before this date
    currency: str = "INR"


class Transaction(BaseModel):
    id: int
    recipient_iban: str
    recipient_name: str
    amount: float
    subject: str
    date: str
    account_id: str = "acc1"        # account the money left (debits) or arrived in (credits)
    direction: str = "debit"        # debit | credit
    counterparty_iban: str = ""     # credits: who paid us
    balance_after: float | None = None
    category: str = "payment"       # payment | own_transfer | card_bill | standing_order | deposit | salary | interest | refund


class Payee(BaseModel):
    id: str
    name: str
    iban: str
    nickname: str = ""
    added: str = TODAY


class StandingOrder(BaseModel):
    id: str
    account_id: str
    recipient_iban: str
    recipient_name: str
    amount: float
    subject: str
    frequency: str                  # one of FREQUENCIES
    next_date: str
    active: bool = True


class Card(BaseModel):
    id: str
    name: str
    kind: str                       # debit | credit
    last4: str
    account_id: str                 # debit: account charged; credit: account that pays the card bill
    status: str = "active"          # active | frozen
    limit: float = 0.0              # credit cards: credit limit
    outstanding: float = 0.0        # credit cards: amount owed
    due_date: str = ""


def _accounts() -> list[Account]:
    return [
        Account(id="acc1", name="Personal Savings", kind="savings", iban="IN07ACME0000552190", balance=250000.0, interest_rate=3.5),
        Account(id="acc2", name="Salary Account", kind="salary", iban="IN07ACME0000552191", balance=182000.0),
        Account(id="acc3", name="Private Wealth", kind="private", iban="IN07ACME0000552192", balance=1850000.0,
                daily_limit=1000000.0, interest_rate=4.0),
        Account(id="acc4", name="Joint Household", kind="joint", iban="IN07ACME0000552193", balance=96500.0,
                holders=[HOLDER, "Meera Mehta"], daily_limit=100000.0, interest_rate=3.0),
        Account(id="acc5", name="Business Current", kind="current", iban="IN07ACME0000552194", balance=640000.0, daily_limit=500000.0),
        Account(id="acc6", name="Fixed Deposit (12 months)", kind="fixed_deposit", iban="IN07ACME0000552195", balance=500000.0,
                interest_rate=7.1, maturity="2027-04-15", daily_limit=0.0),
    ]


def _payees() -> list[Payee]:
    return [
        Payee(id="p1", name="Mumbai Power Ltd", iban="IN12POWR0000004471", nickname="electricity", added="2025-03-02"),
        Payee(id="p2", name="Mumbai Water", iban="IN45WATR0000008812", nickname="water", added="2025-03-02"),
        Payee(id="p3", name="Sunrise Apartments", iban="IN30RENT0000771234", nickname="rent", added="2024-11-20"),
    ]


def _credits() -> list[Transaction]:
    return [
        Transaction(id=101, recipient_iban="IN07ACME0000552191", recipient_name="Salary Account", amount=182000.0,
                    subject="Salary Sep 2026", date="2026-09-30", account_id="acc2", direction="credit",
                    counterparty_iban="IN55ACME0000000001", category="salary"),
        Transaction(id=102, recipient_iban="IN07ACME0000552190", recipient_name="Personal Savings", amount=2187.5,
                    subject="Interest Q2 FY27", date="2026-09-30", account_id="acc1", direction="credit", category="interest"),
    ]


def _orders() -> list[StandingOrder]:
    return [StandingOrder(id="so1", account_id="acc1", recipient_iban="IN30RENT0000771234", recipient_name="Sunrise Apartments",
                          amount=45000.0, subject="Rent", frequency="monthly", next_date="2026-11-01")]


def _cards() -> list[Card]:
    return [
        Card(id="c1", name="Savings Debit Card", kind="debit", last4="4021", account_id="acc1"),
        Card(id="c2", name="Platinum Credit Card", kind="credit", last4="8830", account_id="acc1",
             limit=300000.0, outstanding=42150.0, due_date="2026-10-25"),
        Card(id="c3", name="Business Debit Card", kind="debit", last4="7712", account_id="acc5"),
    ]


class Bank(BaseModel):
    accounts: list[Account] = Field(default_factory=_accounts)
    history: list[Transaction] = Field(default_factory=list)     # outgoing (debits), all accounts
    credits: list[Transaction] = Field(default_factory=_credits)  # incoming, all accounts
    payees: list[Payee] = Field(default_factory=_payees)
    standing_orders: list[StandingOrder] = Field(default_factory=_orders)
    cards: list[Card] = Field(default_factory=_cards)

    # The primary account (Personal Savings): the single account of the original demo bank.
    @property
    def primary(self) -> Account:
        return self.accounts[0]

    @property
    def iban(self) -> str:
        return self.primary.iban

    @property
    def balance(self) -> float:
        return self.primary.balance

    @property
    def total(self) -> float:
        return sum(a.balance for a in self.accounts if a.status != "closed")


# ------------------------------------------------------------------ helpers
def _norm(iban: str) -> str:
    return (iban or "").replace(" ", "").upper()


def _money(x: float) -> str:
    return f"INR {x:,.2f}"


def find_account(bank: Bank, ref: str | None) -> Account:
    """By id ("acc2"), IBAN, exact name or kind ("salary"); None = primary account."""
    if ref is None or not str(ref).strip():
        return bank.primary
    r = " ".join(str(ref).strip().lower().replace("account", "").split())
    if r in ("main", "primary", "default", "main savings", "my", "my savings", "personal", ""):
        return bank.primary
    for a in bank.accounts:
        if r in (a.id.lower(), _norm(a.iban).lower(), a.name.lower(), " ".join(a.name.lower().replace("account", "").split()),
                 a.kind, a.kind.replace("_", " ")):
            return a
    matches = [a for a in bank.accounts if r in a.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown account '{ref}'. Accounts: " + ", ".join(f"{a.id} ({a.name})" for a in bank.accounts))


def find_card(bank: Bank, ref: str) -> Card:
    """By id ("c2"), last 4 digits, name, or type ("debit", "credit") when that is unambiguous."""
    r = str(ref or "").strip().lower().replace("card", "").replace("…", "").strip()
    for c in bank.cards:
        if r in (c.id, c.last4, c.name.lower().replace("card", "").strip()):
            return c
    words = r.split()
    matches = [c for c in bank.cards if words and all(w in f"{c.kind} {c.name.lower()} {c.last4}" for w in words)]
    if len(matches) == 1:
        return matches[0]
    # "my debit card" = the personal one (linked to the main account), as a bank app would assume
    personal = [c for c in matches if c.account_id == bank.primary.id]
    if len(personal) == 1 and set(words) <= {"debit", "credit", "my"}:
        return personal[0]
    raise ValueError(f"{'Ambiguous' if matches else 'Unknown'} card '{ref}'. Cards: " + ", ".join(f"{c.id} {c.name} …{c.last4}" for c in bank.cards))


def _own(bank: Bank, iban: str) -> Account | None:
    return next((a for a in bank.accounts if _norm(a.iban) == _norm(iban)), None)


def _next_id(bank: Bank) -> int:
    return max([t.id for t in bank.history + bank.credits], default=0) + 1


def _spent_today(bank: Bank, acct: Account) -> float:
    return sum(t.amount for t in bank.history if t.account_id == acct.id and t.date == TODAY)


def _check_debit(bank: Bank, acct: Account, amount: float) -> None:
    if not isinstance(amount, (int, float)) or amount != amount or amount <= 0:
        raise ValueError("Invalid amount")
    if round(amount, 2) != amount:
        raise ValueError("Invalid amount: at most 2 decimal places")
    if acct.status != "active":
        raise ValueError(f"{acct.name} is {acct.status}; it cannot send money")
    if acct.kind == "fixed_deposit" and TODAY < acct.maturity:
        raise ValueError(f"{acct.name} is locked until {acct.maturity}; break the deposit with close_fixed_deposit first")
    if amount > acct.balance:
        raise ValueError(f"Invalid amount: insufficient funds in {acct.name} (available {_money(acct.balance)})")
    if _spent_today(bank, acct) + amount > acct.daily_limit:
        left = max(acct.daily_limit - _spent_today(bank, acct), 0)
        raise ValueError(f"Daily limit reached for {acct.name}: {_money(left)} left today (limit {_money(acct.daily_limit)})")


def _debit(bank: Bank, acct: Account, iban: str, name: str, amount: float, subject: str, category: str) -> Transaction:
    _check_debit(bank, acct, amount)
    acct.balance = round(acct.balance - amount, 2)
    t = Transaction(id=_next_id(bank), recipient_iban=iban, recipient_name=name, amount=amount, subject=subject, date=TODAY,
                    account_id=acct.id, balance_after=acct.balance, category=category)
    bank.history.append(t)
    dest = _own(bank, iban)
    if dest is not None:  # money between the user's own accounts arrives immediately
        _credit(bank, dest, amount, subject, acct.iban, "own_transfer")
    return t


def _credit(bank: Bank, acct: Account, amount: float, subject: str, from_iban: str, category: str) -> Transaction:
    if acct.status == "closed":
        raise ValueError(f"{acct.name} is closed")
    acct.balance = round(acct.balance + amount, 2)
    t = Transaction(id=_next_id(bank), recipient_iban=acct.iban, recipient_name=acct.name, amount=amount, subject=subject,
                    date=TODAY, account_id=acct.id, direction="credit", counterparty_iban=from_iban,
                    balance_after=acct.balance, category=category)
    bank.credits.append(t)
    return t


def _payee_name(bank: Bank, iban: str) -> str:
    own = _own(bank, iban)
    if own:
        return own.name
    return next((p.name for p in bank.payees if _norm(p.iban) == _norm(iban)), "")


def _add_period(d: str, frequency: str) -> str:
    y, m, day = (int(x) for x in d.split("-"))
    if frequency == "weekly":
        return (date(y, m, day) + timedelta(days=7)).isoformat()
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[frequency]
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    for dd in (day, 30, 29, 28):  # 31 Jan + 1 month = 28/29 Feb
        try:
            return date(y, m, dd).isoformat()
        except ValueError:
            continue
    raise ValueError(d)


def _valid_date(d: str) -> str:
    try:
        return date.fromisoformat(d.strip()).isoformat()
    except Exception:
        raise ValueError(f"Invalid date '{d}': use YYYY-MM-DD") from None


# ------------------------------------------------------------------ tools (read)
def get_balance(bank: Annotated[Bank, Depends("bank")]) -> str:
    """Returns the balance and IBAN of the user's main account, and of every other account they hold."""
    lines = [f"Balance: {_money(bank.balance)} | Account IBAN: {bank.iban}", "", "All accounts:"]
    for a in bank.accounts:
        extra = f", matures {a.maturity}" if a.maturity else ""
        lines.append(f"- {a.id} {a.name} ({a.kind}, {a.status}{extra}): {_money(a.balance)} | IBAN {a.iban}")
    lines.append(f"Total across accounts: {_money(bank.total)}")
    return "\n".join(lines)


def list_accounts(bank: Annotated[Bank, Depends("bank")]) -> list[Account]:
    """Lists all the user's bank accounts (id, name, type, IBAN, balance, holders, status, daily limit, interest, maturity)."""
    return list(bank.accounts)


def get_transactions(bank: Annotated[Bank, Depends("bank")], n: int = 10, account: str | None = None) -> list[Transaction]:
    """Returns the user's most recent bank transactions, newest last: payments out (debits) and money in (credits).

    :param n: number of transactions to return.
    :param account: optional account id, name or type (e.g. "acc2", "salary") to show only that account.
    """
    rows = bank.history + bank.credits
    if account:
        acct = find_account(bank, account)
        rows = [t for t in rows if t.account_id == acct.id]
    rows = sorted(rows, key=lambda t: (t.date, t.id))
    return rows[-max(int(n), 1):]


def list_payees(bank: Annotated[Bank, Depends("bank")]) -> list[Payee]:
    """Lists the user's saved payees (name, IBAN, nickname)."""
    return list(bank.payees)


def list_scheduled_transfers(bank: Annotated[Bank, Depends("bank")]) -> list[StandingOrder]:
    """Lists the user's standing orders and scheduled transfers (id, payee, amount, frequency, next date)."""
    return list(bank.standing_orders)


def list_cards(bank: Annotated[Bank, Depends("bank")]) -> list[Card]:
    """Lists the user's debit and credit cards (id, last 4 digits, status, credit limit, amount owed, due date)."""
    return list(bank.cards)


# ------------------------------------------------------------------ tools (money moves)
def send_money(bank: Annotated[Bank, Depends("bank")], recipient_iban: str, amount: float, subject: str,
               from_account: str | None = None) -> str:
    """Sends money from one of the user's accounts (the main savings account unless from_account is given).

    :param recipient_iban: IBAN of the recipient account.
    :param amount: amount in INR.
    :param subject: payment reference shown to the recipient.
    :param from_account: only if the user named an account to pay from (e.g. "business", "salary"); leave empty for the main savings account.
    """
    acct = find_account(bank, from_account)
    iban = (recipient_iban or "").strip()
    if len(_norm(iban)) < 15 or not _norm(iban)[:2].isalpha():
        raise ValueError(f"Invalid IBAN '{recipient_iban}'")
    if _own(bank, iban) is acct:
        raise ValueError("Cannot pay an account into itself")
    _debit(bank, acct, iban, _payee_name(bank, iban), amount, subject, "payment")
    return f"Paid {_money(amount)} to {iban} (ref: {subject})" + ("" if acct is bank.primary else f" from {acct.name}")


def transfer_between_accounts(bank: Annotated[Bank, Depends("bank")], from_account: str, to_account: str, amount: float,
                              note: str = "Own transfer") -> str:
    """Transfers money between two of the user's own accounts (arrives immediately).

    :param from_account: account id, name or type to take the money from.
    :param to_account: account id, name or type to put the money in.
    :param amount: amount in INR.
    :param note: optional reference.
    """
    src, dst = find_account(bank, from_account), find_account(bank, to_account)
    if src is dst:
        raise ValueError("From and to are the same account")
    if dst.status != "active":
        raise ValueError(f"{dst.name} is {dst.status}; it cannot receive money")
    _debit(bank, src, dst.iban, dst.name, amount, note, "own_transfer")
    return f"Moved {_money(amount)} from {src.name} to {dst.name}. {src.name}: {_money(src.balance)}, {dst.name}: {_money(dst.balance)}"


def schedule_transfer(bank: Annotated[Bank, Depends("bank")], recipient_iban: str, amount: float, subject: str,
                      frequency: str = "once", start_date: str = "", from_account: str | None = None) -> str:
    """Sets up a scheduled payment or standing order. Nothing is paid today; it runs on start_date.

    :param recipient_iban: IBAN of the recipient account.
    :param amount: amount in INR per payment.
    :param subject: payment reference.
    :param frequency: once, weekly, monthly, quarterly or yearly.
    :param start_date: first payment date, YYYY-MM-DD (default: tomorrow).
    :param from_account: only if the user named an account to pay from; leave empty for the main savings account.
    """
    acct = find_account(bank, from_account)
    freq = (frequency or "once").strip().lower()
    if freq not in FREQUENCIES:
        raise ValueError(f"Invalid frequency '{frequency}': use one of {', '.join(FREQUENCIES)}")
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError("Invalid amount")
    if acct.status != "active" or acct.kind == "fixed_deposit":
        raise ValueError(f"{acct.name} cannot make scheduled payments")
    start = _valid_date(start_date) if start_date else (date.fromisoformat(TODAY) + timedelta(days=1)).isoformat()
    if start <= TODAY:
        raise ValueError("start_date must be in the future; use send_money to pay today")
    oid = f"so{max([int(o.id[2:]) for o in bank.standing_orders if o.id[2:].isdigit()], default=0) + 1}"
    bank.standing_orders.append(StandingOrder(id=oid, account_id=acct.id, recipient_iban=recipient_iban.strip(),
                                              recipient_name=_payee_name(bank, recipient_iban), amount=amount, subject=subject,
                                              frequency=freq, next_date=start))
    return f"Scheduled {_money(amount)} to {recipient_iban.strip()} ({freq}, first on {start}) from {acct.name}: {oid}"


def cancel_scheduled_transfer(bank: Annotated[Bank, Depends("bank")], order_id: str) -> str:
    """Cancels a standing order or scheduled transfer.

    :param order_id: id from list_scheduled_transfers, e.g. "so1".
    """
    o = next((o for o in bank.standing_orders if o.id == order_id), None)
    if o is None:
        raise ValueError(f"Unknown scheduled transfer {order_id}")
    if not o.active:
        return f"{order_id} was already cancelled"
    o.active = False
    return f"Cancelled {order_id}: {_money(o.amount)} {o.frequency} to {o.recipient_name or o.recipient_iban}"


def pay_credit_card_bill(bank: Annotated[Bank, Depends("bank")], card_id: str, amount: float, from_account: str | None = None) -> str:
    """Pays off (part of) a credit card bill from one of the user's accounts.

    :param card_id: the credit card as the user named it ("credit", last 4 digits) or its id from list_cards.
    :param amount: amount in INR (at most the amount owed).
    :param from_account: optional account id, name or type to pay from (default: the card's linked account).
    """
    card = find_card(bank, card_id)
    if card.kind != "credit":
        raise ValueError(f"{card_id} is not a credit card")
    if amount > card.outstanding:
        raise ValueError(f"Invalid amount: only {_money(card.outstanding)} is owed on {card.name}")
    acct = find_account(bank, from_account or card.account_id)
    _debit(bank, acct, f"CARD-{card.last4}", card.name, amount, f"{card.name} bill", "card_bill")
    card.outstanding = round(card.outstanding - amount, 2)
    return f"Paid {_money(amount)} to {card.name} (…{card.last4}) from {acct.name}; still owed {_money(card.outstanding)}"


def open_fixed_deposit(bank: Annotated[Bank, Depends("bank")], amount: float, months: int, from_account: str | None = None) -> str:
    """Opens a new fixed deposit funded from one of the user's accounts.

    :param amount: amount in INR to lock in the deposit.
    :param months: term in months (3 to 120).
    :param from_account: only if the user named an account to fund it from; leave empty for the main savings account.
    """
    if not 3 <= int(months) <= 120:
        raise ValueError("months must be between 3 and 120")
    src = find_account(bank, from_account)
    _check_debit(bank, src, amount)
    rate = 6.5 if months < 12 else 7.1 if months < 36 else 7.4
    y, m, d = (int(x) for x in TODAY.split("-"))
    m += int(months)
    maturity = date(y + (m - 1) // 12, (m - 1) % 12 + 1, min(d, 28)).isoformat()
    n = len(bank.accounts) + 1
    fd = Account(id=f"acc{n}", name=f"Fixed Deposit ({int(months)} months)", kind="fixed_deposit",
                 iban=f"IN07ACME{552189 + n:010d}", balance=0.0, interest_rate=rate, maturity=maturity, daily_limit=0.0)
    bank.accounts.append(fd)
    _debit(bank, src, fd.iban, fd.name, amount, "Fixed deposit", "deposit")
    return f"Opened {fd.id} {fd.name}: {_money(amount)} at {rate}% until {maturity} (funded from {src.name})"


def close_fixed_deposit(bank: Annotated[Bank, Depends("bank")], deposit_account: str, to_account: str | None = None) -> str:
    """Breaks a fixed deposit early (1% interest penalty) or closes it at maturity, paying it into another own account.

    :param deposit_account: fixed deposit account id or name.
    :param to_account: account to receive the money (default: main savings).
    """
    fd = find_account(bank, deposit_account)
    if fd.kind != "fixed_deposit" or fd.status == "closed":
        raise ValueError(f"{fd.name} is not an open fixed deposit")
    dst = find_account(bank, to_account)
    if dst is fd or dst.kind == "fixed_deposit":
        raise ValueError("Pay the deposit into a savings, salary, private, joint or current account")
    early = TODAY < fd.maturity
    payout = round(fd.balance * (0.99 if early else 1.0), 2)
    fd.balance, fd.status = 0.0, "closed"
    _credit(bank, dst, payout, f"{fd.name} closed" + (" early" if early else ""), fd.iban, "deposit")
    return f"Closed {fd.name}{' early (1% penalty)' if early else ''}: {_money(payout)} paid into {dst.name}"


# ------------------------------------------------------------------ tools (settings)
def add_payee(bank: Annotated[Bank, Depends("bank")], name: str, iban: str, nickname: str = "") -> str:
    """Saves a new payee (name and IBAN) to the user's address book at the bank.

    :param name: payee name.
    :param iban: payee IBAN.
    :param nickname: optional short name.
    """
    if len(_norm(iban)) < 15 or not _norm(iban)[:2].isalpha():
        raise ValueError(f"Invalid IBAN '{iban}'")
    if any(_norm(p.iban) == _norm(iban) for p in bank.payees):
        raise ValueError(f"{iban} is already a saved payee")
    pid = f"p{max([int(p.id[1:]) for p in bank.payees if p.id[1:].isdigit()], default=0) + 1}"
    bank.payees.append(Payee(id=pid, name=name.strip(), iban=iban.strip(), nickname=nickname.strip()))
    return f"Saved payee {pid}: {name.strip()} ({iban.strip()})"


def remove_payee(bank: Annotated[Bank, Depends("bank")], payee_id: str) -> str:
    """Removes a saved payee.

    :param payee_id: id from list_payees, e.g. "p2".
    """
    before = len(bank.payees)
    bank.payees = [p for p in bank.payees if p.id != payee_id]
    if len(bank.payees) == before:
        raise ValueError(f"Unknown payee {payee_id}")
    return f"Removed payee {payee_id}"


def freeze_card(bank: Annotated[Bank, Depends("bank")], card_id: str) -> str:
    """Freezes a card (no new charges) e.g. when it is lost.

    :param card_id: the card as the user named it ("debit", "credit", "business debit", last 4 digits) or its id from list_cards.
    """
    card = find_card(bank, card_id)
    card.status = "frozen"
    return f"Froze {card.name} (…{card.last4})"


def unfreeze_card(bank: Annotated[Bank, Depends("bank")], card_id: str) -> str:
    """Unfreezes a frozen card.

    :param card_id: the card as the user named it ("debit", "credit", "business debit", last 4 digits) or its id from list_cards.
    """
    card = find_card(bank, card_id)
    card.status = "active"
    return f"Unfroze {card.name} (…{card.last4})"


READ_TOOLS = [get_balance, list_accounts, get_transactions, list_payees, list_scheduled_transfers, list_cards]
ACTION_TOOLS = [send_money, transfer_between_accounts, schedule_transfer, cancel_scheduled_transfer, pay_credit_card_bill,
                open_fixed_deposit, close_fixed_deposit, add_payee, remove_payee, freeze_card, unfreeze_card]
# Tools beyond the original three (get_balance, get_transactions, send_money), offered in the Playground/Workspace.
EXTRA_TOOLS = [list_accounts, list_payees, list_scheduled_transfers, list_cards, transfer_between_accounts, schedule_transfer,
               cancel_scheduled_transfer, pay_credit_card_bill, open_fixed_deposit, close_fixed_deposit, add_payee, remove_payee,
               freeze_card, unfreeze_card]


def upgrade(old: object) -> Bank:
    """A Bank pickled before the multi-account bank (fields iban/balance/history only) -> the current Bank, keeping
    its payments and the main account's balance. Already-current banks are returned unchanged."""
    d = dict(getattr(old, "__dict__", {}) or {})
    if "accounts" in d:
        return old  # type: ignore[return-value]
    hist = [Transaction(**{k: v for k, v in dict(getattr(t, "__dict__", {})).items() if k in Transaction.model_fields})
            for t in d.get("history", [])]
    bank = Bank(history=hist)
    if isinstance(d.get("balance"), (int, float)):
        bank.primary.balance = float(d["balance"])
    return bank
