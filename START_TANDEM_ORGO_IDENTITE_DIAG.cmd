@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :bad_directory

echo ================================================================
echo  TANDEM - IDENTITE FAKEPLANT DANS ORGO - LECTURE SEULE
echo ================================================================

where.exe pwsh.exe >nul 2>nul
if errorlevel 1 goto :missing_powershell

if not exist "%~dp0tools\tandem_v2\DIAG_ORGO_FAKEPLANT_IDENTITY.ps1" goto :missing_script

pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_v2\DIAG_ORGO_FAKEPLANT_IDENTITY.ps1"
set "STATUS=%ERRORLEVEL%"
if not "%STATUS%"=="0" echo [ECHEC] Diagnostic Orgo en erreur - voir le message ci-dessus.
echo.
echo [FIN] Lecture seule. Aucun chargement ni deploiement.
pause
exit /b %STATUS%

:missing_powershell
echo [ERREUR] PowerShell 7 requis - pwsh.exe introuvable.
pause
exit /b 2

:missing_script
echo [ERREUR] Script DIAG_ORGO_FAKEPLANT_IDENTITY.ps1 manquant.
echo [ACTION] Installer d'abord le correctif Tandem Identity V2.1.
pause
exit /b 2

:bad_directory
echo [ERREUR] Dossier Capsule Manager inaccessible.
pause
exit /b 2
