@echo off
setlocal
cd /d "%~dp0"
echo ==============================================
echo  TANDEM - IDENTITES FAKEPLANT - CONTROLE LIVE
echo ==============================================
where py >nul 2>&1
if errorlevel 1 (
  echo [BLOQUE] Python Launcher py -3 introuvable.
  set "RC=2"
  goto finish
)
py -3 -m tools.tandem_v2.verify_fakeplant_pair
set "RC=%ERRORLEVEL%"
:finish
echo.
if "%RC%"=="0" (
  echo [OK] Alignement des identites verifie. Permissions, SSO, IK non valides.
) else (
  echo [BLOQUE] L'etape n'a PAS ete validee. Aucune ecriture dans les bases.
)
echo Appuyer sur une touche pour fermer.
pause >nul
exit /b %RC%
