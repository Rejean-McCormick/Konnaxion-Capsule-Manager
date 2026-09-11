# KX_Diagnose_Online.ps1
# Read-only Konnaxion online stability diagnostic.
# It checks:
# - Local Capsule Manager state
# - Local capsule file
# - SSH reachability
# - Remote Konnaxion Agent
# - Docker / Compose runtime
# - Traefik routing
# - Public HTTP/HTTPS reachability
# - Service logs and restart loops
# - Host/domain/env mismatches
# - Forbidden public/internal ports
#
# No restart, no deploy, no write to /opt/konnaxion except copying this temporary diagnostic script to /tmp.

$ErrorActionPreference = "Continue"

# ----------------------------
# Defaults for the current Netcup VPS target
# ----------------------------
$LocalRepo     = "C:\mycode\Konnaxion\Konnaxion_Capsule_Manager"
$LocalSource   = "C:\mycode\Konnaxion\Konnaxion"
$LocalCapsule  = "C:\mycode\Konnaxion\runtime\capsules\konnaxion-v14-demo-2026.05.08.kxcap"

$DropletUser   = "root"
$DropletHost   = "2.56.97.41"
$SshPort       = "22"
$SshKey        = "C:\Users\rejea\.ssh\id_ed25519"

$Domain        = "konnaxion.com"
$InstanceId    = "konnaxion-prod"
$CapsuleId     = "konnaxion-v14-local-2026.09.09"
$CapsuleVer    = "2026.09.09-local.1"
$RemoteKxRoot  = "/opt/konnaxion"
$RemoteCapsDir = "/opt/konnaxion/capsules"

$Remote        = "$DropletUser@$DropletHost"
$Stamp         = Get-Date -Format "yyyyMMdd-HHmmss"
$OutDir        = Join-Path $env:TEMP "kx-online-diagnostic-$Stamp"
$LocalRemoteSh = Join-Path $OutDir "kx-remote-diagnostic.sh"
$LogFile       = Join-Path $OutDir "kx-online-diagnostic-$Stamp.log"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Section($Name) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host $Name
    Write-Host "============================================================"
}

function Run-Step($Name, [scriptblock]$Block) {
    Section $Name
    try {
        & $Block
    } catch {
        Write-Host "ERROR in step: $Name"
        Write-Host $_.Exception.Message
    }
}

function Test-Http($Url) {
    Write-Host ""
    Write-Host ">>> HTTP probe: $Url"
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 12 -MaximumRedirection 5 -Uri $Url
        $sw.Stop()
        Write-Host ("OK status={0} ms={1} bytes={2}" -f [int]$r.StatusCode, $sw.ElapsedMilliseconds, $r.RawContentLength)
        $ctype = $r.Headers["Content-Type"]
        if ($ctype) { Write-Host "Content-Type: $ctype" }
        if ($r.Content) {
            $head = $r.Content.Substring(0, [Math]::Min(300, $r.Content.Length))
            Write-Host "--- body head ---"
            Write-Host $head
        }
    } catch {
        Write-Host "FAIL $Url"
        Write-Host $_.Exception.Message
    }
}

function Test-Tcp($HostName, $Port) {
    Write-Host ""
    Write-Host ">>> TCP probe: $HostName`:$Port"
    try {
        $r = Test-NetConnection -ComputerName $HostName -Port $Port -InformationLevel Detailed
        $r | Format-List ComputerName,RemoteAddress,RemotePort,TcpTestSucceeded,PingSucceeded,ResolvedAddresses
    } catch {
        Write-Host "FAIL TCP $HostName`:$Port"
        Write-Host $_.Exception.Message
    }
}

function Redact-Text($Text) {
    if ($null -eq $Text) { return "" }
    $s = [string]$Text
    $s = $s -replace '(?i)(PASSWORD|SECRET|TOKEN|PRIVATE_KEY|API_KEY|DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|AUTHORIZATION|COOKIE)(\s*[:=]\s*)[^\s"'';]+', '$1$2[REDACTED]'
    $s = $s -replace '(?i)(postgres://[^:]+:)[^@]+@', '$1[REDACTED]@'
    $s = $s -replace '(?i)(redis://:)[^@]+@', '$1[REDACTED]@'
    return $s
}

Start-Transcript -Path $LogFile -Force | Out-Null

