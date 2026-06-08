@echo off
REM Step 2 - main app environment (UI + backend). Safe to run standalone.
cd /d "%~dp0.."
set "STATE=%cd%\.setup_state"
if not exist "%STATE%" mkdir "%STATE%"
set "PYEXE=%cd%\python310\python.exe"

echo === Step 2/4 : main environment (env_operating) ===
if not exist "%PYEXE%" (
    echo [ERROR] Python missing. Run setup.bat - it does step 1 first.
    pause
    exit /b 1
)
if not exist env_operating "%PYEXE%" -m venv env_operating
if not exist env_operating\Scripts\python.exe (
    echo [ERROR] Could not create env_operating. Delete that folder and run setup.bat again.
    pause
    exit /b 1
)
call env_operating\Scripts\activate.bat
python -m pip install --upgrade pip
echo Installing main requirements...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Main requirements failed - see the failing package above.
    echo         Fix it and run setup.bat again to resume.
    call env_operating\Scripts\deactivate.bat 2>nul
    pause
    exit /b 1
)
python -m playwright install-deps >nul 2>&1
call env_operating\Scripts\deactivate.bat 2>nul
echo done> "%STATE%\env_operating.ok"
echo Step 2 complete.
exit /b 0
