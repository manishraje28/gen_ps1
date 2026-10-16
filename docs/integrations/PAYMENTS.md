# Real payments (Stripe test mode)

Module: `integrations/payments.py`. Tests: `tests/test_integ_payments.py` (offline, HTTP mocked).

## Provider choice: Stripe test mode, not Payman

- **Payman** (paymanai.com) has a sandbox and a Python SDK (`paymanai`), and LangChain's integration
  lists send-payment, payee search and balance actions. But its public docs don't pin down stable REST
  endpoints or SDK method signatures for payees and payments. Sources: LangChain's Payman page and a
  community MCP server that mentions `TEST_RAILS` payees. Signing up also goes through an approval
  flow. A demo that has to work on stage can't depend on an API surface we can't verify offline.
- **Stripe test mode** is free and instant. Its REST API (`/v1/balance`, `/v1/transfers`, `/v1/accounts`)
  is documented and stable, and test keys (`sk_test_...`) can't move real money. The key prefix tells
  us whether a key is live. Every transfer shows up right away in the dashboard.

Mapping: the user's account is the Stripe platform's test balance. **Payees** are Connect test
connected accounts (`acct_...`). `send_money(recipient, amount, subject)` makes a Stripe **Transfer** to a payee.

| Tool | Stripe call | Policy |
|---|---|---|
| `get_balance()` | `GET /v1/balance` | READ, trusted_source |
| `get_transactions(n)` | `GET /v1/transfers` | READ, trusted_source |
| `list_payees()` | `GET /v1/accounts` | READ, trusted_source |
| `send_money(recipient, amount, subject)` | `POST /v1/transfers` | PAY; `recipient`→IBAN sink, `amount`→AMOUNT sink |

Policies are registered with `POLICIES.update(...)` when `integrations.payments` is imported.
`warrant/policy.py` is unchanged.

## Keys and fallback

- The key is read only from the environment or `.env`: `STRIPE_TEST_SECRET_KEY` (and optionally `STRIPE_TEST_CURRENCY`, default `usd`).
- Any `sk_live_` / `rk_live_` / `pk_live_` key, or anything that isn't `sk_test_` / `rk_test_`, raises `LiveKeyError`.
  If a response ever comes back with `livemode: true`, the call is aborted too.
- With no key, `make_payments_account()` returns `None` and `payment_tools()` returns `("simulated", demo bank tools)`,
  so the demo keeps working on `demo/env.py`'s bank.

## Preview-then-commit (shadow execution)

A real account can't be deep-copied. Today the gate's shadow step does
`shadow_env = env.model_copy(deep=True); runtime.run_function(shadow_env, ...)` (`warrant/gate.py` ~L941).
`PaymentsAccount.__deepcopy__` returns a **dry-run** copy, so in the shadow run `send_money` makes **no
API call**. It appends the exact transfer body (`amount` in cents, `currency`, `destination`, `description`)
to `payments.pending`, and the gate's existing `state_diff` shows that as the shadow diff.
The real `POST /v1/transfers` happens only when the gate runs the call on the original env, after every check passes.
The offline tests check this: one POST for an allowed payment, zero for a refused one.

**Hook request for the gate owner (optional, nicer UI; no change is needed for safety):**
for tools whose env field is a `PaymentsAccount`, emit `integrations.payments.preview_transfer(account, **call.args)`
as the `shadow` event's `diff`, labelled "would POST /v1/transfers". Also check
that the previewed `destination`/`amount` equal the warranted sink values before committing. Any exception
from `preview_transfer` must be treated as REFUSED (fail closed).

To wire it into an env, add a field `payments: PaymentsAccount` to the env model (it is the `Depends("payments")`
dependency), set it to `make_payments_account()`, and swap the demo bank tools for `integrations.payments.TOOLS`.
Note: the real tool's argument is `recipient` (an `acct_...` id), not `recipient_iban`.

## Setup: click by click (the user does this; Claude never creates accounts or handles keys)

1. Go to <https://dashboard.stripe.com/register> and create a free account (email, name, password). You don't need to
   activate payments or enter bank details. Test mode works straight away.
2. In the dashboard, make sure the **Test mode** toggle (top right, or the "Sandbox"/"Test" badge) is **on**.
3. **Developers → API keys** (<https://dashboard.stripe.com/test/apikeys>). Click **Reveal test key** next to *Secret key*
   and copy the value that starts with `sk_test_`.
4. Open `.env` in the repo root and set `STRIPE_TEST_SECRET_KEY=sk_test_...`. Never commit `.env`, and never paste a `sk_live_` key.
5. Enable Connect in test mode: go to **Connect** (<https://dashboard.stripe.com/test/connect/accounts/overview>) and click **Get started**.
   Choose the platform/marketplace option and finish the short questionnaire. Test mode needs no business verification.
6. Create payees: **Connect → Accounts → + Create** (test mode). Choose type *Express* or *Custom* and country = your platform's country.
   Use a test email (e.g. `billing@mumbaipower.test`) and set the business name (e.g. "Mumbai Power Ltd").
   For any onboarding form, use Stripe's test values from <https://docs.stripe.com/connect/testing>.
   Copy each `acct_...` id. These are the recipients the agent can pay.
7. Add test funds so transfers have an *available* balance: **Payments → + Create payment**, card number `4000 0000 0000 0077`
   (any future expiry, any CVC). That test card skips the pending period, so the money is available at once.
8. Restart the demo (`run_demo.bat`).

## Seeing what happened

- Transfers: **Connect → Transfers** (<https://dashboard.stripe.com/test/connect/transfers>). Each allowed `send_money` shows up here
  with its description (= `subject`).
- Balance: **Balances** (<https://dashboard.stripe.com/test/balance/overview>).
- Every API request the demo made: **Developers → Logs** (<https://dashboard.stripe.com/test/logs>). A refused payment leaves no `POST /v1/transfers` here.
