Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$manager = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$kx = 'C:\mycode\Konnaxion\Konnaxion\backend'
$orgo = 'C:\mycode\Orgo\Orgo'
$reportDir = 'C:\mycode\Konnaxion\Tandem_Local'
$reportFile = Join-Path $reportDir 'fakeplant-IK-E2E-LIVE.LOCAL.json'
$backupFile = Join-Path $reportDir 'fakeplant-IK-E2E-LIVE.ORIGINAL.LOCAL.json'
$kxCodePath = Join-Path $PSScriptRoot 'final_ik_konnaxion.py'
$orgoCodePath = Join-Path $PSScriptRoot 'final_ik_orgo.cjs'
$proofCodePath = Join-Path $PSScriptRoot 'final_ik_proof.cjs'
$uuid = '0c2d7244-a0c0-4a10-8f11-0389f275f133'

function Require-File([string]$p) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Fichier absent : $p" }
}
function Parse-Unique([object[]]$lines, [string]$prefix, [int]$exit) {
    $found = @($lines | Where-Object { ([string]$_).StartsWith($prefix) })
    if ($found.Count -ne 1) { throw "Reponse structuree absente ou multiple ($prefix)" }
    $obj = ([string]$found[0]).Substring($prefix.Length) | ConvertFrom-Json
    if ($exit -ne 0 -or $obj.ok -ne $true -or $obj.read_only -ne $true) {
        throw "Audit interrompu : $($obj.reason)"
    }
    return $obj
}
function Read-Django([string]$mode, $orgoData = $null) {
    $oldMode = [Environment]::GetEnvironmentVariable('KOA_FINAL_E2E_MODE', 'Process')
    $oldProof = [Environment]::GetEnvironmentVariable('KOA_FINAL_ORGO_JSON', 'Process')
    Push-Location $kx
    try {
        $env:KOA_FINAL_E2E_MODE = $mode
        if ($null -ne $orgoData) {
            $env:KOA_FINAL_ORGO_JSON = ConvertTo-Json -InputObject $orgoData -Depth 12 -Compress
        }
        $code = Get-Content -LiteralPath $kxCodePath -Raw -Encoding UTF8
        $output = @(& uv run --no-sync python manage.py shell -c $code 2>&1)
        $exit = $LASTEXITCODE
        return Parse-Unique $output 'KOA_FINAL_KX_JSON=' $exit
    } finally {
        if ($null -eq $oldMode) { Remove-Item Env:\KOA_FINAL_E2E_MODE -ErrorAction SilentlyContinue } else { $env:KOA_FINAL_E2E_MODE = $oldMode }
        if ($null -eq $oldProof) { Remove-Item Env:\KOA_FINAL_ORGO_JSON -ErrorAction SilentlyContinue } else { $env:KOA_FINAL_ORGO_JSON = $oldProof }
        Pop-Location
    }
}
function Read-Orgo {
    Push-Location $orgo
    try {
        # Inline pure-proof validator to avoid copying files or changing Docker FS.
        $helper = Get-Content -LiteralPath $proofCodePath -Raw -Encoding UTF8
        $script = Get-Content -LiteralPath $orgoCodePath -Raw -Encoding UTF8
        $import = "const { validateProof } = require('./final_ik_proof.cjs');"
        if (-not $script.Contains($import)) { throw 'Contrat du module de validation modifie' }
        $embedded = "const proofModule = {exports:{}}; (function(module) {`n$helper`n})(proofModule); const {validateProof} = proofModule.exports;"
        $script = $script.Replace($import, $embedded)
        $output = @($script | & docker compose exec -T api node - 2>&1)
        $exit = $LASTEXITCODE
        return Parse-Unique $output 'KOA_FINAL_ORGO_JSON=' $exit
    } finally { Pop-Location }
}
function Stable-Identity($proof) {
    return @(
      [string]$proof.konnaxion.decision_id,
      [string]$proof.konnaxion.interaction_id,
      [string]$proof.orgo.signal.id,
      [string]$proof.orgo.case.id,
      [string]$proof.orgo.task.id,
      [string]$proof.orgo.impact_operation.id,
      [string]$proof.neon_impact.id,
      [string]$proof.neon_impact.correlation_id
    ) -join '|'
}

Write-Host '=== TANDEM FakePlant — FINALISATION IK E2E V2 (LECTURE SEULE) ==='
Write-Host 'Decision 1 / World 58 / release 132 / tenant FakePlant.'
Write-Host 'Aucun POST IK, aucun workflow relance, aucun compte importe.'
foreach ($p in @($kxCodePath,$orgoCodePath,$proofCodePath,(Join-Path $kx 'manage.py'),(Join-Path $orgo 'docker-compose.yml'))) { Require-File $p }

