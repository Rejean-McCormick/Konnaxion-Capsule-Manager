@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_ik\PREVIEW_FAKEPLANT_IK_E2E.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [ECHEC] Preflight IK E2E bloque. Aucune emission creee.
echo.
pause
exit /b %RC%
