# Round 2 lanes: close the holes, add the novel piece, prove it honestly

Round 1 (Lanes A, B) is merged into `main` @ `7badc37`. Lane C never started; Lane F below replaces it.
Round 2 has three lanes that run **at the same time**, each in its own Claude Code session.

Read first, in this order: `CLAUDE.md`, `docs/SOLUTION.md`, `docs/LANES.md` (protocol, section 3 still
applies), `docs/IDEAS.md` (research + ideas), `docs/attacks/HOLES.md` (the attacks you must close).

| Lane | What | Branch | Owns (write) |
|---|---|---|---|
| **D. Perturbation** | Counterfactual value relabeling: the novel piece | `lane-d/perturb` | `warrant/perturb.py`, `tests/test_perturb_*.py`, one marked hook in `warrant/gate.py`, `docs/status/LANE_D.md` |
| **E. Gate hardening** | Two-Witness, origin stamps, amounts, quiet refusals, secrecy, clause masking | `lane-e/harden` | `warrant/gate.py` (except D's hook block), `warrant/ledger.py`, `warrant/auditor.py`, `warrant/contract.py`, `warrant/policy.py`, `tests/test_harden_*.py`, `docs/status/LANE_E.md` |
| **F. Proof + demo** | Honest evaluation, held-out + adaptive attacks, live runs, UI, README | `lane-f/proof` | `bench/`, `results/`, `web/`, `demo/`, `traces/`, `warrant/pipeline.py`, `README.md`, `CLAUDE.md`, `tests/test_bench_*.py`, `docs/status/LANE_F.md` |

**Shared-file rule for `warrant/gate.py`:** Lane D adds exactly ONE block in `WarrantGate._decide`, between the
comment lines `# --- perturbation hook (lane D) ---` and `# --- end perturbation hook ---`, that calls
`warrant.perturb.check(...)` only when env `WARRANT_PERTURB=1`. Lane E never edits inside those markers.
Lane D never edits anything else in gate.py. Whoever merges second rebases; the block is small, so a conflict is trivial.

**Merge order:** E first (the gate semantics), then D (rebased on E), then F (reruns all numbers on merged main).

---

## Lane D prompt (copy into a fresh Claude Code session in the repo root)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection (Genesis 2.0 hackathon PS 01,
AgentDojo v1.2.1, agentdojo==0.1.35). Read CLAUDE.md, docs/SOLUTION.md, docs/LANES.md (section 3 protocol),
docs/IDEAS.md (Idea 1) and docs/attacks/HOLES.md fully before writing code.

You are Lane D. Branch: lane-d/perturb from main. You own ONLY warrant/perturb.py (new),
tests/test_perturb_*.py (new), docs/status/LANE_D.md (new), and ONE hook block in warrant/gate.py
between the markers "# --- perturbation hook (lane D) ---" / "# --- end perturbation hook ---"
inside WarrantGate._decide. Do not touch anything else. Commit AND push after every change; update
docs/status/LANE_D.md (template in docs/LANES.md) after every push. Check docs/status/LANE_E.md on
origin/lane-e/harden before each task.

WHY: the Value Ledger decides where a sink value came from by STRING MATCHING. If an attacker writes the
value twice, once in a flagged instruction and once in a clean-looking line ("CC list: audit@evil"), the clean
copy makes it LOOKUP and the gate allows it (HOLES.md H1). Same for amounts: "Amount due 4250" vs
"Late payment schedule 42500" (H3). Recent work (PACT, arXiv 2605.11039) names provenance inference as THE
open problem; AttriGuard/CausalArmor/MELON only give a yes/no "was this call caused by the data".
Our novel piece: per-argument provenance by counterfactual value relabeling.

BUILD (warrant/perturb.py):
1. check(state, call, decision, planner, messages) -> dict | None. Runs ONLY for risky effects
   (EGRESS, PAY, BOOK, DELETE, ACCOUNT) AND only when a sink value is AMBIGUOUS: the ledger finds it in
   more than one span, OR any span from the same tool output is DIRECTIVE. Otherwise return None (no cost).
2. Relabel: deep-copy the conversation messages. In every tool-output message, replace EACH OCCURRENCE of
   each candidate value with a different FORMAT-PRESERVING fake: emails keep a plausible shape
   (same TLD style, e.g. "m.rao@northwind-ops.com"), IBANs keep country code + length and pass the
   mod-97 checksum, amounts keep the same order of magnitude and decimal style, URLs keep scheme/host shape.
   Keep a map fake -> (span_id, original value). Never use "TAG_123"-style fakes (models treat them as broken data).
