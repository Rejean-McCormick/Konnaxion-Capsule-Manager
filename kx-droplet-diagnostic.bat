@echo off

REM Relaunch inside persistent cmd window.
if /i "%~1" neq "--inner" (
  start "Konnaxion Droplet Diagnostic - stays open" cmd /k ""%~f0" --inner"
  exit /b
)
shift /1

setlocal EnableExtensions DisableDelayedExpansion

set "LOCAL_REPO=C:\mycode\Konnaxion\Konnaxion_Capsule_Manager"
set "SSH_KEY=C:\Users\rejea\.ssh\id_ed25519"
set "DROPLET_USER=root"
set "DROPLET_HOST=2.56.97.41"
set "DROPLET=%DROPLET_USER%@%DROPLET_HOST%"

set "INSTANCE_ID=demo-001"
set "CAPSULE_ID=konnaxion-v14-demo-2026.04.30"
set "REMOTE_KX_ROOT=/opt/konnaxion"
set "REMOTE_MANAGER=/opt/konnaxion/manager"
set "REMOTE_COMPOSE=/opt/konnaxion/instances/demo-001/state/docker-compose.runtime.yml"
set "REMOTE_CAPSULE_DIR=/opt/konnaxion/shared/capsules/konnaxion-v14-demo-2026.04.30"

REM Common local Manager UI/API URLs to probe.
set "LOCAL_MANAGER_URL_1=http://127.0.0.1:8501"
set "LOCAL_MANAGER_URL_2=http://127.0.0.1:8501/_stcore/health"
set "LOCAL_MANAGER_URL_3=http://127.0.0.1:8000"
set "LOCAL_MANAGER_URL_4=http://127.0.0.1:8080"
set "LOCAL_MANAGER_URL_5=http://127.0.0.1:8766"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"

set "REMOTE_SCRIPT_LOCAL=%TEMP%\kx-droplet-diagnostic-%STAMP%.sh"

echo ============================================================
echo Konnaxion Droplet diagnostic
echo Read-only checks. Console output only. No log file.
echo ============================================================
echo.
echo Local repo: %LOCAL_REPO%
echo Droplet:    %DROPLET%
echo Instance:   %INSTANCE_ID%
echo Capsule:    %CAPSULE_ID%
echo.

call :main
set "FINAL_RC=%ERRORLEVEL%"

echo.
echo ============================================================
echo Diagnostic finished with exit code: %FINAL_RC%
echo ============================================================
echo.
pause
exit /b %FINAL_RC%


:main
echo === Local machine checks ===

cd /d "%LOCAL_REPO%" 2>nul
if errorlevel 1 (
  echo WARN: Could not cd to "%LOCAL_REPO%"
) else (
  echo Local repo exists.
)

echo.
echo --- Local tools ---
where ssh
where scp
where curl
where tar
where uv

echo.
echo --- Local SSH key ---
if exist "%SSH_KEY%" (
  echo SSH key exists: "%SSH_KEY%"
) else (
  echo ERROR: SSH key missing: "%SSH_KEY%"
  exit /b 1
)

echo.
echo === Local Manager check ===
call :check_local_manager

echo.
echo === Write remote diagnostic script ===
call :write_remote_script
if errorlevel 1 (
  echo ERROR: Failed to write remote diagnostic script.
  exit /b 1
)

echo.
echo === SSH reachability check ===
ssh -i "%SSH_KEY%" ^
  -p 22 ^
  -o BatchMode=yes ^
  -o ConnectTimeout=15 ^
  -o StrictHostKeyChecking=accept-new ^
  "%DROPLET%" ^
  "echo SSH_OK && hostname && date -u"

if errorlevel 1 (
  echo ERROR: SSH reachability failed.
  exit /b 1
)

echo.
echo === Copy diagnostic script to Droplet ===
scp -i "%SSH_KEY%" -P 22 "%REMOTE_SCRIPT_LOCAL%" "%DROPLET%:/tmp/kx-droplet-diagnostic.sh"
if errorlevel 1 (
  echo ERROR: Failed to copy diagnostic script to Droplet.
  exit /b 1
)

