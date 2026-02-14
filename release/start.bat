@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set PORT=8081
set CONFIG=

:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--config" (
    set "CONFIG=%~2"
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
echo Unknown option: %~1
goto usage

:usage
echo Usage: start.bat [--port PORT] [--config PATH]
echo.
echo Options:
echo   --port PORT      Port to listen on (default: 8081)
echo   --config PATH    Path to config.yaml (default: auto-created next to app/)
exit /b 1

:done_args

:: --- Check Python ---
set PYTHON=
for %%P in (python) do (
    where %%P >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%V in ('%%P -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>^&1') do (
            for /f "tokens=1,2 delims=." %%A in ("%%V") do (
                if %%A==3 if %%B geq 13 (
                    set "PYTHON=%%P"
                )
            )
        )
    )
)

if "%PYTHON%"=="" (
    echo ERROR: Python 3.13+ is required but not found.
    echo Please install Python 3.13 or later and ensure it is on your PATH.
    exit /b 1
)

for /f "delims=" %%V in ('%PYTHON% --version 2^>^&1') do echo Using %%V

:: --- Create venv if needed ---
set "VENV_DIR=%SCRIPT_DIR%\venv"
if not exist "%VENV_DIR%" (
    echo Creating virtual environment...
    %PYTHON% -m venv "%VENV_DIR%"
)

:: --- Install dependencies from vendored wheels ---
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "STAMP=%VENV_DIR%\.vendor-installed"

if not exist "%STAMP%" (
    echo Installing dependencies from vendored packages...
    "%VENV_PYTHON%" -m pip install --no-index --find-links "%SCRIPT_DIR%\vendor" -r "%SCRIPT_DIR%\requirements.txt" --quiet
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        exit /b 1
    )
    echo. > "%STAMP%"
    echo Dependencies installed.
) else (
    echo Dependencies already installed.
)

:: --- Set config path ---
if not "%CONFIG%"=="" (
    set "ROADMAP_CONFIG_PATH=%CONFIG%"
)

:: --- Start the server ---
echo.
echo Starting Roadmap on port %PORT%...
echo Open http://localhost:%PORT% in your browser.
echo.

"%VENV_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% --app-dir "%SCRIPT_DIR%"
