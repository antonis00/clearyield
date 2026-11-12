@echo off
REM Start ClearYield on http://localhost:8001  (run.bat --demo for offline synthetic data; --restart replaces a copy
REM already running, such as the one Windows starts in the background)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv || goto :failed
)
REM Install again whenever requirements.txt or requirements.lock has changed since the last install. The lock holds
REM every package, including the ones the app's packages bring with them, at the versions the tests passed with
set REINSTALL=
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 || set REINSTALL=1
fc /b requirements.lock .venv\requirements.lock.installed >nul 2>&1 || set REINSTALL=1
if defined REINSTALL (
  echo Installing packages...
  .venv\Scripts\python -m pip install -q -r requirements.txt -c requirements.lock || goto :failed
  copy /y requirements.txt .venv\requirements.installed >nul
  copy /y requirements.lock .venv\requirements.lock.installed >nul
)
REM Already running, for example in the background since Windows started (Settings, Start with Windows)?
curl -s -o nul -m 3 http://127.0.0.1:8001/api/meta >nul 2>&1
if errorlevel 1 goto :start
if "%1"=="--restart" goto :replace
echo ClearYield is already running in the background. Opening http://localhost:8001
echo After an update, run.bat --restart replaces it with this copy.
start "" http://localhost:8001
goto :eof

:replace
echo Stopping the copy running in the background...
curl -s -o nul -m 5 -X POST http://127.0.0.1:8001/api/stop >nul 2>&1
REM Wait until it has let go of the port (ping as a pause: timeout refuses to run without a keyboard)
set /a tries=0
:waitstop
ping -n 2 127.0.0.1 >nul
curl -s -o nul -m 2 http://127.0.0.1:8001/api/meta >nul 2>&1
if errorlevel 1 goto :start
set /a tries+=1
if %tries% lss 15 goto :waitstop
echo It didn't stop. End pythonw.exe in Task Manager, then run run.bat again.
exit /b 1

:start
if "%1"=="--demo" set CLEARYIELD_DEMO=1
echo ClearYield running at http://localhost:8001
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
goto :eof

:failed
echo Setting up ClearYield failed. Check that Python is installed, then run run.bat again.
exit /b 1
