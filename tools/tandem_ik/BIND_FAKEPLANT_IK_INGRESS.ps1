[CmdletBinding()]
param([switch]$Preview)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# LOCAL DEVELOPMENT ONLY: Bind Orgo Worker's existing impact-publish bearer
# to Konnaxion's inbound IK handler. Never echoes the token.
$orgoRoot = 'C:\mycode\Orgo\Orgo'
$kxRoot = 'C:\mycode\Konnaxion\Konnaxion\backend'
$envFile = Join-Path $kxRoot '.env'
$workSecret = 'C:\mycode\Konnaxion\Tandem_Local\fakeplant-orgo-work-token.DPAPI.txt'
$expectedUrl = 'http://host.docker.internal:8000/api/integrations/ik/konnaxion/interactions/'
$expectedOrgoUrl = 'http://127.0.0.1:4000/api/v3/ik/interactions'
$orgId = '0c2d7244-a0c0-4a10-8f11-0389f275f133'
$token = $null
$temp = $null
$backup = $null

function Get-EnvSetting([string]$Content, [string]$Key) {
    $pattern = '(?m)^[ \t]*(?:export[ \t]+)?' + [regex]::Escape($Key) + '[ \t]*=[^\r\n]*'
    $matches = [regex]::Matches($Content, $pattern)
    if ($matches.Count -gt 1) { throw "Plusieurs declarations $Key : aucune ecriture" }
    if ($matches.Count -eq 0) { return @{ Present = $false; Value = ''; Match = $null } }
    $line = $matches[0].Value
    $rhs = $line.Substring($line.IndexOf('=') + 1).Trim()
    if ($rhs.StartsWith('"') -and $rhs.EndsWith('"') -and $rhs.Length -gt 1) { $rhs = $rhs.Substring(1, $rhs.Length - 2) }
    if ($rhs.StartsWith("'") -and $rhs.EndsWith("'") -and $rhs.Length -gt 1) { $rhs = $rhs.Substring(1, $rhs.Length - 2) }
    return @{ Present = $true; Value = $rhs; Match = $matches[0] }
}

