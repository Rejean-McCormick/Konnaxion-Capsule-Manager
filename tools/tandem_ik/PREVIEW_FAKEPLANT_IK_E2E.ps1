Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$kx = 'C:\mycode\Konnaxion\Konnaxion'
$orgo = 'C:\mycode\Orgo\Orgo'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path

function Assert-True([bool]$Value, [string]$Message) {
    if (-not $Value) { throw "[BLOQUE] $Message" }
}

Write-Host '=== FAKEPLANT / IK E2E : PREFLIGHT LECTURE SEULE ==='
Write-Host 'Konnaxion uv/Neon, Orgo Docker, Redis Windows local, Celery Windows solo.'
Write-Host 'Aucun evenement, decision ou workflow cree. Aucun jeton affiche.'
Assert-True (Test-Path -LiteralPath (Join-Path $kx 'backend\manage.py')) 'Chemin backend Konnaxion absent.'
Assert-True (Test-Path -LiteralPath (Join-Path $orgo 'docker-compose.yml')) 'Chemin Orgo absent.'

Write-Host '\n--- Konnaxion / World et release ---'
Push-Location (Join-Path $kx 'backend')
try {
    $code = Get-Content -LiteralPath (Join-Path $base 'preview_konnaxion_e2e.py') -Raw -Encoding UTF8
    $lines = @(uv run --no-sync python manage.py shell -c $code 2>&1)
    Assert-True ($LASTEXITCODE -eq 0) 'Django ne peut pas verifier le World et sa release.'
    $found = @($lines | Where-Object { "$_" -like 'IK_E2E_KX_JSON=*' })
    Assert-True ($found.Count -eq 1) 'Sortie Django manquante.'
    $kxState = ("$($found[0])".Substring('IK_E2E_KX_JSON='.Length) | ConvertFrom-Json)
    Write-Host ($kxState | ConvertTo-Json -Depth 5)
    Assert-True ($kxState.ok -eq $true) 'World, release, DEBUG ou configuration IK Django non conformes.'
    Write-Host '[OK] World et release controles sans ecriture.'

    Write-Host '\n--- Redis et Worker Celery ---'
    $redisPong = @(docker exec konnaxion_ik_redis redis-cli ping 2>&1)
    Assert-True (($LASTEXITCODE -eq 0) -and (($redisPong -join ' ') -match '\bPONG\b')) 'Redis konnaxion_ik_redis indisponible.'
    $celeryPong = @(uv run --no-sync celery -A config.celery_app:app inspect ping --timeout=5 2>&1)
    Assert-True (($LASTEXITCODE -eq 0) -and (($celeryPong -join ' ') -match '\bpong\b')) 'Aucun worker Celery ne repond.'
    Write-Host '[OK] Redis PONG; Celery pong.'
} finally { Pop-Location }

Write-Host '\n--- Orgo Docker / Workflow / isolation ---'
Push-Location $orgo
try {
    $running = @(docker compose ps --services --status running 2>&1)
    Assert-True ($LASTEXITCODE -eq 0) 'Docker Compose indisponible.'
    foreach ($name in @('api','worker','postgres')) {
        Assert-True ($running -contains $name) "Service Orgo $name non demarre."
    }
    $js = Get-Content -LiteralPath (Join-Path $base 'preview_orgo_e2e.cjs') -Raw -Encoding UTF8
    $resultLines = @($js | docker compose exec -T api node - 2>&1)
    Assert-True ($LASTEXITCODE -eq 0) 'Le controle Prisma Orgo a echoue.'
    $found = @($resultLines | Where-Object { "$_" -like 'IK_E2E_ORGO_JSON=*' })
    Assert-True ($found.Count -eq 1) 'Sortie Orgo manquante.'
    $orgoState = ("$($found[0])".Substring('IK_E2E_ORGO_JSON='.Length) | ConvertFrom-Json)
    Write-Host ($orgoState | ConvertTo-Json -Depth 5)
    Assert-True ($orgoState.ok -eq $true) 'Workflow, etiquette, tenant ou personas Orgo non conformes.'
    Write-Host '[OK] Orgo API, Worker, tenant et workflow verifies.'
} finally { Pop-Location }

Write-Host '\n--- Reseau ---'
foreach ($url in @('http://127.0.0.1:8000/health/ready/','http://127.0.0.1:4000/health/ready')) {
    $response = Invoke-WebRequest -Uri $url -Method Get -TimeoutSec 5 -SkipHttpErrorCheck
    Assert-True ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) "Sante HTTP non OK : $url"
}
Write-Host '[OK] Konnaxion et Orgo repondent sur Windows.'
Write-Host '[OK] PREFLIGHT TERMINÉ. Aucun POST IK ni ecriture DB. E2E pas encore execute.'
