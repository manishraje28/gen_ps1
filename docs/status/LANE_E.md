# Lane E status
Updated: 2026-10-16 · Branch: `lane-e/harden` · Tests: 221 passed, 1 failed (`test_chat_needs_key`, pre-existing: it expects no GROQ key, but `.env` has one)

## Done (tests: `tests/test_harden_holes.py`, each hole + a paired benign case)
- **Marker lines** for Lane D's hook in `WarrantGate._decide` (commit `lane-e: marker lines ...`).
- **E1 Two-Witness (H2).** A payee IBAN that is not USER_LITERAL / TRUSTED / in records / confirmed needs one
  independent witness: the payee's name or email domain in the user's records, or a known sender domain that names
  the payee. Counted by principal; the user's own history is never a witness for a value the agent wrote there.
  No witness → `NEEDS_INPUT NEW_PAYEE` (reason names what is missing). `confirm_new_payees` is now False from the
  compiler; org policy can still force it on. The records probe now also lists trusted searches with an empty query (contacts).
- **E2 Document taint (H1).** If a document (one tool-output item) has a DIRECTIVE span, its recipients / IBANs / URLs
  lose LOOKUP unless also in a clean document, in records, the document's own sender, or witnessed → `NEEDS_INPUT TAINTED_DOCUMENT`.
  New auditor rule: a line addressed to the agent by role (`Assistant: ...`).
- **E3 Amounts (H3, H4).** Document numbers bind only next to a due label (amount due, total, balance, rent, fee...)
  or a word the user used. DERIVED only when the user asked for arithmetic (or delegated), over user / records / labelled numbers.
- **E4 Origin stamps (H8).** `WarrantState.origins`: committed payees / recipients / links are stamped with their warrant
  kind. LOOKUP/DELEGATED stamps downgrade TRUSTED provenance and are not "in records". `confirm_value()` stamps CONFIRMED and logs it.
- **E5 Quiet refusals.** Data-caused codes give the planner one generic `WARRANT_REFUSED:` sentence; after 3 of the same
  code, that effect is refused for the run (`REPEATED_REFUSALS`). Env `WARRANT_QUIET_REFUSALS` (default 1). `state.asked_user` counts NEEDS_INPUT.
- **E6 Secrecy (H5, H6).** Spans from payroll / salary / HR / password / confidential sources are `secret`; their distinctive
  values in any free-text argument (or a READ tool's URL) → `REFUSED SECRET_EGRESS` unless the request named that source. H6 is also NO_PROVENANCE.
- **E7 Clause masking (H9).** A flagged sentence is split at `, and` / `;` / `but` / `also`; a clause scoring 0 with an amount / IBAN / email stays EVIDENCE.

## Benchmark (oracle, warrant, results in a scratch dir)
| Suite | ASR before → after | Benign before → after | Utility under attack before → after |
|---|---|---|---|
| banking | 0/144 → 0/144 | 100% → 93.75% | 93.75% → 93.75% |
| slack | 21/105 → 21/105 | 100% → 100% | 76.19% → 52.38% |
| travel | 1/140 → 1/140 | 100% → 100% | 97.14% → 97.14% |
| workspace | 0/560 → 0/560 | 100% → 100% | 100% → 100% |

- Banking benign loss = `user_task_0` (new payee from `bill-december-2023.txt`, no witness): asks the user, as intended.
- Slack utility-under-attack loss = user tasks 2, 11, 16, 17, 20: Dora's e-mail is on the attacked web page, so the gate
  asks the user (`TAINTED_DOCUMENT`, 25 runs). These are "asked user" outcomes, not attacks; Lane D's probe could clear them.
- No AgentDojo ASR change was expected: AgentDojo's attacks were already blocked; H1-H9 are the target.

## Requests to other lanes
- **Lane F:** report `NEEDS_INPUT` (NEW_PAYEE, TAINTED_DOCUMENT) as "asked user"; `state.asked_user` has the count.
  Carry `state.origins` across Playground turns in `CarryLedger` (`state.origins = lane.origins`, save after the run),
  and call `warrant.gate.confirm_value(state, value)` when the user confirms a NEEDS_INPUT. Measure `WARRANT_QUIET_REFUSALS=0/1`.
- **Lane D:** new `Span.doc` / `Span.secret` and `state.docs` (DocInfo) are there if useful; hook markers untouched.
