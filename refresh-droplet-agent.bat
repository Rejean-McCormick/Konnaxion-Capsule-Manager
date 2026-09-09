@echo off

REM Relaunch inside persistent cmd window.
if /i "%~1" neq "--inner" (
  start "Konnaxion Droplet Refresh - stays open" cmd /k ""%~f0" --inner"
  exit /b
)
shift /1

setlocal EnableExtensions DisableDelayedExpansion

set "LOCAL_REPO=C:\mycode\Konnaxion\Konnaxion_Capsule_Manager"
set "SSH_KEY=C:\Users\rejea\.ssh\id_ed25519"
set "DROPLET_USER=root"
set "DROPLET_HOST=2.56.97.41"
set "DROPLET=%DROPLET_USER%@%DROPLET_HOST%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"

set "ARCHIVE=%TEMP%\konnaxion-manager-%STAMP%.tar.gz"
set "ARCHIVE_SCRIPT=%TEMP%\kx-create-manager-archive-%STAMP%.py"
set "REMOTE_SCRIPT_LOCAL=%TEMP%\kx-refresh-agent-remote-%STAMP%.sh"

echo ============================================================
echo Konnaxion Droplet Agent hard refresh
echo Console output only. No log file.
echo ============================================================
echo.

call :main
set "FINAL_RC=%ERRORLEVEL%"

echo.
echo ============================================================
echo Script finished with exit code: %FINAL_RC%
echo ============================================================
echo.
pause
exit /b %FINAL_RC%


:main
echo === Local repo ===
echo %LOCAL_REPO%

cd /d "%LOCAL_REPO%"
if errorlevel 1 (
  echo ERROR: Could not cd to "%LOCAL_REPO%"
  exit /b 1
)

echo.
echo === Local compileall ===
uv run python -m compileall .\kx_agent .\kx_manager .\kx_builder .\kx_shared .\kx_cli
if errorlevel 1 (
  echo ERROR: Local compileall failed.
  exit /b 1
)

echo.
echo === Creating archive with Python tarfile ===
echo Archive: "%ARCHIVE%"

call :write_archive_script
if errorlevel 1 (
  echo ERROR: Failed to write archive script.
  exit /b 1
)

uv run python "%ARCHIVE_SCRIPT%" "%LOCAL_REPO%" "%ARCHIVE%"
if errorlevel 1 (
  echo ERROR: archive creation or verification failed.
  del /q "%ARCHIVE_SCRIPT%" 2>nul
  exit /b 1
)

del /q "%ARCHIVE_SCRIPT%" 2>nul

echo.
echo === Writing remote refresh script ===
call :write_remote_script
if errorlevel 1 (
  echo ERROR: Failed to write remote refresh script.
  exit /b 1
)

echo.
echo === Copy archive to Droplet ===
scp -i "%SSH_KEY%" -P 22 "%ARCHIVE%" "%DROPLET%:/tmp/konnaxion-manager-current.tar.gz"
if errorlevel 1 (
  echo ERROR: Failed to copy archive to Droplet.
  exit /b 1
)

echo.
echo === Copy remote script to Droplet ===
scp -i "%SSH_KEY%" -P 22 "%REMOTE_SCRIPT_LOCAL%" "%DROPLET%:/tmp/kx-refresh-agent-remote.sh"
if errorlevel 1 (
  echo ERROR: Failed to copy remote script to Droplet.
  exit /b 1
)

echo.
echo === Execute remote refresh ===
ssh -i "%SSH_KEY%" -p 22 "%DROPLET%" "tr -d '\r' < /tmp/kx-refresh-agent-remote.sh > /tmp/kx-refresh-agent-remote-unix.sh && bash /tmp/kx-refresh-agent-remote-unix.sh"
if errorlevel 1 (
  echo ERROR: Remote refresh failed.
  exit /b 1
)

echo.
echo === Cleanup local temp files ===
del /q "%ARCHIVE%" 2>nul
del /q "%ARCHIVE_SCRIPT%" 2>nul
del /q "%REMOTE_SCRIPT_LOCAL%" 2>nul

echo.
echo SUCCESS: Droplet Agent refreshed, restarted, and health-checked.
exit /b 0


