@echo off
setlocal
cd /d "%~dp0"
echo =================================================================
echo  TANDEM V2 - PRE-FLIGHT (LECTURE SEULE / AUCUN DEPLOIEMENT)
echo =================================================================
where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher ^(py^) introuvable.
  pause
  exit /b 1
)
py -3 -m tools.tandem_v2.cli --capsule-manager "%CD%" preflight
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Seeds reconnus. Release NON publiee. Voir README_TANDEM_RELEASE_V2.md.
) else (
  echo [ECHEC] Verifier les chemins et les manifests.
)
pause
exit /b %RC%
