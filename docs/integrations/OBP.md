# Sandbox banking with the Open Bank Project (OBP)

Module: `integrations/obp.py`. Tests: `tests/test_integ_obp.py` (offline, OBP's HTTP API mocked).

The [Open Bank Project](https://www.openbankproject.com) runs a public **sandbox** bank API with demo banks,
accounts and test money. WARRANT can use it instead of the simulated Acme bank, so balances, transaction history
and payments are real API calls, while **no real money ever moves**. Only sandbox hosts are accepted.

| Tool | OBP call (v4.0.0, view `owner`) | Gate policy |
|---|---|---|
| `get_balance()` | `GET /obp/v4.0.0/banks/{BANK}/accounts/{ACC}/owner/account` | READ, trusted source |
| `get_transactions(n)` | `GET .../owner/transactions` | READ, trusted source |
| `list_payees()` | `GET .../owner/other_accounts` | READ, trusted source |
| `send_money(recipient, amount, subject)` | `POST .../owner/transaction-request-types/SANDBOX_TAN/transaction-requests` | PAY; `recipient` → IBAN sink, `amount` → AMOUNT sink |

A payee is written `BANK_ID/ACCOUNT_ID`, which is how it appears in the transaction history and payee list, so
WARRANT can trace exactly where each payee came from (your words, your records, or a document).

## Setup: click by click (you do this; never use a real bank account)

1. **Create a sandbox user.** Go to <https://apisandbox-portal.openbankproject.com/register>, sign up with any
   email and a new password (this is your sandbox **username / password**, used for DirectLogin), and confirm the
   email if asked.
2. **Get a consumer key.** Go to <https://apisandbox-portal.openbankproject.com/consumers/register>, sign in, and
   register an application:
   - Application type: **Confidential**, name `WARRANT demo`, your email.
   - Redirect URL: `http://localhost:8000` (not used by DirectLogin, but the form needs one).
   - Submit, then copy the **Consumer Key**. You don't need the secret for DirectLogin.
3. **Find your bank and account.** Open the API Explorer <https://apiexplorer-ii-sandbox.openbankproject.com>,
   log in with the sandbox user and run **Get Accounts held** (`GET /my/accounts`). Note one account's
   `bank_id` and `id`. These are `OBP_BANK_ID` and `OBP_ACCOUNT_ID`.
   - If you have no account yet, create one in the API Explorer with **Create Account**
     (`PUT /banks/BANK_ID/accounts/ACCOUNT_ID`) on a sandbox bank listed by **Get Banks**.
   - Create (or pick) a second account to be the "payee", e.g. `Mumbai Power Ltd`. Make a first payment to it from
     the explorer (or with `--pay`, below), so it appears in your history as a known payee.
4. **Add to `.env`** (never commit it):
   ```
   OBP_BASE_URL=https://apisandbox.openbankproject.com
   OBP_API_VERSION=v4.0.0
   OBP_USERNAME=<sandbox username>
   OBP_PASSWORD=<sandbox password>
   OBP_CONSUMER_KEY=<consumer key>
   OBP_BANK_ID=<bank_id from step 3>
   OBP_ACCOUNT_ID=<account id from step 3>
   OBP_CURRENCY=EUR
   ```
5. **Check it.**
   ```bash
   python -m integrations.obp
   ```
   This prints `logged in ...`, the balance, your payees and recent transactions. To see the exact payment request
   without sending it: `python -m integrations.obp --preview BANK_ID/ACCOUNT_ID 1.00 "test"`. To make one sandbox
   payment: `--pay` instead of `--preview`.

## Preview-then-commit (shadow execution)

The gate's shadow step deep-copies the environment and runs the call on the copy. `OBPAccount.__deepcopy__`
returns a **dry-run** account, so in the shadow run `send_money` makes **no API call**. It appends the exact
transaction request body to `obp.pending`, which shows up in the gate's state diff. The real POST happens only when
the gate runs the call on the original environment, after every check passes. The tests check this: one POST for an
allowed payment, zero for an injected payee.

A SANDBOX_TAN request may come back `INITIATED` (the sandbox asks for a challenge answer for larger amounts); the
tool reports the status instead of retrying.

## Wiring it into the app (follow-up)

Like the Stripe module, this is not wired into the Workspace yet. To use it, add a field `obp: OBPAccount` to the
environment model (the `Depends("obp")` dependency), set it to `make_obp_account()`, and swap the demo bank tools for
`integrations.obp.TOOLS` when `obp_tools()` returns `"obp_sandbox"`. Bind only the WARRANT lane's environment to the
real sandbox account; keep the undefended lane on the simulated bank.
