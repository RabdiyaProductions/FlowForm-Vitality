@echo off
setlocal

REM ------------------------------------------------------------
REM Determine repository root from this script location.
REM This keeps paths quote-safe even when directories contain spaces.
REM ------------------------------------------------------------
set "ROOT=%~dp0"
set "VENV_DIR=%ROOT%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo [FlowForm] Setup started in "%ROOT%"

REM Copy .env.example to .env on first setup (so PORT/DB_PATH are loaded by app)
if not exist "%ROOT%.env" (
  if exist "%ROOT%.env.example" copy /Y "%ROOT%.env.example" "%ROOT%.env" >nul 2>&1
)

REM ------------------------------------------------------------
REM Create virtual environment if missing.
REM Uses py launcher when available, otherwise falls back to python.
REM ------------------------------------------------------------
if not exist "%VENV_PY%" (
  echo [FlowForm] Creating virtual environment...
  where py >nul 2>&1
  if errorlevel 1 (
    python -m venv "%VENV_DIR%"
  ) else (
    py -3 -m venv "%VENV_DIR%"
  )
)

if not exist "%VENV_PY%" (
  echo [FlowForm] ERROR: virtual environment python not found.
  exit /b 1
)

REM ------------------------------------------------------------
REM Upgrade pip/setuptools/wheel and install all requirements.
REM Always use venv python explicitly (never global interpreter).
REM ------------------------------------------------------------
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 exit /b 1

"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 exit /b 1

REM Next step
echo [FlowForm] Next step: run "run.bat"

endlocal