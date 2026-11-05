@echo off
REM Start StockPilot on http://localhost:8001  (run.bat --demo for offline synthetic data)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
if "%1"=="--demo" set STOCKPILOT_DEMO=1
echo StockPilot running at http://localhost:8001
.venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8001
