@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Self-Operating-System  -  ONE-TIME SETUP
echo   Installs everything into this folder (5-15 minutes).
echo   Safe to re-run: it RESUMES from the step that failed.
echo ============================================================
echo.

set "STATE=%~dp0.setup_state"
if not exist "%STATE%" mkdir "%STATE%"

REM ---- Step 1: Python ----
if exist "%STATE%\python.ok" (
    echo [1/4] Python 3.10 - already done, skipping.
) else (
    call "%~dp0setup_steps\1_python.bat"
    if errorlevel 1 goto :stopped
)

REM ---- Step 2: main environment ----
if exist "%STATE%\env_operating.ok" (
    echo [2/4] Main environment - already done, skipping.
) else (
    call "%~dp0setup_steps\2_env_operating.bat"
    if errorlevel 1 goto :stopped
)

REM ---- Step 3: OmniParser environment ----
if exist "%STATE%\env_omniparser.ok" (
    echo [3/4] OmniParser environment - already done, skipping.
) else (
    call "%~dp0setup_steps\3_env_omniparser.bat"
    if errorlevel 1 goto :stopped
)

REM ---- Step 4: model weights ----
if exist "%STATE%\weights.ok" (
    echo [4/4] Model weights - already done, skipping.
) else (
    call "%~dp0setup_steps\4_weights.bat"
    if errorlevel 1 goto :stopped
)

REM ---- Finalize ----
if not exist .env (
    copy /Y .env.example .env >nul
    echo Created .env from template - add your API keys in the app's Settings.
)

echo.
echo ============================================================
echo   SETUP COMPLETE
echo   Next: double-click  run.bat  to start the app.
echo   Then click the Settings button to add your API keys.
echo ============================================================
pause
exit /b 0

:stopped
echo.
echo ============================================================
echo   SETUP STOPPED at a step above.
echo   Fix the reported problem, then run setup.bat again -
echo   it will SKIP the finished steps and resume from this one.
echo   (Or run just the failing script in setup_steps\ directly.)
echo ============================================================
exit /b 1
