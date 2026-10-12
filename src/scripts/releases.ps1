param([Alias('Version')][string]$v)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$manifest = Get-Content -LiteralPath (Join-Path $root 'src/gui/dashboard/package.json') -Raw | ConvertFrom-Json
if (-not $v -or $v -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$') {
    Write-Host 'ERROR: Specify -v with a version such as 0.1.0 or 1.2.3-beta.' -ForegroundColor Red
    exit 1
}
if ($v -cne $manifest.version) {
    Write-Host "ERROR: Version $v must exactly match package.json version $($manifest.version)." -ForegroundColor Red
    exit 1
}
$destination = Join-Path $root "dist/releases/$v"
if (Test-Path -LiteralPath $destination) {
    Write-Host "ERROR: Release directory already exists: $destination. Releases are never overwritten." -ForegroundColor Red
    exit 1
}
$python = Join-Path $root 'dist/python/venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'ERROR: Create the project Python environment and install .[release] before releasing.' -ForegroundColor Red
    exit 1
}
$env:PYTHONPATH = Join-Path $root 'src'
$env:PYTHONPYCACHEPREFIX = Join-Path $root 'dist/cache/python'
& $python -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('rich', 'textual', 'packaging')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'ERROR: Install release dependencies with python -m pip install -e .[release].' -ForegroundColor Red
    exit 1
}
Push-Location -LiteralPath $root
try {
    & $python -m gui.releases --version $v
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
