@echo off
REM Step 3 - OmniParser perception environment. Safe to run standalone.
cd /d "%~dp0.."
set "STATE=%cd%\.setup_state"
if not exist "%STATE%" mkdir "%STATE%"
set "PYEXE=%cd%\python310\python.exe"

echo === Step 3/4 : OmniParser environment (env_omniparser) ===
if not exist "%PYEXE%" (
    echo [ERROR] Python missing. Run setup.bat - it does step 1 first.
    pause
    exit /b 1
)
if not exist env_omniparser "%PYEXE%" -m venv env_omniparser
if not exist env_omniparser\Scripts\python.exe (
    echo [ERROR] Could not create env_omniparser. Delete that folder and run setup.bat again.
    pause
    exit /b 1
)
call env_omniparser\Scripts\activate.bat
python -m pip install --upgrade pip
echo Installing OmniParser requirements ... pinned, this is the big one
pip install -r omniparser_setup\omniparser-requirements.txt
if errorlevel 1 (
    echo [ERROR] OmniParser requirements failed - see the failing package above.
    echo         Fix it and run setup.bat again to resume.
    call env_omniparser\Scripts\deactivate.bat 2>nul
    pause
    exit /b 1
)
call env_omniparser\Scripts\deactivate.bat 2>nul
echo done> "%STATE%\env_omniparser.ok"
echo Step 3 complete.
exit /b 0
