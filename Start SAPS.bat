@echo off
setlocal
title SAPS App Launcher
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-app.ps1"
if errorlevel 1 pause
endlocal