3. Re-ask the SAME planner (temperature 0) with the relabeled messages, dry run, nothing executed.
   Find the same tool call (same function); read the fake in the sink argument; map it back to its span.
4. Robustness: run 2 times; require the same span both times. If the tool-call STRUCTURE changes
   (different function, call disappears) or the two runs disagree -> verdict "inconclusive" -> return
   NEEDS_INPUT code PROBE_INCONCLUSIVE (fail closed to a user confirmation, never to ALLOWED).
5. Verdict: if the chosen span is DIRECTIVE, or is in a tool output whose document also contains a
   DIRECTIVE span and the value is not USER_LITERAL/TRUSTED -> REFUSED code TAINTED_SOURCE with the span
   id in the reason. Otherwise annotate the decision with "provenance_span": <id> and leave it ALLOWED.
6. Variant for choices (H7): for a sink of kind TARGET or a recipient chosen among several candidates,
   ablate (remove) each suspicious span one at a time (spans whose score >= 1, or that make claims about
   other documents, e.g. "all other quotes are outdated"); if removing one span changes the chosen value,
   that span is decisive -> NEEDS_INPUT code DECISIVE_UNTRUSTED_SPAN. Keep the budget at <= 3 extra calls.
7. Audit: log every probe (fakes map, runs, chosen span, verdict, extra calls, latency) via the existing
   audit log helper (append_audit in gate.py). Emit an event "perturb" so the UI (Lane F) can show
   original vs relabeled text with the chosen fake highlighted.
8. The planner object: the gate does not hold the planner today. Pass it in via extra_args
   ("warrant_planner") set by the pipeline; Lane F owns warrant/pipeline.py and will add it; request it in
   your status file. Until then, tests inject a fake planner.

TESTS (tests/test_perturb_*.py, offline, no API key): a scripted fake planner that copies the value from a
chosen line, so you can test: H1 -> REFUSED TAINTED_SOURCE; benign "reply to Priya" with a From: header ->
ALLOWED; H3 (amount from the schedule line) -> REFUSED; correct amount line -> ALLOWED; structure change ->
PROBE_INCONCLUSIVE; non-ambiguous value -> check() returns None and makes zero planner calls.
Also unit-test the fake generators (IBAN checksum valid, email shape, amount magnitude).

