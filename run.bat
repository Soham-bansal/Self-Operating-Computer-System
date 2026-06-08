@echo off
setlocal
cd /d "%~dp0"

if not exist env_operating (
    echo [ERROR] Not set up yet. Run setup.bat first.
    pause
    exit /b 1
)

echo ============================================================
echo   Starting Self-Operating-System
echo   (the two servers run hidden; only the UI will appear)
echo ============================================================

REM ---------- 1. OmniParser perception server — HIDDEN (logs\omni_server.log) ----------
echo Starting OmniParser perception server (hidden)...
wscript "%~dp0_hidden.vbs" "%~dp0_start_omni.bat"

echo Waiting for OmniParser to finish loading its models...
set /a OMNI_TRIES=0
:wait_omni
set /a OMNI_TRIES+=1
if %OMNI_TRIES% GTR 100 (
    echo [WARN] OmniParser not ready after ~5 min. See logs\omni_server.log
    goto omni_done
)
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8010/health' -TimeoutSec 3 -UseBasicParsing; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 3 /nobreak >nul
    goto wait_omni
)
echo   OmniParser is READY.
:omni_done

REM ---------- 2. Backend — HIDDEN (logs\backend.log) ----------
echo Starting backend (hidden)...
wscript "%~dp0_hidden.vbs" "%~dp0_start_backend.bat"

echo Waiting for backend to be ready...
set /a BE_TRIES=0
:wait_be
set /a BE_TRIES+=1
if %BE_TRIES% GTR 40 (
    echo [WARN] Backend not ready after ~2 min. See logs\backend.log
    goto be_done
)
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3 -UseBasicParsing; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 3 /nobreak >nul
    goto wait_be
)
echo   Backend is READY.
:be_done

REM ---------- 3. Desktop UI (the only visible window) ----------
echo Starting the UI...
call env_operating\Scripts\activate.bat
python ui\phase_b_app.py

REM ---------- 4. UI closed -> stop the hidden servers ----------
echo.
echo UI closed. Stopping background servers...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*omni_service:app*' -or $_.CommandLine -like '*backend.app.main:app*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo Done.
