# Work Lanes: Parallel Development Plan

Three Claude agents work on WARRANT **at the same time**, one per lane. Each lane owns a separate
set of files, works on its own branch, and publishes a status file so the other lanes can follow its
progress. Read [`SOLUTION.md`](SOLUTION.md) first for the architecture.

| Lane | Layer | Branch | Owner |
|---|---|---|---|
| **A. Trusted front** | Contract compiler + policy: *what is the user allowed to cause?* | `lane-a/contract` | Team (Atharva) + Claude |
| **B. Runtime enforcement** | Gate, ledger, auditor, audit log: *is this exact action justified?* | `lane-b/gate` | Claude agent |
| **C. Proof and presentation** | Benchmark, demo, UI, README: *show it works, honestly* | `lane-c/proof` | Claude agent |

---

## 1. File ownership

A lane edits **only** the files it owns. Everything else is read-only for that lane.

| Path | A | B | C |
|---|:-:|:-:|:-:|
| `warrant/contract.py`, `warrant/policy.py` | **write** | read | read |
| `warrant/gate.py`, `warrant/ledger.py`, `warrant/auditor.py` | read | **write** | read |
| `warrant/llm.py`, `warrant/pipeline.py`, `warrant/config.py`, `warrant/events.py` | read | read | read |
| `tests/test_contract_*.py` | **write** | read | read |
| `tests/test_gate_*.py` | read | **write** | read |
| `tests/test_offline.py`, `tests/test_playground.py` | read | read | read |
| `bench/`, `results/`, `web/`, `demo/`, `traces/` | read | read | **write** |
| `README.md`, `CLAUDE.md` | read | read | **write** |
| `docs/status/LANE_<X>.md` | own file only | own file only | own file only |

Do not edit the existing shared test files. Add new test files with your lane's prefix. If you need
a change in a file you don't own, write it under **Requests to other lanes** in your status file.

## 2. Interface between Lane A and Lane B

Lane A owns the `IntentContract` model (`warrant/contract.py`), and Lane B consumes it in the gate.

- **Adding** a field is allowed. Give it a default so existing code keeps working, and announce
  it in `docs/status/LANE_A.md`.
- **Renaming or removing** a field is not allowed during the hackathon.
- Lane B reads any new field defensively (`getattr(contract, "field", default)`), so B never has
  to wait for A to merge.

Planned new fields (Lane A):

| Field | Type | Meaning for the gate (Lane B) |
|---|---|---|
| `confirm_new_payees` | `bool`, default `True` | If a payment IBAN is not `USER_LITERAL`/`TRUSTED`, return `NEEDS_INPUT` (`NEW_PAYEE`) |
| `confirm_new_recipients` | `bool`, default `True` when external | Same, for egress to an address outside contacts and the org |
| `limits.max_amount_per_payment` | `float \| None` | Hard cap per payment |
| `policy_source` | `str` | Which org policy file narrowed this contract (for the audit log) |

## 3. Coordination protocol

1. **Start:** `git fetch origin`, then branch from `main` (`git checkout -b lane-x/...`).
2. **Commit small:** one fix plus its test per commit. Message format:
   `lane-a: restrict EGRESS to explicit send intent`.
3. **Commit and push after every change, immediately.** Every working change (code, test, status
   update or finding) is committed and pushed to your lane branch as soon as it exists. Don't batch
   work locally: the other lanes and the team build on your pushed branch, so anything not pushed
   is invisible to them. Update your status file in the same push, or right after it.
4. **Status file:** after each push, update `docs/status/LANE_<X>.md` using the template below.
5. **Read the others:** before starting a new task, check the other lanes' status:
   `git fetch origin && git show origin/lane-b/gate:docs/status/LANE_B.md`
6. **Keep green:** `python -m pytest -q` must pass on your branch before every push.
7. **Benchmark check:** Lanes A and B rerun the oracle benchmark after behaviour changes, and
   record the before/after numbers in their status file:
   `python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace`
8. **Merge order:** A, then B (rebased on A), then C (reruns the benchmark on merged code and
   updates the README). Every merge goes through a pull request into `main`.

Status file template (`docs/status/LANE_<X>.md`):

```markdown
# Lane X status
Updated: YYYY-MM-DD HH:MM · Branch: lane-x/... · Last commit: <sha>

## Done
- <fix> (commit <sha>, test <file::name>)
## In progress
- ...
## Interface changes (fields added, codes added)
- ...
## Requests to other lanes
- To Lane B: ...
## Benchmark (oracle) before -> after
| suite | ASR | benign utility | utility under attack |
```

## 4. Project rules (all lanes)

- Never weaken the gate to make a test or demo pass.
- The gate fails closed: an unknown side-effecting tool or an internal error means `REFUSED`.
- Every bug fix gets a regression test.
- Never fabricate or round benchmark numbers; report only what `results/*.json` contains.
- Never commit `.env` or API keys.

---

## 5. Lane prompts

Copy the prompt for your lane into a fresh Claude Code session started in the repository root.

### Lane A: Trusted front (contract + policy)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection, built on AgentDojo.
Read README.md, CLAUDE.md, docs/SOLUTION.md and docs/LANES.md fully before writing code.

