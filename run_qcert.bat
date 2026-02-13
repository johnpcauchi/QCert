@echo off
REM QCert Launcher for Windows
REM Double-click this file to start QCert

cd /d "%~dp0"

REM Try python from PATH
python run_qcert.py 2>nul
if %errorlevel% neq 0 (
    REM Try py launcher
    py -3 run_qcert.py 2>nul
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Python 3 not found. Please install Python 3.9+ from python.org
        echo and ensure it is added to your PATH.
        echo.
        pause
    )
)
