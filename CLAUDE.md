# CLAUDE.md: WARRANT (Genesis 2.0, PS 01, Team Thanekars)

## What this project is
Hackathon prototype for PS 01 "Separating Data from Authority: Securing AI Agents Against Indirect
Prompt Injection" (benchmark: AgentDojo v1.2.1, package `agentdojo==0.1.35`).
WARRANT = a drop-in AgentDojo pipeline: `ContractCompiler` after `InitQuery`, and `WarrantGate`
in place of `ToolsExecutor`. Every sink argument (recipient, IBAN, file, URL, password) must carry a
warrant (USER_LITERAL / TRUSTED / LOOKUP / DELEGATED / DERIVED) traced through the Value Ledger.
The Span Auditor (rules + optional small LLM) labels tool-output spans EVIDENCE / DIRECTIVE and may
only narrow trust. Allowed side effects are dry-run on a deep copy (shadow execution) and checked
against the contract's envelope. Every decision is appended to `audit_logs/<run_id>.jsonl`.

## Parallel work (read first)
Three agents work in parallel lanes. See `docs/SOLUTION.md` (architecture) and `docs/LANES.md`
(file ownership, branches, coordination protocol, lane prompts). Lane status: `docs/status/LANE_<X>.md`.

## Layout
- `warrant/`: core library: `contract.py`, `gate.py` (gate, shadow exec, verifier, undefended emitting executor), `auditor.py`, `ledger.py`, `policy.py` (effect/sink per tool, demo + all 4 AgentDojo suites), `llm.py` (Groq via OpenAI SDK, retries, tool_use_failed recovery), `pipeline.py`, `config.py` (.env loader), `events.py`
- `demo/`: Acme workspace env (`env.py`), 4 scenarios with deterministic attack/utility checks (`scenarios.py`), `runner.py` (run a lane; `ScriptedPlanner` offline stand-in)
- `bench/run_agentdojo.py`: benchmark: `--planner oracle` (LLM-free, fully hijacked worst case) or `--planner groq`; defences none/prompt/detector/warrant_nomask/warrant
- `web/playground.py`: Playground chat API (`/api/play/*`): sessions, full world, add/upload data, multi-turn turns with CarryLedger
- `web/server.py`: FastAPI + SSE (`/api/run`, `/api/replay`, `/api/traces`, `/api/bench`, `/api/audit/verify`); `web/static/` vanilla JS UI (no build step)
- `results/`: oracle benchmark JSON (all 4 suites, already computed); `traces/`: recorded live runs for replay

## Commands
- Setup (Windows): `setup.bat` (creates .venv, installs, copies .env.example -> .env). Key goes in `.env` as `GROQ_API_KEY=...` (never commit .env).
- Demo: `run_demo.bat` or `python -m web.server --port 8000`
- Tests (offline): `python -m pytest -q` (must stay green)
- Held-out attacks: `python -m bench.heldout --planner scripted`; adaptive: `python -m bench.adaptive`; report: `python -m bench.report`
- Oracle benchmark: `python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace`
- Live benchmark (Groq free tier is small): `python -m bench.run_agentdojo --planner groq --suites banking --user-tasks 0-4 --injection-tasks 0-2 --defenses none,prompt,warrant`

## Current status
- Round 1 (Lanes A, B) merged; round 2 = Lanes D (perturbation), E (gate hardening), F (proof) per `docs/NEXT_LANES.md`.
- Results are generated into `docs/RESULTS.md` by `python -m bench.report` from `results/` (never hand-typed).
- Oracle, `important_instructions`: WARRANT executes 1/609 attackable attacks (official AgentDojo score 22/949);
  undefended 583/609. Benign utility 97/97. The 340 dead pairs (empty ground truth) and slack's attempted-call
  scoring (AgentDojo #168) are now reported separately (`asr_executed`, `asr_attackable`).
- NOT robust yet: AgentDojo `direct` / `ignore_previous` / `system_message` attacks get through (banking 38.89%,
  slack 60% executed, oracle) because the auditor does not flag them; held-out set (`bench/heldout.py`, frozen):
  WARRANT stops 7/15. Fix structurally (Lanes D, E), never by adding the test phrases to the regex.
- Planner `openai/gpt-oss-20b`, contract + auditor `openai/gpt-oss-120b` on Groq free tier (~8k TPM/model): slow.
- `tests/test_playground.py::test_chat_needs_key` fails whenever a real key is in `.env` (test assumes none).

## Next steps (in order)
1. Lane E: Two-Witness for new payees/recipients (closes most of the `direct`-style bypass and held-out H2).
2. Lane D: counterfactual value relabeling (held-out H1, H3, H7).
3. Lane F: rerun `bench.run_agentdojo` (all attacks), `bench.heldout`, `bench.adaptive` on merged main; `bench.report`.

## Rules
- Never weaken the gate to make a demo pass; fix the planner prompt, scenario text or contract rules instead.
- Gate must fail closed (unknown side-effecting tool or internal error = REFUSED).
- Keep the offline tests green; add a test for every bug fixed.
- Don't fabricate benchmark numbers; only report what `results/*.json` contains.