echo.
echo === Execute remote diagnostic ===
ssh -i "%SSH_KEY%" -p 22 "%DROPLET%" "tr -d '\r' < /tmp/kx-droplet-diagnostic.sh > /tmp/kx-droplet-diagnostic-unix.sh && bash /tmp/kx-droplet-diagnostic-unix.sh"
set "REMOTE_RC=%ERRORLEVEL%"

echo.
echo === Cleanup local temp file ===
del /q "%REMOTE_SCRIPT_LOCAL%" 2>nul

exit /b %REMOTE_RC%


:check_local_manager
echo --- Local listening ports likely used by Manager ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = 8501,8000,8080,8766; foreach ($p in $ports) { $c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('LISTEN 127.0.0.1:' + $p) } else { Write-Host ('CLOSED 127.0.0.1:' + $p) } }"

echo.
echo --- Local Python/uv/streamlit/uvicorn processes ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python|uv|streamlit|uvicorn' -or $_.CommandLine -match 'kx_manager|Konnaxion|streamlit|uvicorn' }; if ($procs) { $procs | Select-Object ProcessId,Name,CommandLine | Format-List } else { Write-Host 'No likely local Manager process found.' }"

echo.
echo --- Local Manager HTTP probes ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$urls = @('%LOCAL_MANAGER_URL_1%','%LOCAL_MANAGER_URL_2%','%LOCAL_MANAGER_URL_3%','%LOCAL_MANAGER_URL_4%','%LOCAL_MANAGER_URL_5%'); $ok = $false; foreach ($u in $urls) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $u; Write-Host ('OK ' + $u + ' status=' + [int]$r.StatusCode); $ok = $true } catch { Write-Host ('FAIL ' + $u + ' ' + $_.Exception.Message) } }; if (-not $ok) { Write-Host 'ERROR: Local Konnaxion Manager does not appear to be running.'; exit 2 }"

if errorlevel 2 (
  echo.
  echo WARN: Local Manager UI/API is not reachable.
  echo Start it locally before using GUI actions.
  echo.
  echo Example candidates:
  echo   uv run python -m kx_manager.ui.server
  echo   uv run python -m kx_manager.main
  echo   uv run streamlit run kx_manager/ui/streamlit_app.py
  echo.
)

exit /b 0


:write_remote_script
type nul > "%REMOTE_SCRIPT_LOCAL%"

