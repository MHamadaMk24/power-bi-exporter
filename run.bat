@echo off
cd /d "%~dp0src"
call ..\.venv\Scripts\python.exe main.py
pause
