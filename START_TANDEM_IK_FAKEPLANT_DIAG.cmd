@echo off
setlocal
cd /d "%~dp0"
echo ===============================================
echo  TANDEM FAKEPLANT - IK READINESS - LECTURE SEULE
echo ===============================================
where py >nul 2>&1
if errorlevel 1 (
  echo [BLOQUE] py -3 introuvable.
  set "RC=2"
  goto finish
)
py -3 "tools\tandem_v2\diag_ik_fakeplant.py"
set "RC=%ERRORLEVEL%"
:finish
echo.
if "%RC%"=="0" echo [OK] Preflight IK termine. Aucun flux IK execute.
if not "%RC%"=="0" echo [ATTENTE] IK non qualifie; aucun flux IK execute.
echo Appuyer sur une touche pour fermer.
pause >nul
exit /b %RC%
