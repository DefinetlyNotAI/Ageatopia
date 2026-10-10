param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "Agent Utopia | Starting desktop" -ForegroundColor Cyan
Write-Host "[1/3] Checking Python environment..." -ForegroundColor DarkCyan

if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv (Join-Path $root ".venv")
    } else {
        $systemPython = Get-Command python -ErrorAction Stop
        & $systemPython.Source -m venv (Join-Path $root ".venv")
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project virtual environment." }
}

& $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Agent Utopia requires Python 3.12 or later." }

& $python -c "import fastapi, textual"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $root "requirements.lock")
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
    & $python -m pip install -e $root
    if ($LASTEXITCODE -ne 0) { throw "Agent Utopia installation failed." }
}
Write-Host "[2/3] Preparing local services..." -ForegroundColor DarkCyan

Push-Location $root
try {
    & $python -m utopia.desktop @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
