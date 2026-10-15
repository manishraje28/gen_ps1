# Lane A status
Updated: 2026-10-15 · Branch: `lane-a/contract` · Owner: Team (Atharva) + Claude

## Done
- **A1. Effects only for commands** (`warrant/contract.py`, commits `10b4430`, `96475ad`, `bb8cb73`).
  An effect is granted only when the user commands it: a verb in a command position (clause start,
  after "please / and / then / can you", or "want to / need to / your task is to"). Nouns and
  questions ("the latest email", "my rent payment", "a meeting", "tell me ...") grant READ only.
  Special cases: "send them the difference" → PAY (not EGRESS); "update my rent payment" → PAY;
  "update my address" → ACCOUNT; "write them the following message" → EGRESS; "share" needs someone to share with
  ("share X with Y"); "according to the file" is a lookup, not delegation.
  - Over all 97 AgentDojo v1.2.1 user tasks (rules-only contract):

    | Metric | Before | After |
    |---|---|---|
    | Tasks missing an effect they need | 0 | **0** |
    | Extra effects granted (not needed by ground truth) | 75 | **9** |
    | (user task, injection) pairs whose attack effect is granted but not needed | 140 | **11** |

    On every task the new rules grant a subset of what the old rules granted.
    The remaining extras are by design (delegation tasks get WRITE/EGRESS/PAY, bounded by the gate)
    or literal ("I want to book a hotel", "Please help us book a flight").
- **Contract LLM bounded:** the LLM can add an effect only if a trigger word for it appears in the
  request. Before, it could add any effect.
- **A2. New contract fields** (safe defaults, nothing removed or renamed). See *Interface changes*.
- **A3. Org policy** (`OrgPolicy`, `apply_org_policy`, `load_org_policy` in `warrant/contract.py`;
  example `policies/acme.example.yaml`). Loaded from env `WARRANT_ORG_POLICY`. It can only narrow:
  block effects (never READ), cap limits, force `USER_LITERAL` on sinks, disable delegation, switch
  confirmations on. Unknown keys and broken files raise (fail closed).
- Tests: `tests/test_contract_effects.py` (56 tests, including "no AgentDojo task loses a needed
  effect"). Full suite: **89 passed**.

## In progress
- Nothing. Open to requests from Lanes B and C.

## Interface changes (for Lane B)
Read these with `getattr(contract, name, default)` so B works before A merges.

| Field | Default | Gate behaviour Lane B should implement |
|---|---|---|
| `contract.confirm_new_payees` | `True` | IBAN not `USER_LITERAL`/`TRUSTED` (e.g. only `LOOKUP` from a bill) → `NEEDS_INPUT` `NEW_PAYEE` |
| `contract.confirm_new_recipients` | `True` | External recipient (not internal domain, not a contact) that is not `USER_LITERAL`/`TRUSTED` → `NEEDS_INPUT` `NEW_RECIPIENT` |
| `contract.limits.max_amount_per_payment` | `None` | If set, refuse a single payment above it (`ENVELOPE_EXCEEDED`) |
| `contract.policy_source` | `""` | Write it into every audit-log record |

Note for B: the current benign demo ("pay the Mumbai Water bill") relies on a `LOOKUP` IBAN from
the bill. If the payee is in the user's transaction history the IBAN is `TRUSTED`; otherwise
`NEEDS_INPUT` is the correct outcome, and Lane C's demo must show it as "asks the user", not as a
failure.

## Requests to other lanes
- **To all lanes:** commit AND push after every change (code, test or status update) so everyone
  always has full context. This rule is in `docs/LANES.md` section 3 (PR #2, waiting for a merge).
- **To Lane B:** enforce the four fields above. Effects are now narrower, so expect more
  `EFFECT_OUT_OF_SCOPE` refusals on read-only requests. That is intended.
- **To Lane C:** the README's "Known weaknesses → Over-broad effects" is fixed for the rules path;
  please update it after merge. Org policy usage: `WARRANT_ORG_POLICY=policies/acme.example.yaml`.
  The oracle numbers below come from a scratch run; regenerate `results/` on merged `main`.

## Benchmark (oracle, fully hijacked planner, rules-only contract) before → after
Before = `results/*.json` on `main`. After = branch `lane-a/contract` at `96475ad` (rerun on `bb8cb73` in progress).

| Suite | Defence | ASR | Benign utility | Utility under attack |
|---|---|---|---|---|
| banking | warrant | 0.00% → 0.00% | 100% → 100% | 93.75% → 93.75% |
| slack | warrant | 20.00% → 20.00% | 100% → 100% | 76.19% → 76.19% |
| travel | warrant | 7.86% → **6.43%** | 100% → 100% | 90.71% → **92.14%** |
| workspace | warrant | 0.00% → 0.00% | 100% → 100% | 100% → 100% |
| **all (949 runs)** | warrant | 32 → **30** attacks succeed (3.37% → **3.16%**) | 100% → 100% | |

`warrant_nomask` matches `warrant` on every suite.
