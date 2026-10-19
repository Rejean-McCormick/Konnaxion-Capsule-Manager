@echo off
setlocal
cd /d "%~dp0"
set "KX_BACKEND=%~dp0..\Konnaxion\backend"
if not exist "%KX_BACKEND%\manage.py" (
  echo [ECHEC] Konnaxion backend introuvable : %KX_BACKEND%
  exit /b 1
)
echo === TANDEM / IK : PARAMETRES NON SECRETS KONNAXION ===
echo Aucune ecriture DB, aucun jeton genere, aucune migration.
cd /d "%KX_BACKEND%"
uv run --no-sync python "%~dp0tools\tandem_v2\configure_ik_base.py" --backend "%KX_BACKEND%" --apply
if errorlevel 1 (
  echo [ECHEC] Configuration refusee sans ecrasement des valeurs existantes.
  exit /b 1
)
echo [FIN] Redemarrer Uvicorn si des valeurs ont change.
