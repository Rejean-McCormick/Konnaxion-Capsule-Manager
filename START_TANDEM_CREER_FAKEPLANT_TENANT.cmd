@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo  ORGO - FAKEPLANT : APERCU PUIS CONFIRMATION EXPLICITE
echo ================================================================
where pwsh >nul 2>nul
if errorlevel 1 goto missing
pwsh -NoProfile -File "%~dp0tools\tandem_v2\CREATE_ORGO_FAKEPLANT_TENANT.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [ECHEC] Verifier le diagnostic ci-dessus.
echo.
echo [FIN] Code de sortie : %RC%
pause
exit /b %RC%
:missing
echo [ECHEC] PowerShell 7 introuvable. Installer pwsh.
pause
exit /b 2