try {
    if (-not $IsWindows) { throw 'Liaison DPAPI/.env concue pour PowerShell 7 sur Windows seulement' }
    if (-not (Test-Path -LiteralPath (Join-Path $orgoRoot 'docker-compose.yml') -PathType Leaf)) { throw 'Depot Orgo introuvable' }
    if (-not (Test-Path -LiteralPath (Join-Path $kxRoot 'manage.py') -PathType Leaf)) { throw 'Backend Konnaxion introuvable' }
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw 'backend/.env absent; refus de creer une configuration incomplete' }

    Push-Location -LiteralPath $orgoRoot
    try {
        $services = @(& docker compose ps --services --status running 2>$null)
        if ($LASTEXITCODE -ne 0 -or $services -notcontains 'worker') { throw 'Orgo Worker Docker indisponible' }
        # Only stdout is collected into a variable, and never displayed.
        $js = 'process.stdout.write(JSON.stringify({url:process.env.KONNAXION_IK_URL||"",token:process.env.KONNAXION_IK_TOKEN||"",world:process.env.KONNAXION_IK_WORLD||""}))'
        $response = (& docker compose exec -T worker node -e $js 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Impossible de lire la configuration du Worker' }
    } finally { Pop-Location }

    try { $worker = $response | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Configuration Worker non JSON; aucune ecriture' }
    $response = $null
    if ([string]$worker.url -cne $expectedUrl) { throw 'URL IK Worker inattendue; aucun secret copie' }
    if ($worker.world -and -not ([string]$worker.world).StartsWith('fp-', [System.StringComparison]::Ordinal)) {
        throw 'World Worker non FakePlant; aucun secret copie'
    }
    $token = [string]$worker.token
    $worker = $null
    if ($token -cnotmatch '^[A-Za-z0-9_-]{32,256}$') {
        throw 'Le jeton Worker manque ou ne satisfait pas au format de secret fort attendu; aucune ecriture'
    }

    # Safety: don't recycle the FP-S01 work token as IK impact token.
    if (Test-Path -LiteralPath $workSecret -PathType Leaf) {
        try {
            $secureWork = Get-Content -LiteralPath $workSecret -Raw | ConvertTo-SecureString -ErrorAction Stop
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureWork)
            try { $workRaw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            if ([string]::Equals($workRaw, $token, [StringComparison]::Ordinal)) {
                throw 'Le Worker utilise le jeton FP-S01 : refuse, secrets IK dedies requis'
            }
        } catch {
            if ($_.Exception.Message -match 'jeton FP-S01') { throw }
            throw 'Impossible de comparer avec le jeton FP-S01 DPAPI : refus prudent'
        } finally { $workRaw = $null; $secureWork = $null }
    }

    $bytes = [IO.File]::ReadAllBytes($envFile)
    $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191
    $skip = if ($bom) { 3 } else { 0 }
    $content = [Text.UTF8Encoding]::new($false, $true).GetString($bytes, $skip, $bytes.Length - $skip)
    $currentOrgo = Get-EnvSetting $content 'IK_ORGO_INTERACTIONS_URL'
    $currentOrg = Get-EnvSetting $content 'IK_ORGO_TARGET_ORGANIZATION'
    if ($currentOrgo.Value -cne $expectedOrgoUrl -or $currentOrg.Value -cne $orgId) {
        throw 'Configuration IK sortante Konnaxion inattendue : executer dabord CONFIG_TANDEM_IK_BASE.cmd'
    }
    $entry = Get-EnvSetting $content 'IK_ORGO_INBOUND_TOKEN'
    $action = 'deja conforme'
    if ($entry.Present -and $entry.Value -and $entry.Value -cne $token) {
        throw 'IK_ORGO_INBOUND_TOKEN deja different : aucun ecrasement ni modification'
    }
    if (-not $entry.Present -or -not $entry.Value) { $action = 'a configurer' }

    Write-Host '=== IK ORGO WORKER -> KONNAXION : SECRET ENTRANT ==='
    Write-Host '[OK] Worker Orgo actif, URL IK exacte, jeton fort, tenant de sortie FakePlant'
    Write-Host '[OK] Configuration de base Django et UUID FakePlant conformes'
    Write-Host '[OK] Aucun secret affiche; jeton FP-S01 non reutilise si fichier DPAPI disponible'
    if ($Preview) {
        Write-Host "[PREVIEW] Etat : $action. Aucune ecriture."
        return
    }
    if ($action -eq 'deja conforme') {
        Write-Host '[OK] Deja lie : aucune modification. Un redemarrage est requis seulement si Django charge encore une ancienne configuration.'
        return
    }

    $newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
    if ($entry.Present) {
        $m = $entry.Match
        $updated = $content.Substring(0, $m.Index) + ('IK_ORGO_INBOUND_TOKEN=' + $token) + $content.Substring($m.Index + $m.Length)
    } else {
        $join = if ($content.EndsWith("`n")) { '' } else { $newline }
        $updated = $content + $join + 'IK_ORGO_INBOUND_TOKEN=' + $token + $newline
    }
    $encoding = [Text.UTF8Encoding]::new($bom)
    $newBytes = $encoding.GetBytes($updated)
    $temp = Join-Path (Split-Path -Parent $envFile) ('.koa-ik-secret-' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllBytes($temp, $newBytes)
    # Windows PowerShell 7/.NET may reject null as backup path in File.Replace.
    # Use a unique path on the SAME NTFS volume for atomic replacement with ACL preserved.
    # The backup exists only during replacement; remove it immediately after success.
    $backup = Join-Path (Split-Path -Parent $envFile) ('.koa-ik-secret-' + [guid]::NewGuid().ToString('N') + '.bak')
    [IO.File]::Replace($temp, $envFile, $backup, $true)
    $temp = $null
    if (Test-Path -LiteralPath $backup -PathType Leaf) {
        Remove-Item -LiteralPath $backup -Force -ErrorAction Stop
    }
    $backup = $null
    Write-Host '[OK] IK_ORGO_INBOUND_TOKEN ajoute a backend/.env (secret non affiche).'
    Write-Host '[FIN] Aucune ecriture dans Neon ou PostgreSQL Orgo; redemarrer Uvicorn, puis tester le retour IK.'
} catch {
    Write-Host '[REFUS] Liaison / nettoyage non termines :' $_.Exception.Message -ForegroundColor Red
    if (-not $temp -and $backup) {
        Write-Host '[ATTENTION] Remplacement possiblement termine; reverifier en mode PREVIEW avant nouvelle tentative.' -ForegroundColor Yellow
    }
    exit 1
} finally {
    $token = $null
    if ($temp -and (Test-Path -LiteralPath $temp -PathType Leaf)) {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
    if ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
        try { Remove-Item -LiteralPath $backup -Force -ErrorAction Stop }
        catch { Write-Warning 'Sauvegarde temporaire .env non supprimee; attention aux secrets locaux.' }
    }
}
