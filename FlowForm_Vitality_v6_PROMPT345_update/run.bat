@echo off
setlocal EnableExtensions

set "PAUSE_ON_EXIT="
echo %cmdcmdline% | find /i "/c" >nul && set "PAUSE_ON_EXIT=1"

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [FlowForm] ERROR: venv missing. Run setup.bat first.
  exit /b 1
)

REM Resolve preferred/available port and record ACTIVE_PORTS.json.
"%VENV_PY%" "%ROOT%boot_port.py" --write-active --print-port > "%TEMP%\flowform_port.txt"
if errorlevel 1 exit /b 1
set /p PORT=<"%TEMP%\flowform_port.txt"
del "%TEMP%\flowform_port.txt" >nul 2>&1

echo [FlowForm] Starting server on port %PORT%...

REM Start server in a persistent window so crashes stay visible
start "FlowForm Server" cmd /k ""%VENV_PY%" "%ROOT%run_server.py" --port %PORT%"

REM Wait for /health to respond (timeout 40s)
"%VENV_PY%" "%ROOT%tools\wait_for_http.py" "http://127.0.0.1:%PORT%/health" --timeout 40
if errorlevel 1 (
  echo [FlowForm] ERROR: server did not become ready on port %PORT%.
  echo Check the "FlowForm Server" window for the actual Python error.
  if defined PAUSE_ON_EXIT pause
  exit /b 1
)

echo [FlowForm] Ready at http://127.0.0.1:%PORT%/ready
start "" "http://127.0.0.1:%PORT%/ready"

if defined PAUSE_ON_EXIT (
  echo.
  echo [FlowForm] Browser opened. You may close this window.
  pause
)

endlocal
