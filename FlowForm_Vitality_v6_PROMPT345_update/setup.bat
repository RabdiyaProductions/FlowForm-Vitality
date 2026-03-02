@echo off
setlocal EnableExtensions

set "PAUSE_ON_EXIT="
echo %cmdcmdline% | find /i "/c" >nul && set "PAUSE_ON_EXIT=1"

call "%~dp0\_setup_core.bat"
set "RC=%errorlevel%"

if defined PAUSE_ON_EXIT (
  echo.
  if not "%RC%"=="0" echo [FlowForm] Script exited with code %RC%.
  pause
)

endlocal & exit /b %RC%
