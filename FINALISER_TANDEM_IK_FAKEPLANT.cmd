@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_ik\FINALISER_FAKEPLANT_IK_E2E.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [BLOQUE] Ne pas rejouer TEST_TANDEM_IK_E2E_FAKEPLANT.cmd. Verifier le diagnostic.
if "%RC%"=="0" echo [OK] Preuves IK corroborees, rapport V2 finalise.
pause
exit /b %RC%
