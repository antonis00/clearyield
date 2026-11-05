#!/usr/bin/env bash
# Start StockPilot on http://localhost:8001  (pass --demo for offline synthetic data)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
[ "$1" = "--demo" ] && export STOCKPILOT_DEMO=1
echo "StockPilot running at http://localhost:8001"
exec ./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001
