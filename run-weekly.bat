@echo off
cd /d "%~dp0src"
call ..\.venv\Scripts\python.exe main.py --config config/weekly.yaml %*
pause
