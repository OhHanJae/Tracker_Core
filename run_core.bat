@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "BOOTSTRAP_PYTHON="
set "BOOTSTRAP_ARG="
set "VENV_PYTHON=.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  if defined PYTHON call :try_python "%PYTHON%"
  if not defined BOOTSTRAP_PYTHON call :try_python "py" "-3"
  if not defined BOOTSTRAP_PYTHON call :try_python "python"

  if not defined BOOTSTRAP_PYTHON (
    echo [ERROR] Python 3 was not found. Set PYTHON to a valid python.exe path.
    exit /b 1
  )

  echo [1/2] Creating virtual environment: .venv
  "!BOOTSTRAP_PYTHON!" !BOOTSTRAP_ARG! -m venv .venv
  if errorlevel 1 exit /b 1

  echo [2/2] Installing Core dependencies...
  "%VENV_PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)

"%VENV_PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
if errorlevel 1 (
  echo [ERROR] .venv must use Python 3.10 or later.
  exit /b 1
)

"%VENV_PYTHON%" main.py %*
exit /b %ERRORLEVEL%

:try_python
set "CANDIDATE=%~1"
set "CANDIDATE_ARG=%~2"
if "%CANDIDATE_ARG%"=="" (
  "%CANDIDATE%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
) else (
  "%CANDIDATE%" "%CANDIDATE_ARG%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
)
if not errorlevel 1 (
  set "BOOTSTRAP_PYTHON=%CANDIDATE%"
  set "BOOTSTRAP_ARG=%CANDIDATE_ARG%"
)
exit /b 0
