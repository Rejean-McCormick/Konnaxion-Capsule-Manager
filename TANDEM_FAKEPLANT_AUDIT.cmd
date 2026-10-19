@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_pipeline\ENTREE_TANDEM.ps1" -Mode audit
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
