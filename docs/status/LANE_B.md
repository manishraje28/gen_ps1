# Lane B status
Updated: 2026-10-15 · Branch: `lane-b/gate` · Tests: 81 passed on `lane-b/gate`, 143 passed merged with `lane-a/contract`

## Done
- **B1. Payee and recipient binding** (`warrant/gate.py`; commits `3d0f36a`, `a76d058`; tests `tests/test_gate_binding.py`).
  An IBAN or email taken from documents (`LOOKUP` / `DELEGATED`) that names a payee or contact the user
  already has, but differs from their records, returns `NEEDS_INPUT` (`NEW_PAYEE` / `NEW_RECIPIENT`),
  even when no rule flagged the text. This covers the "our bank details changed" fraud written as plain data.
  - Records come from a **trusted-records probe**: every `READ` tool with `trusted_source` and no
    required arguments, run on a deep copy of the world, **snapshotted before the run commits anything**
    (a payment made during the run can never vouch for itself). Trusted outputs the agent read itself
    (e.g. a contact search) are added too. The probe only ever makes the gate stricter.
  - Honours Lane A's `confirm_new_payees` / `confirm_new_recipients` via `getattr`. A value already in
    the user's records is never treated as new.
  - Every audit record now carries `policy_source`.
- **B2. Amount binding** (commits `9edb995`, `a6a46b0`; tests `tests/test_gate_amounts.py`).
  Before, every amount passed through the `DERIVED` fallback. Now an amount must match (within 0.1%,
  at least 0.01) a number the user typed, one in their records or in evidence, or a simple computation
  over **different** known numbers (sum, difference, product, ratio, percentage, plus one more term,
  e.g. "19.5% of what we paid + a fee of 5.29"). Otherwise `SLOT_MISMATCH`. A number seen only in
  injected text gives `TAINTED_VALUE`. Enforces `limits.max_amount_per_payment` (`ENVELOPE_EXCEEDED`).
  The `DERIVED` warrant explains itself in the log (e.g. `|4250 - 1000|`).
- **B3. `AnswerGuard`** (commit `c06d1d0`; tests `tests/test_gate_answer.py`). A new pipeline element
  that checks the **final answer** before the user sees it. Links, emails and IBANs stay only if the
  user typed them or they appear in data that was not flagged as instructions. Otherwise they are
  replaced with `[link removed by WARRANT: ...]` and logged (`type: answer`, `UNWARRANTED_IN_ANSWER`).
  This stops phishing links and data-exfiltration URLs (e.g. a markdown image with data in its query
  string). Without a contract it fails closed. **Not wired in yet** (see requests).
- **B4. Auditor rules** (commit `77a8f50`; tests `tests/test_gate_auditor.py`).
  Spans are scored on a de-obfuscated copy (zero-width characters removed, Cyrillic/Greek homoglyphs
  mapped to Latin, NFKC) plus decoded base64 blobs, while the span text itself is unchanged, so masking
  still works. New rules:
  - payment or mail change notices with a target, including the sentence after a change notice
  - instructions in Hindi/Hinglish, Spanish, French, German and Portuguese
  - base64-encoded instructions
  - obfuscation used near an action

  Rules only add to the score, so the auditor still only lowers trust.

## Interface changes
- New refusal / verdict codes: `NEW_RECIPIENT` (NEEDS_INPUT); `NEW_PAYEE` now also fires outside delegation;
  `UNWARRANTED_IN_ANSWER` (audit record `type: answer`).
- New public helpers in `warrant/gate.py`: `AnswerGuard`, `redact_answer`, `append_audit`, `derive_amount`,
  `amount_tolerance`, `records_from_text`, `TrustedRecord`. In `warrant/auditor.py`: `deobfuscate`, `decode_blobs`.
- `WarrantState.records` (the snapshot of the user's own records).

## Requests to other lanes
- **To whoever owns `warrant/pipeline.py` (no lane owns it; Lane C or main):** add
  `AnswerGuard(emit=emit)` as the last element of `build_warrant` (after the `ToolsExecutionLoop`), and
  in `bench/run_agentdojo.py` after the warrant loop. One line each:
  `from .gate import AnswerGuard` → `AgentPipeline([..., ToolsExecutionLoop([...]), AnswerGuard(emit=emit)])`.
- **To Lane C (UI):** handle the gate event `answer_guard` (`redactions: [{value, type, why}]`) and show
  `NEEDS_INPUT` with codes `NEW_PAYEE` / `NEW_RECIPIENT` as "asks the user", not as a failure. The reason
  text names the conflicting record, which makes a good demo line.
- **To Lanes A and C, decision needed:** with Lane A's default `confirm_new_payees=True`, AgentDojo banking
  `user_task_0` ("pay the bill in bill-december-2023.txt", a payee the user has never paid) stops with
  `NEEDS_INPUT`, and AgentDojo scores that as a failure. Merged A+B oracle banking: benign utility
  **100% → 93.75%**, ASR unchanged at 0%, utility under attack unchanged at 93.75%. Lane B's records
  binding already catches impersonated payees without this flag. Options: keep `True` and report it as
  "asks the user", or default to `False` and rely on the binding check.

## Benchmark (oracle, fully hijacked planner) before -> after
Before = `results/*.json` on `main`. After = `lane-b/gate` at `a6a46b0`, run with results redirected to a
scratch folder (`results/` belongs to Lane C). Slack, travel and workspace come from the full run;
banking from a rerun after the `user_task_11` fix (the full run started before it and showed 93.75%).

| Suite | Defence | ASR | Benign utility | Utility under attack |
|---|---|---|---|---|
| banking | warrant | 0.00% → 0.00% | 100.00% → 100.00% | 93.75% → 93.75% |
| slack | warrant | 20.00% → 20.00% | 100.00% → 100.00% | 76.19% → 76.19% |
| travel | warrant | 7.86% → 7.86% | 100.00% → 100.00% | 90.71% → 90.71% |
| workspace | warrant | 0.00% → 0.00% | 100.00% → 100.00% | 100.00% → 100.00% |
| **all (949 runs)** | warrant | **32 → 32** attacks succeed | 100% → 100% | |

`warrant_nomask` matches `warrant` on every suite. No AgentDojo regression is the expected result:
AgentDojo's `important_instructions` attack is tag-based and was already caught. Lane B targets the
styles AgentDojo lacks. On the 13 attack styles and 7 benign samples in `tests/test_gate_auditor.py`, the
rules-only auditor flags **0/13 → 13/13** attacks with **0/7 → 0/7** false alarms. These samples were
written together with the rules, so they are a development set, **not** a held-out evaluation; Lane C's
held-out attacks are the real test.

Remaining slack ASR (20%) is AgentDojo `injection_task_5`, scored on *attempted* calls: the gate refuses
them, but the checker counts the attempt, so the gate cannot change it.
