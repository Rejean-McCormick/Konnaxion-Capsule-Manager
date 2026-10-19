param(
    [ValidateSet('Apply','Status')][string]$Mode = 'Status'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$cmRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$kx = 'C:\mycode\Konnaxion\Konnaxion\backend'
$orgo = 'C:\mycode\Orgo\Orgo'
$pythonPath = Join-Path $PSScriptRoot 'ik_e2e_konnaxion.py'
$nodePath = Join-Path $PSScriptRoot 'ik_e2e_orgo.cjs'
$reportDir = 'C:\mycode\Konnaxion\Tandem_Local'
$reportFile = Join-Path $reportDir 'fakeplant-IK-E2E-LIVE.LOCAL.json'
$expectedOrg = '0c2d7244-a0c0-4a10-8f11-0389f275f133'

function Require-Path([string]$p) { if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Fichier requis manquant : $p" } }
Require-Path $pythonPath
Require-Path $nodePath
Require-Path (Join-Path $kx 'manage.py')
Require-Path (Join-Path $orgo 'docker-compose.yml')

function Read-Konnaxion([string]$action) {
    Push-Location $kx
    $old = [Environment]::GetEnvironmentVariable('KOA_FAKEPLANT_E2E_MODE', 'Process')
    try {
        $env:KOA_FAKEPLANT_E2E_MODE = $action
        $code = Get-Content -LiteralPath $pythonPath -Raw -Encoding UTF8
        $lines = @(& uv run --no-sync python manage.py shell -c $code 2>&1)
        $exit = $LASTEXITCODE
        $found = @($lines | Where-Object { "$_" -like 'KOA_FAKEPLANT_E2E_JSON=*' })
        if ($found.Count -ne 1) { throw 'Reponse Django introuvable. Verifier les logs Uvicorn/Celery et Django.' }
        $rawJson = [string]$found[0]
        $json = $rawJson.Substring('KOA_FAKEPLANT_E2E_JSON='.Length) | ConvertFrom-Json
        if ($exit -ne 0 -or -not $json.ok) { throw "Django E2E refuse : $($json.error) $($json.detail)" }
        if ($json.organization_id -ne $expectedOrg -or $json.release_id -ne 132) { throw 'World/release/tenant non conforme' }
        return $json
    } finally {
        if ($null -eq $old) { Remove-Item Env:\KOA_FAKEPLANT_E2E_MODE -ErrorAction SilentlyContinue } else { $env:KOA_FAKEPLANT_E2E_MODE = $old }
        Pop-Location
    }
}

function Read-Orgo([string]$decisionId) {
    if ($decisionId -notmatch '^(0|[1-9][0-9]{0,12})$') { throw 'ID decision non conforme' }
    Push-Location $orgo
    try {
        $script = Get-Content -LiteralPath $nodePath -Raw -Encoding UTF8
        $arg = "KOA_FAKEPLANT_DECISION_ID=$decisionId"
        $lines = @($script | & docker compose exec -T -e $arg api node - 2>&1)
        $exit = $LASTEXITCODE
        $found = @($lines | Where-Object { "$_" -like 'KOA_FAKEPLANT_ORGO_JSON=*' })
        if ($found.Count -ne 1) { throw 'Reponse de lecture Prisma Orgo manquante' }
        $rawJson = [string]$found[0]
        $json = $rawJson.Substring('KOA_FAKEPLANT_ORGO_JSON='.Length) | ConvertFrom-Json
        if ($exit -ne 0 -or -not $json.ok) { throw "Audit Orgo refuse : $($json.error)" }
        return $json
    } finally { Pop-Location }
}

Write-Host "=== TANDEM / IK E2E FakePlant : $Mode ==="
Write-Host 'World: fp-electromecanique-informatique / release 132; tenant UUID verrouille.'
Write-Host 'Aucun secret imprime. Pas de re-import des 900 personas.'
if ($Mode -eq 'Apply') {
    $preflight = Join-Path $cmRoot 'tools\tandem_ik\PREVIEW_FAKEPLANT_IK_E2E.ps1'
    Require-Path $preflight
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $preflight
    if ($LASTEXITCODE -ne 0) { throw 'Preflight bloquant; aucune decision creee' }
    $guard = Read-Orgo '0'
    if (-not $guard.workflow_v2_ready -or -not $guard.token_scopes_ok) { throw 'Workflow V2 ou droits du jeton IK non conformes : aucune decision creee' }
    Write-Host '[OK] Orgo : Workflow V2 et scopes IK requis toujours valides.'
    Write-Host '=== CREATION IDEMPOTENTE D UNE DECISION SYNTHETIQUE ==='
    $first = Read-Konnaxion 'apply'
    Write-Host "Decision Konnaxion : $($first.decision_id); interaction : $($first.interaction_id)"
} else {
    $first = Read-Konnaxion 'status'
    if (-not $first.decision_id) {
        Write-Host '[ATTENTE] Aucun test E2E cree. TEST_TANDEM_IK_E2E_FAKEPLANT.cmd est le lanceur d execution.'
        exit 0
    }
}
if (-not $first.decision_id -or -not $first.interaction_id) { throw 'Decision ou emission introuvable apres lancement' }
$cycles = if ($Mode -eq 'Apply') { 12 } else { 1 }
$passed = $false
for ($i = 1; $i -le $cycles; $i++) {
    if ($i -gt 1) { Start-Sleep -Seconds 5 }
    $kxState = Read-Konnaxion 'status'
    $orgoState = Read-Orgo ([string]$kxState.decision_id)
    Write-Host "[ETAT $i/$cycles] emission=$($kxState.emission_status), impacts=$($kxState.impact_count), signal=$([bool]$orgoState.signal), dossier+tache=$($orgoState.matched_case_task)"
    if ($kxState.emission_status -in @('dead','failed')) {
        Write-Host "[ECHEC IK] $($kxState.emission_last_error_code)"
        break
    }
    if ($orgoState.signal -and $orgoState.matched_case_task -and $kxState.impact_count -ge 1) {
        $passed = $true
        break
    }
}

# Local report contains only IDs, statuses, and counts; never tokens.
$report = [ordered]@{
    schema = 'koa.fakeplant-ik-e2e-local/v1'
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    complete = $passed
    source_world = 'fp-electromecanique-informatique'
    source_release_id = 132
    tenant_uuid = $expectedOrg
    konnaxion = $kxState
    orgo = $orgoState
}
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$report | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $reportFile -Encoding utf8
Write-Host "[RAPPORT] $reportFile"
if ($passed) {
    Write-Host '[OK] E2E confirme : decision -> signal Orgo -> dossier et tache -> impact publie dans Neon.'
    exit 0
}
Write-Host '[ATTENTE] Pas encore de preuve E2E complete. Consulter le rapport et les logs. Ne pas rejouer Apply.'
exit 2
