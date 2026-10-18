#requires -Version 7.0
<#
Lecture seule : trouve Organization(slug=fakeplant) dans la DB réellement utilisée par
le conteneur API Orgo en cours, ou par la configuration Node locale si pas de conteneur.
Aucun secret imprimé; aucune commande seed/migrate/apply/promote.
#>
[CmdletBinding()]
param(
    [string]$OrgoRoot = 'C:\mycode\Orgo\Orgo',
    [string]$WorldsRoot = 'C:\mycode\Konnaxion\Konnaxion_Worlds'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$scriptFile = Join-Path $PSScriptRoot 'inspect_orgo_tenant_readonly.cjs'
$sourceManifest = Join-Path $WorldsRoot 'universes\fakeplant\integrations\orgo.tandem.json'
Write-Host '=== TANDEM — IDENTITE ORGO / FAKEPLANT (LECTURE SEULE) ===' -ForegroundColor Cyan

if (!(Test-Path -LiteralPath $scriptFile -PathType Leaf)) { throw 'Fichier diagnostic JavaScript manquant.' }
if (!(Test-Path -LiteralPath (Join-Path $OrgoRoot 'apps\api\prisma\schema.prisma') -PathType Leaf)) {
    throw "Depot Orgo invalide : $OrgoRoot"
}
if (!(Test-Path -LiteralPath $sourceManifest -PathType Leaf)) {
    throw "Manifeste de liaison Konnaxion_Worlds manquant : $sourceManifest"
}
$manifest = Get-Content -LiteralPath $sourceManifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.contract -ne 'koa.tandem-universe/v1' -or $manifest.universe_key -ne 'fakeplant' -or
    $manifest.orgo.organization_slug -ne 'fakeplant') {
    throw 'Contrat ou slug de liaison FakePlant divergent (aucune requete executee).'
}
Write-Host '[OK] Liaison canonique FakePlant -> slug Orgo fakeplant'

$nodeSource = Get-Content -LiteralPath $scriptFile -Raw -Encoding UTF8
$mode = ''
$rawLines = @()
$exitCode = -1
Push-Location -LiteralPath $OrgoRoot
try {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    $apiRunning = $false
    if ($docker) {
        $services = @(& docker compose -f docker-compose.yml ps --services --status running 2>$null)
        if ($LASTEXITCODE -eq 0 -and @($services | Where-Object { $_.Trim() -eq 'api' }).Count -gt 0) {
            $apiRunning = $true
        }
    }
    if ($apiRunning) {
        $mode = 'DOCKER : service API Orgo demarre (base active du conteneur)'
        # node - lit le programme sur stdin : aucun fichier ecrit dans le conteneur.
        $rawLines = @($nodeSource | & docker compose -f docker-compose.yml exec -T api node - 2>$null)
        $exitCode = $LASTEXITCODE
    } else {
        $node = Get-Command node -ErrorAction SilentlyContinue
        if (!$node) { throw 'Pas de conteneur API Orgo en cours et Node.js introuvable.' }
        $nodeVersionRaw = (& node --version 2>$null | Select-Object -First 1)
        if ($nodeVersionRaw -match '^v(?<major>[0-9]+)\.(?<minor>[0-9]+)') {
            $major = [int]$Matches['major']
            $minor = [int]$Matches['minor']
            if ($major -lt 20 -or ($major -eq 20 -and $minor -lt 6)) {
                throw 'Node.js trop ancien pour --env-file. Installer Node.js >= 20.6 ou lancer le diagnostic dans l API Docker active.'
            }
        }

        $envFile = Join-Path $OrgoRoot '.env'
        $hasEnv = Test-Path -LiteralPath $envFile -PathType Leaf
        if (!$hasEnv -and !$env:DATABASE_URL) {
            throw 'Pas de conteneur API Orgo actif, ni de configuration DATABASE_URL locale. Aucun tenant invente.'
        }
        if ($hasEnv) {
            $mode = 'NODE LOCAL : Orgo/.env (ou DATABASE_URL de session prioritaire; confirmer la base reelle de l API)'
            $rawLines = @($nodeSource | & node --env-file=.env - 2>$null)
        } else {
            $mode = 'NODE LOCAL : DATABASE_URL de la session PowerShell (verifier la base cible)'
            $rawLines = @($nodeSource | & node - 2>$null)
        }
        $exitCode = $LASTEXITCODE
    }
} finally {
    Pop-Location
}
Write-Host "Mode : $mode"
$marker = '__KOA_ORGO_IDENTITY__='
$line = @($rawLines | Where-Object { $_ -is [string] -and $_.StartsWith($marker) } | Select-Object -Last 1)
if ($line.Count -eq 0) {
    Write-Host '[ECHEC] Aucune reponse structuree. Verifier Orgo, Prisma Client, DB et acces Node.' -ForegroundColor Red
    exit 2
}
try { $result = $line[0].Substring($marker.Length) | ConvertFrom-Json } catch {
    Write-Host '[ECHEC] Reponse structuree Orgo invalide.' -ForegroundColor Red; exit 2
}
if ($exitCode -ne 0 -or $result.ok -ne $true) {
    Write-Host "[ECHEC] Code : $($result.error_code)" -ForegroundColor Red
    if ($result.error_kind) { Write-Host "  Cause categorisee : $($result.error_kind)" -ForegroundColor Yellow }
    if ($result.error_stage) { Write-Host "  Etape : $($result.error_stage)" }
    if ($result.suggested_action) { Write-Host "  Action : $($result.suggested_action)" }
    Write-Host 'Aucune modification. Ne pas creer un tenant sans validation.'
    exit 2
}
if (!$result.organization_found) {
    Write-Host '[ABSENT] Organization(slug=fakeplant) non trouvee dans cette base Orgo.' -ForegroundColor Yellow
    Write-Host 'Le seed Orgo standard utilise ORGO_ORGANIZATION=orgo par defaut.'
    Write-Host 'Aucune creation, migration ou import effectue.'
    exit 0
}
$org = $result.organization
Write-Host "[OK] Tenant Orgo : $($org.slug)"
Write-Host "  UUID reel   : $($org.id)"
Write-Host "  Nom         : $($org.display_name)"
Write-Host "  Etat        : $($org.status)"
$flows = @($result.published_workflows)
Write-Host "  Versions de workflows publiees trouvees : $($flows.Count)"
if ($flows.Count -gt 0) {
    $flows | Select-Object workflow_code, workflow_version, workflow_version_id, scope | Format-Table -AutoSize | Out-Host
}
Write-Host 'IMPORTANT : droits ACL et Interaction Kernel toujours NON verifies.' -ForegroundColor Yellow
Write-Host 'Le binding Capsule Manager ne sera PAS cree automatiquement.'
