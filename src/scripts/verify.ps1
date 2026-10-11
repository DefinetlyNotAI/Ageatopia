param(
    [ValidateSet('All', 'Unit', 'Integration')]
    [string]$Suite = 'All'
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
$python = Join-Path $repository 'dist/python/venv/Scripts/python.exe'
$env:PYTHONPYCACHEPREFIX = Join-Path $repository "dist/cache/python"
$env:PYTHONPATH = Join-Path $repository "src"
$env:TEMP = Join-Path $repository "dist/cache/tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$previousIntegration = $env:UTOPIA_INTEGRATION
Push-Location -LiteralPath $repository
try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Create dist/python/venv and install the dev dependencies first; see README.md.'
    }
    if ($Suite -ne 'Integration') {
        $env:UTOPIA_INTEGRATION = '0'
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
    }
    if ($Suite -ne 'Unit') {
        docker image inspect agent-utopia-world:1 --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) {
            throw 'Build the sandbox once with: docker build -t agent-utopia-world:1 src/hooks/sandbox'
        }
        $env:UTOPIA_INTEGRATION = '1'
        & $python -m pytest tests/test_integration.py -q
        if ($LASTEXITCODE -ne 0) { throw 'Integration tests failed.' }
    }
} finally {
    $env:UTOPIA_INTEGRATION = $previousIntegration
    Pop-Location
}