You are Lane A. Branch: lane-a/contract (from main). You own ONLY warrant/contract.py,
warrant/policy.py, new tests/test_contract_*.py files and docs/status/LANE_A.md.
Follow the coordination protocol in docs/LANES.md section 3: commit AND push after every
change (code, test or status update), so the other lanes always see your latest work.

Your layer decides what a user's request is allowed to cause. Tasks, in order:
1. Over-broad effects: EGRESS is granted for any request that mentions "email", "tell" or "send", and
   WRITE for "book" (which also matches "booking"). Grant an effect only for an action verb aimed at
   a target (e.g. "send X to Y", "pay the bill", "book a table"). A read-only question gets READ only.
   Add tests for false grants and for the AgentDojo-style requests that must still be granted.
2. Add the contract fields listed in docs/LANES.md section 2 (confirm_new_payees,
   confirm_new_recipients, limits.max_amount_per_payment, policy_source), with safe defaults.
   Announce them in your status file so Lane B can enforce them.
3. Optional org policy: a YAML file (internal domains, payment caps, blocked effects) that can only
   NARROW a compiled contract, never widen it. Test that it cannot add effects.

The contract is compiled from the user's words only, never from tool data; the contract LLM may only
narrow it. After each behaviour change, run python -m pytest -q and the oracle benchmark, and record
before/after numbers in docs/status/LANE_A.md. Benign utility must not drop.
```

### Lane B: Runtime enforcement (gate, ledger, auditor)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection, built on AgentDojo.
Read README.md, CLAUDE.md, docs/SOLUTION.md and docs/LANES.md fully before writing code.

You are Lane B. Branch: lane-b/gate (from main). You own ONLY warrant/gate.py, warrant/ledger.py,
warrant/auditor.py, new tests/test_gate_*.py files and docs/status/LANE_B.md.
Follow the coordination protocol in docs/LANES.md section 3: commit AND push after every
change (code, test or status update), so the other lanes always see your latest work. Check Lane A's status
(git show origin/lane-a/contract:docs/status/LANE_A.md) for new contract fields, and read them with
getattr(..., default) so you never depend on A being merged.

Your layer checks every proposed tool call before it touches the world. Tasks, in order:
1. Data-like injections: a new IBAN or email that appears in a document as plain data (not flagged
   DIRECTIVE) currently counts as LOOKUP evidence. If it differs from the user's TRUSTED records for
   the same payee or contact, return NEEDS_INPUT with code NEW_PAYEE / NEW_RECIPIENT (respect
   confirm_new_payees / confirm_new_recipients when present).
2. Amounts: bind payment amounts to an evidence span, a user literal or a DERIVED value, within a
   small tolerance; otherwise refuse with SLOT_MISMATCH. Enforce limits.max_amount_per_payment if present.
3. Final answer: before the answer reaches the user, redact URLs, emails and IBANs that have no warrant.
4. Auditor: add rules for data-like, non-English and encoded (base64, homoglyph) instructions. The
   auditor may only LOWER trust.

Never weaken the gate to make a test pass; fail closed; log every decision to the JSONL audit log.
After each change, run python -m pytest -q and the oracle benchmark, and record before/after numbers
in docs/status/LANE_B.md.
```

### Lane C: Proof and presentation (benchmark, demo, UI)

```
Project: WARRANT, a defence for AI agents against indirect prompt injection, built on AgentDojo,
for the Genesis 2.0 PS 01 hackathon.
Read README.md, CLAUDE.md, docs/SOLUTION.md and docs/LANES.md fully before writing code.

You are Lane C. Branch: lane-c/proof (from main). You own ONLY bench/, results/, web/, demo/,
traces/, README.md, CLAUDE.md and docs/status/LANE_C.md. warrant/ is read-only for you.
Follow the coordination protocol in docs/LANES.md section 3: commit AND push after every
change (code, test or status update), so the other lanes always see your latest work. Read the Lane A and Lane B status
files before each task.

Tasks, in order:
1. Held-out attacks: add bench/attacks/ with attack styles not used during development: data-like
   IBAN or reply-to changes, non-English, encoded (base64, homoglyph), and multi-step attacks that
   reuse already-allowed effects. Make them runnable against any branch, so Lanes A and B can measure
   their fixes. Report ASR per defence.
2. Live numbers: with GROQ_API_KEY in .env, run
   python -m bench.run_agentdojo --planner groq --suites banking --user-tasks 0-4 --injection-tasks 0-2 --defenses none,prompt,warrant
   Handle free-tier rate limits (resume, don't restart). Save results in the existing results/*.json schema.
3. Demo: record one clean live trace per scenario (mask off first, then mask on). Make sure Replay
   works fully offline. Add a "stage mode" (bigger fonts, auto-scroll to verdicts) and a session
   counter of attacks blocked vs. false alarms. UI stays vanilla JS with no build step; test in a browser.
4. After A and B merge: rerun the oracle benchmark on main, and update the README tables with only
   numbers that exist in results/*.json. Add the A and B changelogs from their status files.

Never fabricate or round numbers; report partial runs honestly.
```
