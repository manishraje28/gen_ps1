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
- Tests (offline): `python -m pytest -q` (33 tests, must stay green)
- Oracle benchmark: `python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace`
- Live benchmark (Groq free tier is small): `python -m bench.run_agentdojo --planner groq --suites banking --user-tasks 0-4 --injection-tasks 0-2 --defenses none,prompt,warrant`

## Current status
- Offline tests: 33 pass (Python 3.13, Windows). Groq API reachable from the dev machine.
- Groq account has no Llama chat models; defaults are now planner `openai/gpt-oss-20b` (gets hijacked in all
  3 attack scenarios), contract + auditor `openai/gpt-oss-120b`. Free tier ~8k TPM/model, so live runs are slow.
- Live (runner, single runs): exfil / iban / benign pass 4/4 with mask on+off. Delegation: attack always blocked,
  utility flaky; the `resource` LOOKUP fix is NOT yet re-verified live.
- No clean live traces recorded yet (`traces/` empty). Live AgentDojo subset NOT run yet.
- Oracle results (949 attacked runs): ASR undefended 61% -> WARRANT 3%; benign utility 100%; re-checked after
  the contract/auditor fixes (15/16 configs rerun, identical). Rules were tuned on AgentDojo prompts.

## Next steps (in order)
1. Re-verify delegation live (WARRANT lane, mask on and off).
2. Record one clean live run per scenario via the UI (mask off first, then mask on, so replay picks mask on).
3. Run the small live Groq AgentDojo subset and check the Benchmark tab.

## Rules
- Never weaken the gate to make a demo pass; fix the planner prompt, scenario text or contract rules instead.
- Gate must fail closed (unknown side-effecting tool or internal error = REFUSED).
- Keep the offline tests green; add a test for every bug fixed.
- Don't fabricate benchmark numbers; only report what `results/*.json` contains.
