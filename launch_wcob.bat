@echo off
rem Double-click this to run the WeChat bot on Windows. No commands, no Python,
rem no git needed — this window does everything.
rem
rem First launch: Windows SmartScreen may warn ("Windows protected your PC").
rem If so, click "More info" -> "Run anyway".
rem
rem Keep this window OPEN while you use the bot; closing it stops the bot.

rem Run from the folder this script lives in (where pyproject.toml is).
cd /d "%~dp0"

rem uv brings its own Python and installs everything else, so nothing needs to
rem be preinstalled. Install it once if it's missing.
where uv >nul 2>nul
if errorlevel 1 (
    echo First-time setup: installing the Python runtime ^(uv^)...
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
)
rem Make this session see uv without a restart (installer target dir).
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

rem Create/update the local venv with the app + providers + wizard, then launch
rem the one-window Textual flow. Fast after the first run.
uv run --extra app wcob app

echo.
echo The bot exited. Press any key to close this window.
pause >nul
