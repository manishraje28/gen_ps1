@echo off
call .venv\Scripts\activate.bat
REM 1) LLM-free worst case (fully hijacked planner) on all 4 AgentDojo suites. Free, about 5 minutes.
python -m bench.run_agentdojo --planner oracle --suites banking,slack,travel,workspace
REM 2) Live Groq run on a small subset (free tier friendly). Increase the ranges if you have quota.
python -m bench.run_agentdojo --planner groq --suites banking --user-tasks 0-4 --injection-tasks 0-2 --defenses none,prompt,warrant
