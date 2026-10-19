@echo off
setlocal
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tandem_ik\TEST_FAKEPLANT_IK_INGRESS.ps1"
exit /b %errorlevel%
