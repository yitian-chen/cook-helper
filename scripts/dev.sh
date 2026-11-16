#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then npm --prefix frontend ci; fi
npm --prefix frontend run build
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
