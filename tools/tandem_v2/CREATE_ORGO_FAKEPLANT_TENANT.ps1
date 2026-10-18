#requires -Version 7.0
<#
One-click workflow for minimal FakePlant tenant provisioning IN THE RUNNING ORGO API DB.
First checks are read-only, writes require explicit exact confirmation.
Never calls migrate, db:seed, fakeplant-people, or creates administrator users.
#>
[CmdletBinding()]
param(
    [string]$OrgoRoot = 'C:\mycode\Orgo\Orgo',
    [string]$WorldsRoot = 'C:\mycode\Konnaxion\Konnaxion_Worlds',
    [switch]$PreviewOnly
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$jsFile = Join-Path $PSScriptRoot 'provision_orgo_fakeplant_tenant.cjs'
$manifestFile = Join-Path $WorldsRoot 'universes\fakeplant\integrations\orgo.tandem.json'
Write-Host '=== ORGO / FAKEPLANT : CREATION MINIMALE DU TENANT ===' -ForegroundColor Cyan
Write-Host 'Ne modifie jamais Konnaxion, les 900 personnes, les workflows ou les autres tenants.'
if (!(Test-Path -LiteralPath $jsFile -PathType Leaf)) { throw "Script manquant : $jsFile" }
if (!(Test-Path -LiteralPath (Join-Path $OrgoRoot 'apps\api\prisma\schema.prisma') -PathType Leaf)) { throw "Depot Orgo introuvable : $OrgoRoot" }
if (!(Test-Path -LiteralPath $manifestFile -PathType Leaf)) { throw "Manifeste de liaison absent : $manifestFile" }
$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.contract -ne 'koa.tandem-universe/v1' -or $manifest.universe_key -ne 'fakeplant' -or $manifest.orgo.organization_slug -ne 'fakeplant') {
    throw 'Liaison FakePlant diverge du contrat attendu. Aucune execution.'
}
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker introuvable. Demarrer Docker Desktop.' }
$src = Get-Content -LiteralPath $jsFile -Raw -Encoding UTF8
function Invoke-ContainerPrisma([string]$operation) {
    $marker = '__KOA_ORGO_TENANT_V2__='
    $resultLines = @($src | & docker compose -f docker-compose.yml exec -T api node - "--$operation" 2>$null)
    $exitCode = $LASTEXITCODE
    $last = @($resultLines | Where-Object { $_ -is [string] -and $_.StartsWith($marker) } | Select-Object -Last 1)
    if ($last.Count -ne 1) { throw "Aucun resultat structure. Orgo / Prisma non disponibles (etape $operation)." }
    $result = $last[0].Substring($marker.Length) | ConvertFrom-Json
    if ($exitCode -ne 0 -or $result.ok -ne $true) {
        throw "Orgo refuse $operation (code : $($result.error_code)). Aucun autre objet modifie par ce script."
    }
    return $result
}
Push-Location -LiteralPath $OrgoRoot
try {
    $services = @(& docker compose -f docker-compose.yml ps --services --status running 2>$null)
    if ($LASTEXITCODE -ne 0 -or !($services -contains 'api') -or !($services -contains 'postgres')) {
        throw 'Les services api et postgres doivent etre demarres dans Docker Compose Orgo.'
    }
    $apiContainer = @(& docker compose -f docker-compose.yml ps -q api 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or !$apiContainer[0]) { throw 'Conteneur API introuvable.' }
    Write-Host "Orgo Docker API : $($apiContainer[0].Substring(0, [Math]::Min(12, $apiContainer[0].Length)))"
    $preview = Invoke-ContainerPrisma 'preview'
    Write-Host "Base Orgo active : $($preview.database)"
    if ($preview.database -ne 'orgo') { throw 'Nom de base inattendu (orgo requis) : aucune ecriture.' }
    if ($preview.organization) {
        Write-Host '[EXISTANT] FakePlant est deja present. Aucune ecriture necessaire.' -ForegroundColor Green
        $preview.organization | Select-Object slug,id,status,display_name,timezone,default_locale | Format-List | Out-Host
        if ($preview.organization.status -ne 'active') {
            Write-Host 'ATTENTION : organisation non active. Aucune modification automatique.' -ForegroundColor Yellow
            exit 2
        }
        Write-Host 'Poursuivre ensuite avec le diagnostic d identite et les tests IK.'
        exit 0
    }
    Write-Host '[ABSENT] FakePlant non trouve. Creation possible : 1 seule ligne Organization.' -ForegroundColor Yellow
    if ($PreviewOnly) { Write-Host '[PREVIEW] Aucune ecriture.'; exit 0 }
    Write-Host 'ATTENTION : la base utilisee est celle du conteneur Orgo. Cette action est persistante.' -ForegroundColor Yellow
    $confirm = Read-Host 'Pour creer UNIQUEMENT ce tenant, taper exactement CREER fakeplant (sinon annulation)'
    if ($confirm -cne 'CREER fakeplant') { Write-Host '[ANNULE] Aucune ecriture.'; exit 0 }
    $result = Invoke-ContainerPrisma 'apply'
    if ($result.organization.slug -ne 'fakeplant' -or $result.organization.status -ne 'active' -or !$result.verified) {
        throw 'Verification post-creation incoherente. Inspecter Orgo avant toute autre action.'
    }
    Write-Host "[OK] Tenant : $($result.organization.slug)" -ForegroundColor Green
    Write-Host "UUID : $($result.organization.id)"
    Write-Host "Cree pendant cette operation : $($result.created)"
    Write-Host 'Aucun utilisateur, aucun workflow, aucun scenario ni jeton cree.'
    Write-Host 'IMPORTANT : UUID connu ne signifie PAS que les permissions ou Interaction Kernel sont operationnels.'
} finally {
    Pop-Location
}