$initial = Read-Django 'initial'
Write-Host "[OK] Konnaxion : decision $($initial.konnaxion.decision_id) publiee; emission $($initial.konnaxion.emission_status)."
$orgoState = Read-Orgo
Write-Host "[OK] Orgo : Signal $($orgoState.signal.id), workflow V$($orgoState.workflow.version), dossier et tache."
Write-Host "[OK] Orgo : operation d impact $($orgoState.impact_operation.status) ; recu $($orgoState.impact_operation.receipt_status)."
$cross = Read-Django 'corroborate' $orgoState
if ($cross.konnaxion.interaction_id -cne $initial.konnaxion.interaction_id) { throw 'Emission changee entre les lectures : refus' }
Write-Host "[OK] Neon : impact #$($cross.impact.id) publie ; operation et correlation exactes."
Write-Host '[OK] Non-duplication : decision, emission, signal, dossier, tache, workflow, operation, impact — 1 chacun.'

# Conserver les champs exploites par l'ancien rapport (mais avec les vraies preuves).
$kxCompat = $cross.konnaxion
$kxCompat | Add-Member -MemberType NoteProperty -Name 'impact_count' -Value 1 -Force
$kxCompat | Add-Member -MemberType NoteProperty -Name 'impacts' -Value @($cross.impact) -Force
$orgoCompat = $orgoState
$orgoCompat | Add-Member -MemberType NoteProperty -Name 'workflow_instances' -Value @($orgoState.workflow) -Force
$orgoCompat | Add-Member -MemberType NoteProperty -Name 'cases' -Value @($orgoState.case) -Force
$orgoCompat | Add-Member -MemberType NoteProperty -Name 'tasks' -Value @($orgoState.task) -Force
$orgoCompat | Add-Member -MemberType NoteProperty -Name 'impact_operations' -Value @($orgoState.impact_operation) -Force
$orgoCompat | Add-Member -MemberType NoteProperty -Name 'matched_case_task' -Value $true -Force

$report = [ordered]@{
    schema = 'koa.fakeplant-ik-e2e-local/v2'
    complete = $true
    verified_at = (Get-Date).ToUniversalTime().ToString('o')
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    verification = 'READ_ONLY_NEON_AND_ORGO_EXACT_CROSS_REFERENCE'
    source_world = 'fp-electromecanique-informatique'
    source_world_id = 58
    source_release_id = 132
    tenant_uuid = $uuid
    konnaxion = $kxCompat
    orgo = $orgoCompat
    neon_impact = $cross.impact
    neon_non_duplication = $cross.non_duplication
    boundary = [ordered]@{
       scope = 'one-synthetic-FakePlant-E2E'
       sso_human_verified = $false
       production_approved = $false
       other_universes_qualified = $false
       remote_databases_modified_by_this_report = $false
    }
}
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$tmpFile = Join-Path $reportDir ('.koa-ik-e2e-verified-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    if (Test-Path -LiteralPath $reportFile -PathType Leaf) {
        $current = Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($current.schema -eq $report.schema -and $current.complete -eq $true) {
            if ((Stable-Identity $current) -cne (Stable-Identity $report)) { throw 'Rapport V2 existant avec preuves divergentes; refus d ecrasement' }
            Write-Host '[OK] Rapport V2 deja present et identique : aucune reecriture.'
        } else {
            if (Test-Path -LiteralPath $backupFile) { throw 'Sauvegarde originale deja presente : refus d ecraser un rapport historique different' }
            if ($current.schema -ne 'koa.fakeplant-ik-e2e-local/v1' -or $current.source_release_id -ne 132 -or $current.tenant_uuid -ne $uuid) {
                throw 'Ancien rapport inattendu; aucun remplacement'
            }
            $raw = ConvertTo-Json -InputObject $report -Depth 18
            [IO.File]::WriteAllText($tmpFile,$raw,[Text.UTF8Encoding]::new($false))
            # PS 7/.NET requires a nonempty NTFS backup path, unlike our historical bug.
            [IO.File]::Replace($tmpFile,$reportFile,$backupFile,$true)
            Write-Host "[OK] Rapport V1 original conserve : $backupFile"
            Write-Host '[OK] Rapport corrige en V2 (complete=true).'
        }
    } else {
        $raw = ConvertTo-Json -InputObject $report -Depth 18
        [IO.File]::WriteAllText($tmpFile,$raw,[Text.UTF8Encoding]::new($false))
        [IO.File]::Move($tmpFile,$reportFile)
        Write-Host '[OK] Nouveau rapport V2 cree (complete=true).'
    }
} finally {
    if (Test-Path -LiteralPath $tmpFile -PathType Leaf) { Remove-Item -LiteralPath $tmpFile -Force -ErrorAction SilentlyContinue }
}
Write-Host "[RAPPORT] $reportFile"
Write-Host '[OK] E2E FAKEPLANT TERMINE : preuves exactes dans Orgo et Neon; aucun rejouement.'
