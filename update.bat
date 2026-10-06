@echo off
title ECHOES Update
cd /d "%~dp0"
if not exist "%~dp0setup\update.ps1" (
    echo.
    echo   Update files not found. Extract the WHOLE archive first,
    echo   then run this file from the extracted folder.
    echo.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\update.ps1"
if errorlevel 9009 (
    echo.
    echo   PowerShell not found. Windows 10 or 11 is required.
    echo.
    pause
)