Write-Host "Konnaxion Online Stability Diagnostic"
Write-Host "Timestamp:     $Stamp"
Write-Host "Local repo:    $LocalRepo"
Write-Host "Local source:  $LocalSource"
Write-Host "Local capsule: $LocalCapsule"
Write-Host "Droplet:       $Remote"
Write-Host "Domain:        $Domain"
Write-Host "Instance:      $InstanceId"
Write-Host "Capsule:       $CapsuleId"
Write-Host "Output dir:    $OutDir"
Write-Host "Log file:      $LogFile"

Run-Step "1. Local tools" {
    foreach ($tool in @("ssh.exe","scp.exe","curl.exe","tar.exe","uv.exe","git.exe","python.exe")) {
        Write-Host ""
        Write-Host ">>> where $tool"
        where.exe $tool 2>&1
    }

    Write-Host ""
    Write-Host "PowerShell:"
    $PSVersionTable | Format-List
}

Run-Step "2. Local paths and state" {
    Write-Host "Repo exists:       $(Test-Path $LocalRepo)"
    Write-Host "Source exists:     $(Test-Path $LocalSource)"
    Write-Host "Capsule exists:    $(Test-Path $LocalCapsule)"
    Write-Host "SSH key exists:    $(Test-Path $SshKey)"

    if (Test-Path $LocalCapsule) {
        Get-Item $LocalCapsule | Format-List FullName,Length,CreationTime,LastWriteTime
        Write-Host ""
        Write-Host ">>> SHA256 capsule"
        Get-FileHash -Algorithm SHA256 $LocalCapsule | Format-List
    }

    $stateFile = Join-Path $LocalRepo ".kx-ui\manager-ui-state.json"
    Write-Host ""
    Write-Host "Manager UI state file: $stateFile"
    if (Test-Path $stateFile) {
        $raw = Get-Content $stateFile -Raw
        Write-Host (Redact-Text $raw)
    } else {
        Write-Host "WARN: manager-ui-state.json not found."
    }
}

Run-Step "3. Local git and Python project" {
    if (Test-Path $LocalRepo) {
        Push-Location $LocalRepo

        Write-Host ">>> git status --short"
        git status --short 2>&1

        Write-Host ""
        Write-Host ">>> git rev-parse HEAD"
        git rev-parse HEAD 2>&1

        Write-Host ""
        Write-Host ">>> pyproject scripts"
        if (Test-Path "pyproject.toml") {
            Select-String -Path "pyproject.toml" -Pattern "kx =|kx-agent|kx-manager|kx-builder|python_version|packages" -Context 0,2
        }

        Pop-Location
    }
}

Run-Step "4. Local Manager ports and HTTP probes" {
    foreach ($p in @(8501, 8714, 8000, 8080, 8766, 8765)) {
        Write-Host ""
        Write-Host ">>> Local listen check 127.0.0.1:$p"
        Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
            Format-Table -AutoSize LocalAddress,LocalPort,State,OwningProcess
    }

    Write-Host ""
    Write-Host ">>> Local Python / uv / streamlit / uvicorn processes"
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match "python|uv|streamlit|uvicorn" -or
            $_.CommandLine -match "kx_manager|Konnaxion|streamlit|uvicorn|kx-agent|kx-manager"
        } |
        Select-Object ProcessId,Name,CommandLine |
        Format-List

    foreach ($url in @(
        "http://127.0.0.1:8501",
        "http://127.0.0.1:8501/_stcore/health",
        "http://127.0.0.1:8714/health",
        "http://127.0.0.1:8714/v1/health",
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8080/health",
        "http://127.0.0.1:8766/health"
    )) {
        Test-Http $url
    }
}

