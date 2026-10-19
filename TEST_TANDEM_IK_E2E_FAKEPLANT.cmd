@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_ik\RUN_FAKEPLANT_IK_E2E_LIVE.ps1" -Mode Apply
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [ATTENTE/ECHEC] Verifier la sortie et le rapport. Ne pas rejouer le lancement sans diagnostic.
pause
exit /b %RC%
