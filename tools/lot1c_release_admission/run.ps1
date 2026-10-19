param([ValidateSet('preview','apply','verify','rollback')][string]$Mode='preview')
$ErrorActionPreference='Stop'
$script=Join-Path $PSScriptRoot 'installer.py'
if (-not (Test-Path -LiteralPath $script)) { throw 'Installer Lot 1C manquant' }
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 $script $Mode
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python $script $Mode
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
  & uv run --no-sync python $script $Mode
} else { throw 'Python 3 est requis.' }
exit $LASTEXITCODE
