@echo off
REM Step 1 - self-contained Python 3.10. Safe to run standalone or via setup.bat.
cd /d "%~dp0.."
set "STATE=%cd%\.setup_state"
if not exist "%STATE%" mkdir "%STATE%"

set "PY_TAG=20241016"
set "PY_VER=3.10.15"
set "PY_URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PY_TAG%/cpython-%PY_VER%+%PY_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz"
set "PYEXE=%cd%\python310\python.exe"

echo === Step 1/4 : Python %PY_VER% (self-contained) ===
if not exist "%PYEXE%" (
    echo Downloading portable Python %PY_VER% ... about 30 MB
    curl -L -o python310.tar.gz "%PY_URL%"
    if errorlevel 1 (
        echo [ERROR] Python download failed. Fix your connection and run setup.bat again to resume.
        pause
        exit /b 1
    )
    echo Extracting...
    tar -xf python310.tar.gz
    if exist python ren python python310
    del python310.tar.gz >nul 2>&1
)
if not exist "%PYEXE%" (
    echo [ERROR] Python extraction failed. curl and tar ship with Windows 10/11.
    echo         Delete the python310 folder and run setup.bat again to resume.
    pause
    exit /b 1
)
"%PYEXE%" -c "import sys; assert sys.version_info[:2]==(3,10)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Bundled Python is not 3.10. Delete the python310 folder and run setup.bat again.
    pause
    exit /b 1
)
"%PYEXE%" --version
echo done> "%STATE%\python.ok"
echo Step 1 complete.
exit /b 0
