param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

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

function Install-ProjectPython {
    param([string]$Title, [string[]]$PipArguments)

    # Windows PowerShell treats native stderr as errors; pip warnings must still stream.
    $ErrorActionPreference = 'Continue'
    $width = [Math]::Max(20, [Math]::Min(100, $Host.UI.RawUI.WindowSize.Width - 4))
    $edge = '+' + ('-' * ($width + 2)) + '+'
    Write-Host $edge -ForegroundColor DarkCyan
    Write-Host ('| ' + $Title.PadRight($width) + ' |') -ForegroundColor Cyan
    Write-Host $edge -ForegroundColor DarkCyan
    & $python -m pip --disable-pip-version-check @PipArguments 2>&1 | ForEach-Object {
        $line = $_.ToString()
        $color = 'Gray'
        if ($line -match '^ERROR') { $color = 'Red' }
        elseif ($line -match '^WARNING') { $color = 'Yellow' }
        elseif ($line -match '^(Successfully|Requirement already satisfied)') { $color = 'Green' }
        elseif ($line -match '^(Collecting|Downloading|Installing|Building|Obtaining)') { $color = 'Cyan' }
        do {
            $length = [Math]::Min($width, $line.Length)
            Write-Host ('| ' + $line.Substring(0, $length).PadRight($width) + ' |') -ForegroundColor $color
            $line = $line.Substring($length)
        } while ($line.Length -gt 0)
    }
    $result = $LASTEXITCODE
    Write-Host $edge -ForegroundColor DarkCyan
    if ($result -ne 0) { throw "$Title failed (exit code $result)." }
}

Install-ProjectPython -Title 'Python dependencies' -PipArguments @('install', '-r', (Join-Path $root 'requirements.lock'))
Install-ProjectPython -Title 'Agent Utopia installation' -PipArguments @('install', '-e', $root)

Push-Location $root
try {
    & $python -m utopia.maintenance @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
