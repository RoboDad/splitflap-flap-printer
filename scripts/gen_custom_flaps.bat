@echo off
rem Double-click launcher for gen_custom_flaps.ps1
rem Uses the repo's .venv so `python` resolves to the right interpreter.
setlocal
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& { & '%REPO_ROOT%\.venv\Scripts\Activate.ps1'; & '%SCRIPT_DIR%gen_custom_flaps.ps1' }"
echo.
pause
