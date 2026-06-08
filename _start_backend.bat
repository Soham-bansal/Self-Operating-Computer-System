@echo off
REM Internal — launched hidden by run.bat. Output goes to logs\backend.log
if not exist "%~dp0logs" mkdir "%~dp0logs"
cd /d "%~dp0"
call "%~dp0env_operating\Scripts\activate.bat"
set PYTHONPATH=.
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 > "%~dp0logs\backend.log" 2>&1
