@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-attendance-kiosk.ps1" %*
exit /b %errorlevel%
