# Round 2 integration merge

Merged on 2026-10-16 from branch `integrate/round2` into `main`, in the order set by `docs/NEXT_LANES.md`
(E, then D, then F), plus the round-2 hand-off branch `lane-a/agentdyn`.

| Branch | Commits | Conflicts |
|---|---|---|
| `lane-e/harden` (gate hardening) | 3 | none |
| `lane-d/perturb` (counterfactual relabeling) | 4 | `warrant/gate.py`: Lane E's new secrecy check sits just before Lane D's hook markers; both kept, the hook block stays between its markers. One D test now accepts `REFUSED` as well as `NEEDS_INPUT` for H3, because Lane E's amount rule refuses that payment on its own. |
| `lane-f/proof` (honest metrics, held-out, wiring) | 17 | none |
| `lane-a/agentdyn` (AgentDyn port, AutoDojo replay) | 4 | `bench/run_agentdojo.py`: kept Lane F's multi-attack loop and `DEFAULT_ATTACK`, plus Lane A's `DYNAMIC_SUITES` / lazy oracle. `contract.py` / `policy.py` merged automatically. |

Checks on the merged code: **270 tests pass**; the AgentDojo effect audit is unchanged (no task loses a
needed effect); the held-out phrasing probe is unchanged (31/31 commands, 20/20 questions clean).

## What each lane contributed

**Lane E: gate hardening** (`warrant/gate.py`, `ledger.py`, `auditor.py`, `contract.py`, `policy.py`)
- Two-Witness for new payees (H2): a payee IBAN that is not typed by the user or in their records needs one
  independent witness, or the gate asks the user (`NEW_PAYEE`).
- Document taint (H1): a document with an instruction in it cannot vouch for recipients / IBANs / URLs (`TAINTED_DOCUMENT`).
- Amounts (H3, H4): document amounts bind only next to a due label; arithmetic only when the user asked for it.
- Origin stamps (H8): values the agent wrote into the user's records cannot later count as the user's own.
- Quiet refusals: the planner gets one generic refusal for data-caused blocks (switch `WARRANT_QUIET_REFUSALS`).
- Secrecy labels (H5, H6): confidential values cannot leave in bodies, subjects or URLs (`SECRET_EGRESS`).
- Clause-level masking (H9): only the instruction clause of a sentence is masked.

**Lane D: counterfactual value relabeling** (`warrant/perturb.py`, one hook block in `gate.py`)
- For an ambiguous value in a risky call, swap each copy for a different realistic fake, re-ask the planner
  twice, and see which copy it uses; refuse if that copy is in an instruction (`TAINTED_SOURCE`).
- For choices (amounts, vendors), remove steering lines one at a time; if the choice flips, ask the user
  (`DECISIVE_UNTRUSTED_SPAN`). Unclear results fail closed (`PROBE_INCONCLUSIVE`).
- Off by default (`WARRANT_PERTURB=1` to enable). With it off, the oracle benchmark is identical.

**Lane F: proof and wiring** (`bench/`, `web/`, `warrant/pipeline.py`, `results/`, `README.md`, `CLAUDE.md`)
- `AnswerGuard` wired last in every WARRANT pipeline; `PlannerHandle` passes the planner to Lane D's probe; `--perturb` flag.
- Honest metrics: official vs executed ASR (slack's attempted-call scoring), attackable-pair denominator
  (340 of 949 AgentDojo pairs can never succeed), asked-user rates.
- Frozen held-out set (`bench/heldout.py`): 15 attacks + 3 benign look-alikes; multi-attack runs; UI updates.

**Lane A (round-2 hand-off): AgentDyn and AutoDojo** (`warrant/policy.py`, `contract.py`, `bench/`)
- Tool policies and command rules for AgentDyn's github / shopping / dailylife suites (new `FORM` effect, `RECORD` sink).
- AgentDyn-capable oracle (recomputes ground truth, fills one-time passwords) and `bench/corrections.py`
  (effect-based rescoring of three unsound benchmark oracles, after ROPE).
- Transfer replay of AutoDojo's optimised injections (results in `docs/status/LANE_A.md`, measured before E and D).

## Numbers on the merged code (oracle = fully hijacked planner, scratch runs)

AgentDojo, `important_instructions`, WARRANT:

| Suite | ASR official | ASR executed | Benign utility | Utility under attack | Asked user (benign / attacked) |
|---|---|---|---|---|---|
| banking | 0% | 0% | 93.75% | 93.75% | 6% / 0% |
| slack | 20% | **0%** | 100% | 52.38% | 0% / 24% |
| travel | 0.71% | 0.71% | 100% | 97.14% | 0% / 0% |
| workspace | 0% | 0% | 100% | 100% | 0% / 0% |

- Banking's benign loss is `user_task_0` (a payee never paid before, no witness): the gate asks the user.
- Slack's lower utility under attack is the gate asking before using an address found on an attacked page.

Held-out set (`python -m bench.heldout --planner scripted`, fully hijacked scripted planner):

| | Attacks that succeed | Asked user | Benign completed |
|---|---|---|---|
| no defence | 15 / 15 | 0 | 3 / 3 |
| WARRANT before round 2 (`results/heldout`, Lane F) | 8 / 15 | 1 | 3 / 3 |
| **WARRANT, merged (probe off)** | **2 / 15** | 3 | **3 / 3** |
| WARRANT, merged, `WARRANT_PERTURB=1` | 2 / 15 | 7 | 1 / 3 |

The probe-on row is not a fair measure yet: the held-out harness does not pass its planner to the probe, so
the probe falls back to asking the user. Keep `WARRANT_PERTURB` off until Lane F wires it in.

## Still open
- Held-out H7 (choice steering) and X7 (polite outsider asks for a confidential file) still succeed.
- AgentDojo's `direct` / `ignore_previous` / `system_message` attacks were getting through before round 2
  (Lane F's sweep); re-run that sweep on this merge.
- AutoDojo replay and AgentDyn numbers in `LANE_A.md` predate Lanes E and D; re-run them.
- Live Groq runs (needs a key in `.env`) and Lane F's adaptive attacker.
