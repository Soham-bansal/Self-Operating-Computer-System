@echo off
REM Internal — launched hidden by run.bat. Output goes to logs\omni_server.log
if not exist "%~dp0logs" mkdir "%~dp0logs"
cd /d "%~dp0OmniParser"
call "%~dp0env_omniparser\Scripts\activate.bat"
uvicorn omni_service:app --host 127.0.0.1 --port 8010 > "%~dp0logs\omni_server.log" 2>&1
