#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright"
export ITP_HOST="${ITP_HOST:-0.0.0.0}"
export ITP_PORT="${ITP_PORT:-8765}"
export ITP_OPEN_BROWSER=0
exec .venv/bin/python -u app.py
