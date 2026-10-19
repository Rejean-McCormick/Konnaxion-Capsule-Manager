[CmdletBinding()]
param([ValidateSet('menu','audit','resume','status','export','guard-install','inventory','ik-proof','guard-preview','sso-acl','konfid-inventory')]
    [string]$Mode='menu')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$manager=(Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backend=(Join-Path (Split-Path -Parent $manager) 'Konnaxion\backend')
$script=Join-Path $PSScriptRoot 'orchestrateur.py'
if (-not (Test-Path -LiteralPath $script)) { throw 'Orchestrateur introuvable.' }
if (-not (Test-Path -LiteralPath (Join-Path $backend 'manage.py'))) { throw 'Backend Konnaxion absent.' }
if ($Mode -eq 'menu') {
    Write-Host ''
    Write-Host '=== ORCHESTRATEUR TANDEM FAKEPLANT ==='
    Write-Host '1. Audit complet : IK + SSO/ACL + Konfid (sans ecriture DB)'
    Write-Host '2. Reprendre les controles en attente ou en echec'
    Write-Host '3. Etat du dernier passage'
    Write-Host '4. Installer le garde-fou SSO Konnaxion (seul changement de code)'
    Write-Host '5. Preparer le ZIP de diagnostic local'
    Write-Host '6. Quitter'
    $choice=Read-Host 'Option (1-6)'
    $choices=@{'1'='audit';'2'='resume';'3'='status';'4'='guard-install';'5'='export';'6'='quit'}
    if (-not $choices.ContainsKey($choice)) { Write-Host '[ATTENTE] Choix non valide';exit 10 }
    $Mode=$choices[$choice]
    if ($Mode -eq 'quit') {exit 0}
}
Push-Location $backend
try {
    & uv run --no-sync python $script $Mode
    $rc=$LASTEXITCODE
    if ($rc -eq 10) {
        Write-Host '[ATTENTE] Diagnostic execute; configuration SSO/Konfid encore incomplete.'
    } elseif ($rc -eq 20) {
        Write-Host '[BLOQUE] Voir le rapport de diagnostic. Ne pas lancer de correction aveugle.'
    } elseif ($rc -eq 0) {
        Write-Host '[OK] Operation terminee.'
    } else { Write-Host "[ECHEC] Code de retour inattendu : $rc" }
    exit $rc
} finally { Pop-Location }
