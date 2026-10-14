@echo off
REM One-time setup on Windows: creates a virtual environment and installs dependencies.
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Setup done. Put your Groq key in .env, then run run_demo.bat
