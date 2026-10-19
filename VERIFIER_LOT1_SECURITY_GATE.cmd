@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\lot1_security_gate\run.ps1" -Mode verify
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ECHEC/ATTENTE] Voir les diagnostics du Lot 1. Aucun deploiement lance.
pause
exit /b %RC%
