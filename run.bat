@echo off
REM Start ClearYield on http://localhost:8001  (run.bat --demo for offline synthetic data)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv || goto :failed
)
REM Install again whenever requirements.txt has changed since the last install
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo Installing packages...
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto :failed
  copy /y requirements.txt .venv\requirements.installed >nul
)
if "%1"=="--demo" set CLEARYIELD_DEMO=1
echo ClearYield running at http://localhost:8001
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
goto :eof

:failed
echo Setting up ClearYield failed. Check that Python is installed, then run run.bat again.
exit /b 1
