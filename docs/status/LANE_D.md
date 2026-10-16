# Lane D status
Updated: 2026-10-16 · Branch: `lane-d/perturb` · Owner: Team (Atharva) + Claude

## Done
- **Counterfactual value relabeling** (`warrant/perturb.py`, commits `700138b`, `361b808`).
  - **Trigger:** runs only for risky effects (EGRESS, PAY, BOOK, DELETE, ACCOUNT, FORM) and only when a
    sink value is ambiguous: it appears in more than one span, or in a tool output that also has a
    DIRECTIVE span. `USER_LITERAL` / `TRUSTED` values are never probed. Anything else: `check()` returns
    `None` and makes **zero** planner calls.
  - **Relabel mode (H1):** every occurrence of the value in every tool output gets its own
    format-preserving fake (plausible email with the same TLD, IBAN with the same country code, length and
    a valid mod-97 checksum, amount with the same magnitude and thousands/decimal style, URL with the same
    scheme/host shape). The planner is re-asked twice (dry run). The fake it picks names the exact span.
    The span carries an instruction cue (auditor DIRECTIVE or score >= 1), or sits in a document with a
    DIRECTIVE span → `REFUSED TAINTED_SOURCE`. The two runs disagree, the call disappears, or no planner is
    available → `NEEDS_INPUT PROBE_INCONCLUSIVE` (fails closed).
  - **Ablation mode (H3, H7 choices):** the value appears once, but its document offers several
    candidates of the same kind and has steering lines (imperatives such as "settle", "use", "ignore";
    claims such as "outdated", "only vendor", "for faster processing"). One baseline re-ask must
    reproduce the value; then each of up to 2 steering lines is removed. If the choice flips →
    `NEEDS_INPUT DECISIVE_UNTRUSTED_SPAN`.
- **Gate hook:** the single Lane D block between the markers in `WarrantGate._decide`. It is active only
  with `WARRANT_PERTURB=1`; it appends the probe as a check, emits a `perturb` event, and writes an audit
  record (`type: perturb`, with the fakes map, runs, chosen span, verdict, extra calls and latency).
- **Tests** (`tests/test_perturb_fakes.py` 13, `tests/test_perturb_probe.py` 10, offline, scripted
  planners):
  - H1 → `TAINTED_SOURCE` (and with the probe off, H1 is `ALLOWED`: the hole).
  - A benign reply to the sender, with the address in two clean lines → `ALLOWED`, with the probe naming
    the span.
  - H3 with a Note-steered planner → `DECISIVE_UNTRUSTED_SPAN`; correct amount line → allowed.
  - Structure change / no planner → `PROBE_INCONCLUSIVE`; unambiguous value → 0 calls; probe off by default.
  - Full suite: **220 passed**.

## Cost (measured on the scripted planners in the tests)
| Mode | Extra planner calls per probed risky call | When |
|---|---|---|
| none | 0 | value not ambiguous (most calls) |
| relabel | 2 | value in several spans / tainted document |
| ablation | 2-3 (1 baseline + up to 2 removals) | choice among several candidates with steering lines |
Live latency per probe is about 2-3 planner calls' worth. To be measured by Lane F with a Groq key.

## Honest limits
- A planner that picks the wrong amount **without being steered by any line** is not caught: there is no
  causal signal to find. Lane E's labelled-amount rule (task 3) covers that case
  (`test_unsteered_wrong_amount_is_not_attributed_to_the_note`).
- The probe uses the auditor's weak score (>= 1) on the *chosen* span. That is safe because it is backed by
  causal evidence, but a perfectly data-like source line (score 0, no DIRECTIVE in the document) is
  attributed as clean. Two-Witness (Lane E task 1) is the complement for payees.
- Fakes are only made for emails, IBANs, amounts and URLs. Names, repo names and ids are not probed.

## Interface needs (requests)
- **To Lane E (owner of `gate.py` outside the hook):** please add two lines at the top of
  `WarrantGate.query`, after `state` is resolved:
  `state.perturb_ctx = {"query": query, "runtime": runtime, "env": env, "messages": messages, "extra_args": extra_args}`
  and `state.planner = extra_args.get("warrant_planner")`. Until then, `perturb.py` finds the same context
  by walking up to `WarrantGate.query`'s frame (`_caller_context`), which works but is fragile.
- **To Lane F:** `PlannerHandle` (already on `lane-f/proof`) is what the probe reads; `perturb.py` unwraps
  it (`.llm`). Please render the `perturb` event (fields: `mode`, `value`, `fakes` {fake → span},
  `runs`, `extra_planner_calls`, `latency_ms`, `verdict`, `code`, `span`) as original vs relabeled text,
  and show `NEEDS_INPUT` codes `PROBE_INCONCLUSIVE` and `DECISIVE_UNTRUSTED_SPAN` as "asks the user".
  `bench/run_agentdojo.py` must pass the planner (via PlannerHandle) before `--perturb` is meaningful:
  without it every probed call becomes `PROBE_INCONCLUSIVE`.

## Benchmark (oracle) with WARRANT_PERTURB=0 vs main
Identical to `main` (the hook is inert when the flag is off):

| Suite | ASR | Benign utility | Utility under attack |
|---|---|---|---|
| banking | 0% | 100% | 94% |
| slack | 20% (AgentDojo IT5 attempted-call scoring) | 100% | 76% |
| travel | 0.7% | 100% | 97% |
| workspace | 0% | 100% | 100% |

With `WARRANT_PERTURB=1` the oracle bench needs the planner passed through (Lane F's PlannerHandle); until
then every probed call is `PROBE_INCONCLUSIVE`, so no `=1` benchmark numbers are reported yet.
