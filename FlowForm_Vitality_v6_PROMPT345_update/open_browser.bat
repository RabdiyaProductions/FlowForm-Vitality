@echo off
setlocal EnableExtensions

set "PAUSE_ON_EXIT="
echo %cmdcmdline% | find /i "/c" >nul && set "PAUSE_ON_EXIT=1"

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

set "PORT=5410"
if exist "%ROOT%ACTIVE_PORTS.json" (
  if exist "%VENV_PY%" (
    for /f "usebackq delims=" %%p in (`"%VENV_PY%" "%ROOT%tools\read_active_port.py" --file "%ROOT%ACTIVE_PORTS.json" --default 5410`) do set "PORT=%%p"
  ) else (
    for /f "usebackq delims=" %%p in (`python "%ROOT%tools\read_active_port.py" --file "%ROOT%ACTIVE_PORTS.json" --default 5410`) do set "PORT=%%p"
  )
)

start "" "http://127.0.0.1:%PORT%/ready"
if defined PAUSE_ON_EXIT (
  echo.
  echo [FlowForm] Opened http://127.0.0.1:%PORT%/ready
  pause
)

endlocal