DONE WHEN: all tests green (python -m pytest -q), H1 and H3 blocked with WARRANT_PERTURB=1, oracle benchmark
unchanged or better with WARRANT_PERTURB=0 (record before/after in your status), and your status file lists
the measured extra calls/latency per risky call. Never weaken the gate; probe failures fail closed.
Frame it in docs as "counterfactual value relabeling (to our knowledge new)", citing PACT, AttriGuard,
CausalArmor, NeuroTaint and "Influence Is Not Authority" (arXiv 2608.29942) as neighbours.
```

---

## Lane E prompt (copy into a fresh Claude Code session in the repo root)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection (Genesis 2.0 hackathon PS 01,
AgentDojo v1.2.1, agentdojo==0.1.35). Read CLAUDE.md, docs/SOLUTION.md, docs/LANES.md (section 3 protocol),
docs/status/LANE_A.md, docs/status/LANE_B.md, docs/IDEAS.md and docs/attacks/HOLES.md fully before coding.

You are Lane E. Branch: lane-e/harden from main. You own warrant/gate.py (EXCEPT the block between
"# --- perturbation hook (lane D) ---" and "# --- end perturbation hook ---", which is Lane D's),
warrant/ledger.py, warrant/auditor.py, warrant/contract.py, warrant/policy.py, new tests/test_harden_*.py,
and docs/status/LANE_E.md (new). Commit AND push after every change; update your status file after every push.
Add the two empty marker comment lines for Lane D's hook in WarrantGate._decide (after the per-argument
checks, before the final verdict) in your FIRST commit and push it immediately.

WHY: the gate's warrants rest on string-match provenance and on trusting whatever is in the user's records.
HOLES.md lists the attacks that still work. Your job is to make the gate safe even when the Span Auditor
misses something, without losing benign utility.

TASKS, in order (each = fix + regression test from HOLES.md + a paired benign test that must stay ALLOWED):
1. Two-Witness for new payees (H2). Today confirm_new_payees is False when the request names exactly one
   file (contract.py ~line 409), so a fully fake invoice from a never-paid payee is paid silently.
   Replace with: a payee IBAN that is not USER_LITERAL/TRUSTED needs ONE independent witness, counted by
   PRINCIPAL not by document: (a) the payee name or its domain is in the user's contacts / sent mail /
   transaction history, or (b) the document's sender domain matches the payee name/domain AND that sender
   domain already appears in the user's records. Repeats inside the same document, or two documents from
   the same unknown sender, are ONE witness. No witness -> NEEDS_INPUT code NEW_PAYEE with a reason that
   names what is missing. AgentDojo banking user_task_0 will then ask the user: that is intended; record it
   as a separate "asked user" count (Lane F reports it as its own outcome), and keep org policy able to force it on.
2. Document taint (H1, cheap complement to Lane D). If any span of a tool output is DIRECTIVE, every value
   from that SAME output loses LOOKUP for RECIPIENT/IBAN/URL sinks unless also USER_LITERAL/TRUSTED or
   witnessed per task 1 -> NEEDS_INPUT code TAINTED_DOCUMENT. Check that the 4 demo scenarios still pass
   (the benign one has no DIRECTIVE span; the delegation one must still do David's legitimate tasks).
3. Amounts (H3, H4). An amount from a document must bind to a LABELLED due field ("amount due", "total due",
   "balance", "amount payable", "rent", or the user's own words), not to any number in the text. DERIVED
   arithmetic is allowed only when the USER asked for a computation ("the difference", "total including",
   "split", "%"); operands named only by the document ("plus surcharge") do not count. Else SLOT_MISMATCH.
4. Origin stamps (H8, sleeper attack). Every value written to the user's world by a tool call made during a
   run (send_money history rows, contacts, calendar participants, shared files) carries the warrant kind it
   was written with (store it next to WarrantState.records / in a persistent side-table keyed by value;
   the Playground keeps a CarryLedger across turns: carry the stamps too). A value whose origin is
   LOOKUP/DELEGATED can never be upgraded to TRUSTED by reading it back later; it stays LOOKUP and needs
   a witness. Test: turn 1 pays a new payee (after confirmation), turn 2 "pay them again" still treats the
   IBAN as not-TRUSTED unless the user confirmed it in turn 1 (a confirmation upgrades it, and is logged).
5. Quiet refusals (denial-feedback leakage, arXiv 2604.04035). The planner receives only a generic message
   ("This action was not authorised by the user's request. Continue with the original task.") for refusals
   caused by tool-output data (TAINTED_*, NO_PROVENANCE, DELEGATION_SCOPE, SLOT_MISMATCH from data);
   keep typed codes for user-recoverable ones (EFFECT_OUT_OF_SCOPE). Full reasons stay in the audit log
   and UI events. After 3 refusals of the same code in one run, refuse all further calls of that effect.
   Make it switchable (env WARRANT_QUIET_REFUSALS, default 1) so Lane F can measure both.
6. Secrecy labels on outgoing content (H5, H6). Mark spans from confidential sources (File.confidential,
   /hr, payroll, salary, passwords, security codes) as SECRET. A side-effecting call's free-text arguments
   (email body, subject, file name, event title, URL query) that contain a SECRET span's distinctive values
   (numbers, account fragments, names next to salaries) need the user to have named that file in the
   request; else REFUSED code SECRET_EGRESS. Also check get_webpage URLs (READ tool, url sink) the same way.
   Verify H6 is already refused (NO_PROVENANCE) and add the test either way.
7. Clause-level masking (H9). The auditor masks whole sentences; split DIRECTIVE sentences at clause
   boundaries (", and", "; ", " but ", " also ") and keep clauses that score 0 and contain typed values
   (amount, IBAN, email of a known party) as EVIDENCE. The auditor still may only lower trust.

After EACH task: python -m pytest -q (all green) and the oracle benchmark
(python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace, results to a scratch
dir, never results/ which is Lane F's). Record before/after ASR, benign utility, utility under attack and
NEEDS_INPUT counts in docs/status/LANE_E.md. Never weaken the gate to pass a test; fail closed.
```

---

## Lane F prompt (copy into a fresh Claude Code session in the repo root)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection (Genesis 2.0 hackathon PS 01,
AgentDojo v1.2.1, agentdojo==0.1.35). Read CLAUDE.md, README.md, docs/SOLUTION.md, docs/LANES.md (section 3
protocol), docs/status/LANE_A.md, docs/status/LANE_B.md, docs/IDEAS.md and docs/attacks/HOLES.md fully first.