Run-Step "5. Public DNS and local internet probes" {
    Write-Host ">>> Resolve-DnsName $Domain"
    Resolve-DnsName $Domain -ErrorAction Continue | Format-Table -AutoSize

    Write-Host ""
    Write-Host ">>> nslookup $Domain"
    nslookup $Domain 2>&1

    Test-Tcp $DropletHost 22
    Test-Tcp $DropletHost 80
    Test-Tcp $DropletHost 443
    Test-Tcp $DropletHost 8765

    Test-Tcp $Domain 80
    Test-Tcp $Domain 443
    Test-Tcp $Domain 8765

    Test-Http "http://$Domain/"
    Test-Http "https://$Domain/"
    Test-Http "https://$Domain/api/"
    Test-Http "https://$Domain/admin/"
    Test-Http "https://$Domain/media/"

    Write-Host ""
    Write-Host ">>> curl.exe timing probes"
    foreach ($url in @("http://$Domain/","https://$Domain/","https://$Domain/api/","https://$Domain/admin/")) {
        Write-Host ""
        Write-Host "URL: $url"
        curl.exe -k -L --connect-timeout 10 --max-time 25 `
            -o NUL `
            -w "http_code=%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} remote_ip=%{remote_ip}`n" `
            $url 2>&1
    }
}

# ----------------------------
# Build remote bash diagnostic
# ----------------------------
$RemoteScript = @'
#!/usr/bin/env bash
set +e
set -o pipefail

INSTANCE_ID="__INSTANCE_ID__"
CAPSULE_ID="__CAPSULE_ID__"
CAPSULE_VERSION="__CAPSULE_VERSION__"
DOMAIN="__DOMAIN__"
DROPLET_HOST="__DROPLET_HOST__"
KX_ROOT="__REMOTE_KX_ROOT__"
CAPSULE_DIR="__REMOTE_CAPSULE_DIR__"
COMPOSE_FILE="${KX_ROOT}/instances/${INSTANCE_ID}/state/docker-compose.runtime.yml"
CAPSULE_FILE="${CAPSULE_DIR}/${CAPSULE_ID}.kxcap"
MANAGER_DIR="${KX_ROOT}/manager"
AGENT_HEALTH_1="http://127.0.0.1:8765/v1/health"
AGENT_HEALTH_2="http://127.0.0.1:8765/health"

SERVICES=(
  traefik
  frontend-next
  django-api
  postgres
  redis
  celeryworker
  celerybeat
  flower
  media-nginx
)

FORBIDDEN_PORTS=(3000 5000 5432 6379 5555 8000 2375 2376)

section() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

redact() {
  sed -E \
    -e 's#((PASSWORD|SECRET|TOKEN|PRIVATE_KEY|API_KEY|DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|AUTHORIZATION|COOKIE)[A-Za-z0-9_ -]*[=:][[:space:]]*)[^[:space:]"'"'"';]+#\1[REDACTED]#Ig' \
    -e 's#(postgres://[^:]+:)[^@]+@#\1[REDACTED]@#Ig' \
    -e 's#(redis://:)[^@]+@#\1[REDACTED]@#Ig'
}

run() {
  echo
  echo ">>> $*"
  bash -lc "$*" 2>&1 | redact
  local rc=${PIPESTATUS[0]}
  echo "<<< rc=${rc}"
}

curl_probe() {
  local url="$1"
  echo
  echo ">>> curl probe: ${url}"
  curl -k -L -sS --connect-timeout 8 --max-time 25 \
    -o /tmp/kx_diag_curl_body.txt \
    -w "http_code=%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} size=%{size_download} remote_ip=%{remote_ip}\n" \
    "$url" 2>&1 | redact
  echo "--- body head ---"
  head -c 500 /tmp/kx_diag_curl_body.txt 2>/dev/null | redact
  echo
}

curl_host_probe() {
  local scheme="$1"
  local target="$2"
  local host="$3"
  local path="$4"
  local url="${scheme}://${target}${path}"

  echo
  echo ">>> curl Host-header probe: ${url} Host: ${host}"
  curl -k -L -sS --connect-timeout 8 --max-time 25 \
    -H "Host: ${host}" \
    -o /tmp/kx_diag_curl_body.txt \
    -w "http_code=%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} size=%{size_download} remote_ip=%{remote_ip}\n" \
    "$url" 2>&1 | redact
  echo "--- body head ---"
  head -c 500 /tmp/kx_diag_curl_body.txt 2>/dev/null | redact
  echo
}

section "REMOTE 0. Diagnostic identity"
echo "UTC date:       $(date -u)"
echo "Host:           $(hostname)"
echo "User:           $(whoami)"
echo "Instance:       ${INSTANCE_ID}"
echo "Capsule:        ${CAPSULE_ID}"
echo "Capsule ver:    ${CAPSULE_VERSION}"
echo "Domain:         ${DOMAIN}"
echo "Droplet host:   ${DROPLET_HOST}"
echo "KX root:        ${KX_ROOT}"
echo "Compose file:   ${COMPOSE_FILE}"
echo "Capsule file:   ${CAPSULE_FILE}"

section "REMOTE 1. OS, uptime, resources"
run "hostnamectl || true"
run "uname -a"
run "uptime"
run "free -h"
run "df -h"
run "df -ih"
run "top -b -n 1 | head -80"
run "ps aux --sort=-%mem | head -40"
run "journalctl --disk-usage || true"
run "dmesg -T | tail -120 || true"

section "REMOTE 2. Network, DNS, routes, listeners"
run "ip -br addr"
run "ip route"
run "getent hosts ${DOMAIN} || true"
run "which dig >/dev/null 2>&1 && dig +short ${DOMAIN} || true"
run "which nslookup >/dev/null 2>&1 && nslookup ${DOMAIN} || true"
run "ss -ltnup"
run "ss -s"
run "curl -sS --connect-timeout 5 https://ifconfig.me || true; echo"

section "REMOTE 3. Firewall and public surface"
run "ufw status verbose || true"
run "iptables -S || true"
run "iptables -t nat -S || true"
run "nft list ruleset 2>/dev/null | head -300 || true"

echo
echo "Forbidden public/internal direct ports should NOT be listening publicly:"
for p in "${FORBIDDEN_PORTS[@]}"; do
  echo
  echo ">>> port ${p}"
  ss -ltnp "sport = :${p}" 2>&1 | redact
done

section "REMOTE 4. Konnaxion folder layout"
run "ls -la ${KX_ROOT} || true"
run "find ${KX_ROOT} -maxdepth 3 -type d -printf '%M %u:%g %p\n' 2>/dev/null | sort | head -300"
run "find ${KX_ROOT} -maxdepth 4 -type f \( -name '*.kxcap' -o -name '*.yml' -o -name '*.yaml' -o -name '*.json' -o -name '*.env' -o -name '*.log' \) -printf '%s bytes %TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort | head -400"
run "stat ${CAPSULE_FILE} || true"
run "ls -lah ${CAPSULE_DIR} || true"

section "REMOTE 5. Agent service and private health"
run "systemctl status konnaxion-agent --no-pager -l || true"
run "systemctl cat konnaxion-agent --no-pager || true"
run "journalctl -u konnaxion-agent -n 260 --no-pager || true"

curl_probe "${AGENT_HEALTH_1}"
curl_probe "${AGENT_HEALTH_2}"

echo
echo "Agent must not be public. This should fail or be blocked:"
curl_probe "http://${DROPLET_HOST}:8765/v1/health"

section "REMOTE 6. Manager/Agent code installed on droplet"
run "ls -la ${MANAGER_DIR} || true"
run "cd ${MANAGER_DIR} 2>/dev/null && pwd && git rev-parse HEAD 2>/dev/null && git status --short 2>/dev/null || true"
run "cd ${MANAGER_DIR} 2>/dev/null && python3 --version && which python3 && which uv && uv --version || true"
run "cd ${MANAGER_DIR} 2>/dev/null && test -f pyproject.toml && grep -nE 'kx-agent|kx-manager|kx-builder|kx =' pyproject.toml || true"
run "cd ${MANAGER_DIR} 2>/dev/null && find . -maxdepth 2 -type f \( -name '*.log' -o -name '*.json' -o -name '*.env' \) -printf '%s bytes %TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort | head -200 || true"

section "REMOTE 7. Docker daemon"
run "systemctl status docker --no-pager -l || true"
run "journalctl -u docker -n 160 --no-pager || true"
run "docker version || true"
run "docker info || true"
run "docker system df || true"
run "docker ps -a --no-trunc || true"
run "docker images || true"
run "docker volume ls || true"
run "docker network ls || true"
run "docker stats --no-stream || true"

section "REMOTE 8. Container restart / health summary"
run 'for cid in $(docker ps -aq 2>/dev/null); do docker inspect --format "{{.Name}} status={{.State.Status}} running={{.State.Running}} restart={{.RestartCount}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} image={{.Config.Image}}" "$cid"; done'

section "REMOTE 9. Docker Compose runtime file"
if [ -f "${COMPOSE_FILE}" ]; then
  echo "Compose file exists: ${COMPOSE_FILE}"
  run "ls -lah ${COMPOSE_FILE}"
  run "sed -n '1,260p' ${COMPOSE_FILE}"
  run "docker compose -f ${COMPOSE_FILE} config --quiet"
  run "docker compose -f ${COMPOSE_FILE} ps"
  run "docker compose -f ${COMPOSE_FILE} ps --format json || true"
  run "docker compose -f ${COMPOSE_FILE} images || true"
else
  echo "ERROR: Compose file missing: ${COMPOSE_FILE}"
  run "find ${KX_ROOT}/instances/${INSTANCE_ID} -maxdepth 5 -type f -printf '%s bytes %p\n' 2>/dev/null | sort || true"
fi

section "REMOTE 10. Runtime env and host/domain propagation"
run "find ${KX_ROOT}/instances/${INSTANCE_ID} -maxdepth 4 -type f \( -name '*.env' -o -name '*env*' -o -name '*.json' -o -name '*.yml' -o -name '*.yaml' \) -printf '%s bytes %TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort"

run "grep -RInE 'KX_HOST|KX_HOST_ALIASES|KX_NETWORK_PROFILE|KX_EXPOSURE_MODE|KX_PUBLIC_MODE|DJANGO_ALLOWED_HOSTS|CSRF|NEXT_PUBLIC|TRAEFIK|${DOMAIN}|${DROPLET_HOST}|sslip' ${KX_ROOT}/instances/${INSTANCE_ID}/env ${KX_ROOT}/instances/${INSTANCE_ID}/state ${MANAGER_DIR}/.kx-ui 2>/dev/null | head -260"

section "REMOTE 11. Traefik files and routing clues"
run "find ${KX_ROOT}/instances/${INSTANCE_ID} -maxdepth 6 -type f \( -iname '*traefik*' -o -iname '*dynamic*' -o -iname '*router*' -o -iname '*tls*' -o -iname '*acme*' \) -printf '%s bytes %p\n' 2>/dev/null | sort"
run "find ${KX_ROOT} -maxdepth 7 -type f \( -iname 'acme.json' -o -iname '*cert*' -o -iname '*letsencrypt*' \) -printf '%s bytes %p\n' 2>/dev/null | sort"
run "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -E 'traefik|frontend|django|media|postgres|redis|celery|flower|NAMES' || true"

section "REMOTE 12. Internal route probes through local Traefik"
curl_host_probe "http"  "127.0.0.1" "${DOMAIN}" "/"
curl_host_probe "http"  "127.0.0.1" "${DOMAIN}" "/api/"
curl_host_probe "http"  "127.0.0.1" "${DOMAIN}" "/admin/"
curl_host_probe "http"  "127.0.0.1" "${DOMAIN}" "/media/"

curl_host_probe "https" "127.0.0.1" "${DOMAIN}" "/"
curl_host_probe "https" "127.0.0.1" "${DOMAIN}" "/api/"
curl_host_probe "https" "127.0.0.1" "${DOMAIN}" "/admin/"

section "REMOTE 13. Public HTTP/HTTPS probes from droplet"
curl_probe "http://${DOMAIN}/"
curl_probe "https://${DOMAIN}/"
curl_probe "https://${DOMAIN}/api/"
curl_probe "https://${DOMAIN}/admin/"
curl_probe "https://${DOMAIN}/media/"

section "REMOTE 14. TLS certificate"
run "echo | openssl s_client -connect ${DOMAIN}:443 -servername ${DOMAIN} -showcerts 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName || true"
run "echo | openssl s_client -connect ${DROPLET_HOST}:443 -servername ${DOMAIN} -showcerts 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName || true"

section "REMOTE 15. Capsule archive surface"
if [ -f "${CAPSULE_FILE}" ]; then
  run "sha256sum ${CAPSULE_FILE}"
  run "tar -tf ${CAPSULE_FILE} 2>/dev/null | head -200 || true"
  run "tar -xOf ${CAPSULE_FILE} manifest.yaml 2>/dev/null | sed -n '1,220p' || true"
  run "tar -xOf ${CAPSULE_FILE} docker-compose.capsule.yml 2>/dev/null | sed -n '1,260p' || true"
  run "tar -xOf ${CAPSULE_FILE} profiles/public_vps.yaml 2>/dev/null | sed -n '1,220p' || true"
else
  echo "WARN: Capsule file not found: ${CAPSULE_FILE}"
fi

section "REMOTE 16. Canonical service inspect and logs"
if [ -f "${COMPOSE_FILE}" ]; then
  for svc in "${SERVICES[@]}"; do
    echo
    echo "------------------------------------------------------------"
    echo "SERVICE ${svc}"
    echo "------------------------------------------------------------"

    cid="$(docker compose -f "${COMPOSE_FILE}" ps -q "${svc}" 2>/dev/null)"
    if [ -n "${cid}" ]; then
      echo "container_id=${cid}"
      docker inspect --format 'name={{.Name}} status={{.State.Status}} running={{.State.Running}} restart={{.RestartCount}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} started={{.State.StartedAt}} finished={{.State.FinishedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} image={{.Config.Image}} ports={{json .NetworkSettings.Ports}}' "${cid}" 2>&1 | redact

      echo
      echo ">>> recent health log for ${svc}"
      docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{.Start}} exit={{.ExitCode}} output={{printf "%q" .Output}}{{"\n"}}{{end}}{{else}}no healthcheck{{end}}' "${cid}" 2>&1 | tail -50 | redact

      echo
      echo ">>> last 260 compose logs for ${svc}"
      docker compose -f "${COMPOSE_FILE}" logs --tail=260 "${svc}" 2>&1 | redact
    else
      echo "WARN: no container found for service ${svc}"
      docker compose -f "${COMPOSE_FILE}" ps "${svc}" 2>&1 | redact
    fi
  done
else
  echo "Skipping service logs because compose file is missing."
fi

section "REMOTE 17. Application-specific backend clues"
if [ -f "${COMPOSE_FILE}" ]; then
  DJANGO_CID="$(docker compose -f "${COMPOSE_FILE}" ps -q django-api 2>/dev/null)"
  FRONTEND_CID="$(docker compose -f "${COMPOSE_FILE}" ps -q frontend-next 2>/dev/null)"
  TRAEFIK_CID="$(docker compose -f "${COMPOSE_FILE}" ps -q traefik 2>/dev/null)"
  POSTGRES_CID="$(docker compose -f "${COMPOSE_FILE}" ps -q postgres 2>/dev/null)"
  REDIS_CID="$(docker compose -f "${COMPOSE_FILE}" ps -q redis 2>/dev/null)"

  if [ -n "${DJANGO_CID}" ]; then
    run "docker exec ${DJANGO_CID} sh -lc 'python --version; env | sort | grep -E \"DJANGO|DATABASE|REDIS|CELERY|KX_|ALLOWED|CSRF|HOST|DEBUG\"' "
    run "docker exec ${DJANGO_CID} sh -lc 'python manage.py check --deploy 2>&1 | head -160' "
    run "docker exec ${DJANGO_CID} sh -lc 'python manage.py showmigrations --plan 2>&1 | tail -120' "
  fi

  if [ -n "${FRONTEND_CID}" ]; then
    run "docker exec ${FRONTEND_CID} sh -lc 'node --version 2>/dev/null || true; env | sort | grep -E \"NEXT|KX_|HOST|API|BACKEND\"' "
  fi

  if [ -n "${POSTGRES_CID}" ]; then
    run "docker exec ${POSTGRES_CID} sh -lc 'pg_isready -U konnaxion -d konnaxion 2>&1 || pg_isready 2>&1 || true' "
  fi

  if [ -n "${REDIS_CID}" ]; then
    run "docker exec ${REDIS_CID} sh -lc 'redis-cli ping 2>&1 || true' "
  fi

  if [ -n "${TRAEFIK_CID}" ]; then
    run "docker exec ${TRAEFIK_CID} sh -lc 'traefik version 2>&1 || true; ls -R /etc/traefik 2>/dev/null | head -200' "
  fi
fi

section "REMOTE 18. Recent system errors"
run "journalctl -p warning..alert -n 260 --no-pager || true"
run "grep -RInE 'error|exception|traceback|fail|timeout|refused|bad gateway|502|503|504|connection reset|allowed host|csrf|migration|database|redis' ${KX_ROOT}/instances/${INSTANCE_ID}/logs ${KX_ROOT}/instances/${INSTANCE_ID}/state 2>/dev/null | tail -260 || true"

section "REMOTE 19. Diagnosis hints"
echo "Read the sections above in this order:"
echo "1) Public DNS/probes: domain must resolve to ${DROPLET_HOST}."
echo "2) Agent: 127.0.0.1:8765 should pass locally and fail publicly."
echo "3) Docker restart summary: restart counts > 0 identify unstable containers."
echo "4) Compose ps: unhealthy/exited services identify runtime failures."
echo "5) Traefik logs: 404/502/Bad Gateway means routing or upstream service failure."
echo "6) django-api logs: AllowedHost/CSRF/DB/migration errors explain online instability."
echo "7) Host propagation: KX_HOST/DJANGO_ALLOWED_HOSTS/NEXT_PUBLIC should use ${DOMAIN}, not stale IP unless intended."
echo "8) Forbidden ports: 3000/5000/5432/6379/5555/8000/2375/2376 should not be public."

section "REMOTE 20. End"
echo "Remote diagnostic finished at $(date -u)."
'@

$RemoteScript = $RemoteScript.Replace("__INSTANCE_ID__", $InstanceId)
$RemoteScript = $RemoteScript.Replace("__CAPSULE_ID__", $CapsuleId)
$RemoteScript = $RemoteScript.Replace("__CAPSULE_VERSION__", $CapsuleVer)
$RemoteScript = $RemoteScript.Replace("__DOMAIN__", $Domain)
$RemoteScript = $RemoteScript.Replace("__DROPLET_HOST__", $DropletHost)
$RemoteScript = $RemoteScript.Replace("__REMOTE_KX_ROOT__", $RemoteKxRoot)
$RemoteScript = $RemoteScript.Replace("__REMOTE_CAPSULE_DIR__", $RemoteCapsDir)

Set-Content -Path $LocalRemoteSh -Value $RemoteScript -Encoding UTF8

Run-Step "6. SSH reachability" {
    ssh.exe -i $SshKey `
        -p $SshPort `
        -o BatchMode=yes `
        -o ConnectTimeout=15 `
        -o StrictHostKeyChecking=accept-new `
        $Remote `
        "echo SSH_OK && hostname && date -u && whoami"
}

Run-Step "7. Copy remote diagnostic to /tmp" {
    scp.exe -i $SshKey `
        -P $SshPort `
        $LocalRemoteSh `
        "${Remote}:/tmp/kx-remote-diagnostic-$Stamp.sh"
}

Run-Step "8. Execute remote diagnostic" {
    ssh.exe -i $SshKey `
        -p $SshPort `
        -o BatchMode=yes `
        -o ConnectTimeout=15 `
        $Remote `
        "tr -d '\r' < /tmp/kx-remote-diagnostic-$Stamp.sh > /tmp/kx-remote-diagnostic-$Stamp.unix.sh && chmod 700 /tmp/kx-remote-diagnostic-$Stamp.unix.sh && bash /tmp/kx-remote-diagnostic-$Stamp.unix.sh"
}

Run-Step "9. Final local summary" {
    Write-Host "Diagnostic output directory:"
    Write-Host $OutDir
    Write-Host ""
    Write-Host "Transcript log:"
    Write-Host $LogFile
    Write-Host ""
    Write-Host "Most useful failure signatures to look for:"
    Write-Host "- DNS does not resolve $Domain to $DropletHost"
    Write-Host "- Public :443 fails, but internal 127.0.0.1 Traefik probe works"
    Write-Host "- konnaxion-agent unhealthy or not listening on 127.0.0.1:8765"
    Write-Host "- docker compose ps shows exited/unhealthy containers"
    Write-Host "- django-api logs show AllowedHost, CSRF, migration, database, Redis, or static/media errors"
    Write-Host "- traefik logs show 502/Bad Gateway, unknown router, certificate, or upstream connection errors"
    Write-Host "- env/state contains stale host=$DropletHost when it should use $Domain"
    Write-Host "- forbidden services are listening publicly: 3000, 5000, 5432, 6379, 5555, 8000, 2375, 2376"
}

Stop-Transcript | Out-Null

Write-Host ""
Write-Host "DONE"
Write-Host "Log file: $LogFile"