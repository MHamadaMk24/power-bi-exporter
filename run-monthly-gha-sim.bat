@echo off
REM Simulate GitHub Actions monthly export locally (headless, full workflow)
set GITHUB_ACTIONS=true
set PLAYWRIGHT_STORAGE_STATE_PATH=%~dp0playwright-state\session.json
cd /d "%~dp0src"
call ..\.venv\Scripts\python.exe main.py --config config/monthly.yaml %*
