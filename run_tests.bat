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

"%VENV_PY%" "%ROOT%tools\run_full_tests.py"
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :done

echo [FlowForm] Tests PASS
:done
if defined PAUSE_ON_EXIT (
  echo.
  if not "%RC%"=="0" echo [FlowForm] Script exited with code %RC%.
  pause
)

endlocal & exit /b %RC%