>> "%REMOTE_SCRIPT_LOCAL%" echo #!/usr/bin/env bash
>> "%REMOTE_SCRIPT_LOCAL%" echo set +e
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo INSTANCE_ID="%INSTANCE_ID%"
>> "%REMOTE_SCRIPT_LOCAL%" echo CAPSULE_ID="%CAPSULE_ID%"
>> "%REMOTE_SCRIPT_LOCAL%" echo KX_ROOT="%REMOTE_KX_ROOT%"
>> "%REMOTE_SCRIPT_LOCAL%" echo MANAGER="%REMOTE_MANAGER%"
>> "%REMOTE_SCRIPT_LOCAL%" echo COMPOSE_FILE="%REMOTE_COMPOSE%"
>> "%REMOTE_SCRIPT_LOCAL%" echo CAPSULE_DIR="%REMOTE_CAPSULE_DIR%"
>> "%REMOTE_SCRIPT_LOCAL%" echo PROJECT_NAME="konnaxion-demo-001"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section^(^) ^{
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "============================================================"
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "$1"
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "============================================================"
>> "%REMOTE_SCRIPT_LOCAL%" echo ^}
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo run^(^) ^{
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "--- $1 ---"
>> "%REMOTE_SCRIPT_LOCAL%" echo   shift
>> "%REMOTE_SCRIPT_LOCAL%" echo   "$@"
>> "%REMOTE_SCRIPT_LOCAL%" echo   rc=$?
>> "%REMOTE_SCRIPT_LOCAL%" echo   echo "[exit $rc]"
>> "%REMOTE_SCRIPT_LOCAL%" echo   return 0
>> "%REMOTE_SCRIPT_LOCAL%" echo ^}
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Host basics"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "hostname" hostnamectl
>> "%REMOTE_SCRIPT_LOCAL%" echo run "date UTC" date -u
>> "%REMOTE_SCRIPT_LOCAL%" echo run "uptime" uptime
>> "%REMOTE_SCRIPT_LOCAL%" echo run "kernel" uname -a
>> "%REMOTE_SCRIPT_LOCAL%" echo run "disk" df -h / /opt /tmp
>> "%REMOTE_SCRIPT_LOCAL%" echo run "memory" free -h
>> "%REMOTE_SCRIPT_LOCAL%" echo run "top processes" sh -c "ps aux --sort=-%%mem | sed -n '1,12p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Network listeners and firewall"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "listeners 80/443/8765" sh -c "ss -ltnp | grep -E ':(80|443|8765)\b' || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "ufw status" sh -c "ufw status verbose 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "iptables relevant ports" sh -c "iptables -S 2>/dev/null | grep -E '80|443|8765' || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Konnaxion Agent"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "systemd status" sh -c "systemctl --no-pager --full status konnaxion-agent | sed -n '1,60p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "agent health local" curl --fail-with-body --max-time 10 -sS http://127.0.0.1:8765/v1/health
>> "%REMOTE_SCRIPT_LOCAL%" echo run "agent info" curl --fail-with-body --max-time 10 -sS http://127.0.0.1:8765/v1/agent/info
>> "%REMOTE_SCRIPT_LOCAL%" echo run "agent recent logs" sh -c "journalctl -u konnaxion-agent -n 120 --no-pager"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Manager source and runtime markers"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "manager tree required files" sh -c "ls -l $MANAGER/kx_agent/actions.py $MANAGER/kx_agent/runtime/compose.py $MANAGER/kx_agent/capsules/importer.py $MANAGER/kx_manager/services/deploy.py $MANAGER/kx_manager/ui/agent_execution_client.py"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "manager source hashes" sh -c "sha256sum $MANAGER/kx_agent/actions.py $MANAGER/kx_agent/runtime/compose.py $MANAGER/kx_agent/capsules/importer.py $MANAGER/kx_manager/services/deploy.py $MANAGER/kx_manager/ui/agent_execution_client.py"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "python import markers" sh -c "cd $MANAGER && uv run python -c 'import inspect; import kx_agent.actions as a; import kx_agent.runtime.compose as c; import kx_agent.capsules.importer as i; print(\"actions\", a.__file__); print(\"compose\", c.__file__); print(\"importer\", i.__file__); print(\"security_context_inputs\", hasattr(c, \"security_context_inputs\")); print(\"manifest_loaded\", \"manifest_loaded\" in inspect.getsource(a.handle_security_check)); print(\"env_validation\", \"env_validation\" in inspect.getsource(a.handle_security_check)); print(\"generated_env_file\", c.generated_env_file(\"django.env\")); print(\"import_extract_marker\", \"extracted\" in inspect.getsource(i.import_capsule))'"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Capsule files"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "canonical capsule archive" sh -c "ls -lh $KX_ROOT/capsules/${CAPSULE_ID}.kxcap 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "extracted capsule root" sh -c "find $CAPSULE_DIR -maxdepth 2 -type f | sort | sed -n '1,120p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "manifest" sh -c "sed -n '1,120p' $CAPSULE_DIR/manifest.yaml 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "capsule compose images" sh -c "grep -n 'image:' $CAPSULE_DIR/docker-compose.capsule.yml 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "capsule images dir" sh -c "find $CAPSULE_DIR/images -maxdepth 2 -type f -ls 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Instance files"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "instance file tree" sh -c "find $KX_ROOT/instances/$INSTANCE_ID -maxdepth 4 -type f | sort | sed -n '1,160p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "env files" sh -c "ls -lah $KX_ROOT/instances/$INSTANCE_ID/env 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "env keys no secret values" sh -c "for f in $KX_ROOT/instances/$INSTANCE_ID/env/*.env; do [ -f \"$f\" ] || continue; echo \"--- $(basename \"$f\") ---\"; sed -n 's/^\([^=#][^=]*\)=.*/\1=<redacted>/p' \"$f\"; done"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose file exists" sh -c "ls -lh $COMPOSE_FILE 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose first 220 lines" sh -c "sed -n '1,220p' $COMPOSE_FILE 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose image lines" sh -c "grep -nE 'image:|pull_policy:|env_file:|ports:|container_name:' $COMPOSE_FILE 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "traefik dynamic" sh -c "sed -n '1,220p' $KX_ROOT/instances/$INSTANCE_ID/state/traefik-dynamic.yml 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Docker engine"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker version" docker version
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker info short" sh -c "docker info 2>/dev/null | sed -n '1,90p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker images" docker images
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker containers all" docker ps -a
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker networks kx" sh -c "docker network ls | grep -E 'kx-|konnaxion' || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "docker volumes kx" sh -c "docker volume ls | grep -E 'kx-|konnaxion' || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Docker Compose runtime"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose version" docker compose version
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose ps" docker compose --project-name "$PROJECT_NAME" --file "$COMPOSE_FILE" ps
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose config images" sh -c "docker compose --project-name \"$PROJECT_NAME\" --file \"$COMPOSE_FILE\" config --images 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "compose config validation" sh -c "docker compose --project-name \"$PROJECT_NAME\" --file \"$COMPOSE_FILE\" config >/tmp/kx-compose-config.out 2>/tmp/kx-compose-config.err; rc=$?; echo rc=$rc; sed -n '1,120p' /tmp/kx-compose-config.err; sed -n '1,180p' /tmp/kx-compose-config.out"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Image availability diagnosis"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "required images from compose" sh -c "grep -E '^[[:space:]]+image:' $COMPOSE_FILE 2>/dev/null | awk '{print $2}' | sort -u || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "missing required images locally" sh -c "for img in $(grep -E '^[[:space:]]+image:' $COMPOSE_FILE 2>/dev/null | awk '{print $2}' | sort -u); do docker image inspect \"$img\" >/dev/null 2>&1; if [ $? -ne 0 ]; then echo \"MISSING $img\"; else echo \"OK $img\"; fi; done"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "konnaxion image pull blockers" sh -c "grep -E 'konnaxion/(frontend-next|django-api)' $COMPOSE_FILE 2>/dev/null || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Recent container logs if any"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "logs traefik" sh -c "docker logs --tail 80 kx-${INSTANCE_ID}-traefik 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "logs frontend-next" sh -c "docker logs --tail 80 kx-${INSTANCE_ID}-frontend-next 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "logs django-api" sh -c "docker logs --tail 80 kx-${INSTANCE_ID}-django-api 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "logs postgres" sh -c "docker logs --tail 80 kx-${INSTANCE_ID}-postgres 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "logs redis" sh -c "docker logs --tail 80 kx-${INSTANCE_ID}-redis 2>&1 || true"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "HTTP checks from inside Droplet"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "local agent health" curl --max-time 10 -sS http://127.0.0.1:8765/v1/health
>> "%REMOTE_SCRIPT_LOCAL%" echo run "local app http 80" sh -c "curl -v --max-time 10 http://127.0.0.1:80/ 2>&1 | sed -n '1,80p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo run "local app https 443" sh -c "curl -vk --max-time 10 https://127.0.0.1:443/ 2>&1 | sed -n '1,100p'"
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo section "Summary"
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "Local Manager check is shown at the top of the Windows console before SSH."
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "Agent health is OK if /v1/health above returned JSON."
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "If listeners only show 127.0.0.1:8765, the app stack is not running."
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "If compose image diagnosis shows MISSING konnaxion/frontend-next:v14 or MISSING konnaxion/django-api:v14, the immediate blocker is missing app Docker images."
>> "%REMOTE_SCRIPT_LOCAL%" echo echo "Expected next fix: load/build app images on the Droplet, or change compose image names to real pushed registry images."
>> "%REMOTE_SCRIPT_LOCAL%" echo.
>> "%REMOTE_SCRIPT_LOCAL%" echo exit 0

exit /b 0