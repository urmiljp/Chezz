@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :fail
)

rem Reinstall dependencies only when pyproject.toml changed (keeps startup offline).
fc /b pyproject.toml .venv\pyproject.installed >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e .
    if errorlevel 1 goto :fail
    copy /y pyproject.toml .venv\pyproject.installed >nul
)

".venv\Scripts\python.exe" -m chezz %*
exit /b %errorlevel%

:fail
echo Setup failed. Install Python 3.11+ from python.org and try again.
pause
exit /b 1
