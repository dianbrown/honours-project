@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-hecto-live.ps1" %*
exit /b %errorlevel%
