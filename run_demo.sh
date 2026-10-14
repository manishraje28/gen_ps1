#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv; . .venv/bin/activate; pip install -r requirements.txt; }
. .venv/bin/activate
python -m web.server --port 8000
