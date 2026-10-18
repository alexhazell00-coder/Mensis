@echo off
setlocal EnableExtensions

rem Run from the repository directory, even when launched by double-clicking.
cd /d "%~dp0"
if errorlevel 1 goto :setup_failed

rem Prefer the Windows Python launcher, then fall back to python on PATH.
set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo MENSIS requires Python 3.11 or newer.
    echo Install Python from python.org, then run setup.bat again.
    goto :setup_failed
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the local Python environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :setup_failed
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo The existing .venv does not use Python 3.11 or newer.
    echo Remove the .venv folder manually, then run setup.bat again.
    goto :setup_failed
)

echo Installing MENSIS dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :setup_failed

rem Keep application state in the conventional per-user Windows data directory.
if not defined MENSIS_DATA_DIR (
    if defined LOCALAPPDATA (
        set "MENSIS_DATA_DIR=%LOCALAPPDATA%\MENSIS"
    ) else (
        set "MENSIS_DATA_DIR=%USERPROFILE%\AppData\Local\MENSIS"
    )
)
if not defined MENSIS_WORKSPACE set "MENSIS_WORKSPACE=%MENSIS_DATA_DIR%\workspace"
if not defined MENSIS_HOST set "MENSIS_HOST=127.0.0.1"
if not defined MENSIS_PORT set "MENSIS_PORT=8000"

echo.
echo MENSIS is starting at http://%MENSIS_HOST%:%MENSIS_PORT%/
echo Data:      %MENSIS_DATA_DIR%
echo Workspace: %MENSIS_WORKSPACE%
echo Press Ctrl+C to stop MENSIS.
echo.
".venv\Scripts\python.exe" -m app.main
exit /b %ERRORLEVEL%

:setup_failed
echo.
echo MENSIS setup could not continue. Check the message above and your Python/network setup.
pause
exit /b 1