:write_archive_script
type nul > "%ARCHIVE_SCRIPT%"

>> "%ARCHIVE_SCRIPT%" echo from __future__ import annotations
>> "%ARCHIVE_SCRIPT%" echo import sys
>> "%ARCHIVE_SCRIPT%" echo import tarfile
>> "%ARCHIVE_SCRIPT%" echo from pathlib import Path
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo root = Path(sys.argv[1]).resolve()
>> "%ARCHIVE_SCRIPT%" echo archive = Path(sys.argv[2]).resolve()
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo excluded_top = {".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".kx-ui", "runtime", "dist", "build"}
>> "%ARCHIVE_SCRIPT%" echo excluded_any = {"__pycache__"}
>> "%ARCHIVE_SCRIPT%" echo required = {
>> "%ARCHIVE_SCRIPT%" echo     "kx_agent/runtime/compose.py",
>> "%ARCHIVE_SCRIPT%" echo     "kx_agent/actions.py",
>> "%ARCHIVE_SCRIPT%" echo     "kx_agent/capsules/importer.py",
>> "%ARCHIVE_SCRIPT%" echo     "kx_manager/services/deploy.py",
>> "%ARCHIVE_SCRIPT%" echo     "kx_manager/ui/agent_execution_client.py",
>> "%ARCHIVE_SCRIPT%" echo }
>> "%ARCHIVE_SCRIPT%" echo included = set()
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo def should_skip(path):
>> "%ARCHIVE_SCRIPT%" echo     rel = path.relative_to(root)
>> "%ARCHIVE_SCRIPT%" echo     parts = rel.parts
>> "%ARCHIVE_SCRIPT%" echo     if not parts:
>> "%ARCHIVE_SCRIPT%" echo         return False
>> "%ARCHIVE_SCRIPT%" echo     if parts[0] in excluded_top:
>> "%ARCHIVE_SCRIPT%" echo         return True
>> "%ARCHIVE_SCRIPT%" echo     if any(part in excluded_any for part in parts):
>> "%ARCHIVE_SCRIPT%" echo         return True
>> "%ARCHIVE_SCRIPT%" echo     return False
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo with tarfile.open(archive, "w:gz") as tar:
>> "%ARCHIVE_SCRIPT%" echo     for path in sorted(root.rglob("*")):
>> "%ARCHIVE_SCRIPT%" echo         if should_skip(path):
>> "%ARCHIVE_SCRIPT%" echo             continue
>> "%ARCHIVE_SCRIPT%" echo         rel = path.relative_to(root).as_posix()
>> "%ARCHIVE_SCRIPT%" echo         if path.is_file():
>> "%ARCHIVE_SCRIPT%" echo             tar.add(path, arcname=rel)
>> "%ARCHIVE_SCRIPT%" echo             included.add(rel)
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo missing = sorted(required - included)
>> "%ARCHIVE_SCRIPT%" echo if missing:
>> "%ARCHIVE_SCRIPT%" echo     print("ERROR: Archive is missing required files:")
>> "%ARCHIVE_SCRIPT%" echo     for item in missing:
>> "%ARCHIVE_SCRIPT%" echo         print(" -", item)
>> "%ARCHIVE_SCRIPT%" echo     raise SystemExit(1)
>> "%ARCHIVE_SCRIPT%" echo.
>> "%ARCHIVE_SCRIPT%" echo print("Archive source check passed.")
>> "%ARCHIVE_SCRIPT%" echo print("Included required files:")
>> "%ARCHIVE_SCRIPT%" echo for item in sorted(required):
>> "%ARCHIVE_SCRIPT%" echo     print(" -", item)

exit /b 0


:write_remote_script
type nul > "%REMOTE_SCRIPT_LOCAL%"

>> "%REMOTE_SCRIPT_LOCAL%" echo set -e
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Stop current Agent ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo systemctl stop konnaxion-agent ^|^| true
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Replace /opt/konnaxion/manager ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo rm -rf /opt/konnaxion/manager
>> "%REMOTE_SCRIPT_LOCAL%" echo mkdir -p /opt/konnaxion/manager
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo tar -xzf /tmp/konnaxion-manager-current.tar.gz -C /opt/konnaxion/manager
>> "%REMOTE_SCRIPT_LOCAL%" echo rm -f /tmp/konnaxion-manager-current.tar.gz
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo cd /opt/konnaxion/manager
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Verify extracted source exists ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo test -f /opt/konnaxion/manager/kx_agent/actions.py
>> "%REMOTE_SCRIPT_LOCAL%" echo test -f /opt/konnaxion/manager/kx_agent/runtime/compose.py
>> "%REMOTE_SCRIPT_LOCAL%" echo test -f /opt/konnaxion/manager/kx_agent/capsules/importer.py
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Remote dependency sync/install ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo uv sync ^|^| uv pip install -e .
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Remote compileall ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo python3 -m compileall kx_agent kx_manager kx_builder kx_shared kx_cli
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Remote file hashes ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo sha256sum /opt/konnaxion/manager/kx_agent/actions.py /opt/konnaxion/manager/kx_agent/runtime/compose.py /opt/konnaxion/manager/kx_agent/capsules/importer.py /opt/konnaxion/manager/kx_manager/services/deploy.py /opt/konnaxion/manager/kx_manager/ui/agent_execution_client.py
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Restart Agent ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo systemctl daemon-reload
>> "%REMOTE_SCRIPT_LOCAL%" echo systemctl restart konnaxion-agent
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo sleep 3
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Agent status ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo systemctl --no-pager --full status konnaxion-agent ^| sed -n "1,30p"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Runtime source markers ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo cd /opt/konnaxion/manager
>> "%REMOTE_SCRIPT_LOCAL%" echo uv run python - ^<^<'PY'
>> "%REMOTE_SCRIPT_LOCAL%" echo import inspect
>> "%REMOTE_SCRIPT_LOCAL%" echo import kx_agent.actions as actions
>> "%REMOTE_SCRIPT_LOCAL%" echo import kx_agent.runtime.compose as compose
>> "%REMOTE_SCRIPT_LOCAL%" echo import kx_agent.capsules.importer as importer
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo print("actions:", actions.__file__)
>> "%REMOTE_SCRIPT_LOCAL%" echo print("compose:", compose.__file__)
>> "%REMOTE_SCRIPT_LOCAL%" echo print("importer:", importer.__file__)
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo print("has security_context_inputs:", hasattr(compose, "security_context_inputs"))
>> "%REMOTE_SCRIPT_LOCAL%" echo print("has manifest_loaded marker:", "manifest_loaded" in inspect.getsource(actions.handle_security_check))
>> "%REMOTE_SCRIPT_LOCAL%" echo print("has env_validation marker:", "env_validation" in inspect.getsource(actions.handle_security_check))
>> "%REMOTE_SCRIPT_LOCAL%" echo print("has extracted marker:", "extracted" in inspect.getsource(importer.import_capsule))
>> "%REMOTE_SCRIPT_LOCAL%" echo PY
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Health ==="
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "Waiting for Agent health..."
>> "%REMOTE_SCRIPT_LOCAL%" echo for i in $(seq 1 30); do
>> "%REMOTE_SCRIPT_LOCAL%" echo   if curl --fail-with-body --max-time 5 -sS http://127.0.0.1:8765/v1/health; then
>> "%REMOTE_SCRIPT_LOCAL%" echo     echo
>> "%REMOTE_SCRIPT_LOCAL%" echo     echo "Agent health OK."
>> "%REMOTE_SCRIPT_LOCAL%" echo     exit 0
>> "%REMOTE_SCRIPT_LOCAL%" echo   fi
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "Agent not ready yet, attempt $i/30..."
>> "%REMOTE_SCRIPT_LOCAL%" echo   sleep 2
>> "%REMOTE_SCRIPT_LOCAL%" echo done
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "ERROR: Agent did not become healthy."
>> "%REMOTE_SCRIPT_LOCAL%" echo systemctl --no-pager --full status konnaxion-agent ^| sed -n "1,80p"
>> "%REMOTE_SCRIPT_LOCAL%" echo journalctl -u konnaxion-agent -n 120 --no-pager
>> "%REMOTE_SCRIPT_LOCAL%" echo exit 1>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo echo
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "=== Remote refresh complete ==="

exit /b 0