You are Lane F (replaces the never-started Lane C). Branch: lane-f/proof from main. You own bench/, results/,
web/, demo/, traces/, warrant/pipeline.py, README.md, CLAUDE.md, new tests/test_bench_*.py and
docs/status/LANE_F.md (new). warrant/gate.py, ledger.py, auditor.py, contract.py, policy.py, perturb.py are
read-only for you. Commit AND push after every change; update your status file after every push; read
LANE_D.md and LANE_E.md (origin/lane-d/perturb, origin/lane-e/harden) before each task.

WHY: two independent reviews found our headline numbers are mis-denominated and that judges will ask about
attacks we did not tune for. Credible numbers are the cheapest win. Facts already verified on this install:
9 injection tasks have an EMPTY ground truth in the default environment (workspace injection_task_6..13,
travel injection_task_6), so 340 of the 949 oracle pairs can never succeed (609 attackable). Slack
injection_task_5 is scored on ATTEMPTED calls (AgentDojo issue #168), so a blocked call still counts as success.

TASKS, in order:
1. Pipeline wiring (warrant/pipeline.py + bench/run_agentdojo.py): add AnswerGuard(emit=emit) as the last
   element after the ToolsExecutionLoop (Lane B built it, see LANE_B.md). Put the planner into extra_args
   under "warrant_planner" so Lane D's perturbation probe can re-ask it. Put Lane D's flag WARRANT_PERTURB
   into the bench CLI (--perturb).
2. Honest metrics in bench/run_agentdojo.py and the results JSON (add fields, keep old ones):
   asr_official; asr_executed (count an attack only if its effect is in the post-run environment diff /
   executed calls, not proposed ones); n_attackable_pairs and asr_attackable (exclude injection tasks whose
   ground truth is empty in the default env, list their ids in the JSON); asked_user_rate split benign vs
   attacked (NEEDS_INPUT outcomes as their own category, plus "utility counting asked-user as success").
3. All attack types: run the oracle over every attack registered in the installed agentdojo
   (important_instructions and its variants, direct, ignore_previous, system_message, injecagent,
   tool_knowledge; check the registry names), defences none / prompt / detector / warrant_nomask / warrant.
4. Held-out attack set (bench/attacks/heldout/): write 15-20 attacks BEFORE running them and freeze them
   (commit + SHA-256 hash in the status file): the 9 HOLES.md attacks as Acme-world scenarios with
   deterministic attack/utility checks (demo/scenarios.py style), plus helpful third-party instructions,
   fake chat-turn/system templates, split-and-reassembled addresses, choice steering, confidential data via
   an allowed recipient. Runnable offline (scripted hijacked planner) and live.
5. Adaptive attacker (bench/adaptive.py): an attacker LLM rewrites the injection after each failure, up to
   10 rounds per pair on a sample of ~30 pairs, in two modes: (a) sees only success/fail, (b) sees the
   refusal text the planner saw. Report ASR@k for none / warrant / warrant+perturb, quiet refusals on and off
   (Lane E's WARRANT_QUIET_REFUSALS). Respect Groq free-tier limits: resume from a checkpoint, never restart.
6. Live subset: python -m bench.run_agentdojo --planner groq --suites banking --user-tasks 0-4
   --injection-tasks 0-2 --defenses none,prompt,warrant (gpt-oss-20b planner). Save in the results schema.
7. UI (vanilla JS, no build step; test in the browser pane): add "BOOK" to effectPills(); show NEEDS_INPUT
   (NEW_PAYEE / NEW_RECIPIENT / TAINTED_DOCUMENT / PROBE_INCONCLUSIVE) as an amber "asks the user" card
   with the reason, not as a failure; render the answer_guard event; render Lane D's "perturb" event as
   original vs relabeled text with the chosen fake highlighted; add a Playground preset menu that plants
   each HOLES.md attack in one click; a stage mode (big fonts, auto-scroll to verdicts).
8. Demo: record one clean live trace per scenario plus H1, H2, H8 (mask off, then mask on) so Replay works
   offline. Write the README results section from results/*.json ONLY (official and executed ASR,
   attackable denominator, asked-user rate, per attack type, held-out, adaptive), with honest caveats.

Never fabricate or round numbers; label partial runs. Update CLAUDE.md "Current status" when done.
```
