param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

$ErrorActionPreference = "Stop"
$env:PYTHONPYCACHEPREFIX = Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))) "dist/cache/python"
$env:PYTHONPATH = Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))) "src"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$env:TEMP = Join-Path $root "dist/cache/tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $root "dist/cache/pip"
$env:HF_HOME = Join-Path $root "dist/cache/huggingface"
$env:TORCH_HOME = Join-Path $root "dist/cache/torch"
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$python = Join-Path $root "dist\python\venv\Scripts\python.exe"
if (-not $Arguments) { $Arguments = @() }
Write-Host "Agent Utopia | Starting desktop" -ForegroundColor Cyan
Write-Host "[1/4] Cleaning up previous Ageatopia processes..." -ForegroundColor DarkCyan

if ($Arguments -notcontains "--window-only") {
    $snapshot = @(Get-CimInstance Win32_Process)
    $protected = [Collections.Generic.HashSet[uint32]]::new()
    $ancestorId = [uint32]$PID
    while ($ancestorId -and $protected.Add($ancestorId)) {
        $ancestor = $snapshot | Where-Object ProcessId -eq $ancestorId | Select-Object -First 1
        if (-not $ancestor) { break }
        $ancestorId = [uint32]$ancestor.ParentProcessId
    }
    $owned = [Collections.Generic.HashSet[uint32]]::new()
    $repoPath = [IO.Path]::GetFullPath($root).TrimEnd('\').ToLowerInvariant()
    foreach ($entry in $snapshot) {
        if ($protected.Contains([uint32]$entry.ProcessId)) { continue }
        $command = ([string]$entry.CommandLine).Replace('/', '\').ToLowerInvariant()
        $executable = ([string]$entry.ExecutablePath).ToLowerInvariant()
        $isPython = $entry.Name -match '^python(w)?\.exe$' -and (
            $executable.StartsWith("$repoPath\dist\python\venv\") -or
            ($command.Contains("$repoPath\dist\python\venv\") -and $command -match '-m\s+(gui|backend)\.(desktop|cli)')
        )
        $isDashboard = $entry.Name -eq 'node.exe' -and $command.Contains("$repoPath\dist\gui\dashboard\node_modules\next\")
        $isWindow = $entry.Name -eq 'ageatopia.exe' -and $executable -eq "$repoPath\dist\native\build\ageatopia.exe"
        $isWebView = $entry.Name -eq 'msedgewebview2.exe' -and $command.Contains("$repoPath\.data\")
        if ($isPython -or $isDashboard -or $isWindow -or $isWebView) { [void]$owned.Add([uint32]$entry.ProcessId) }
    }
    do {
        $added = $false
        foreach ($entry in $snapshot) {
            if (-not $protected.Contains([uint32]$entry.ProcessId) -and $owned.Contains([uint32]$entry.ParentProcessId)) {
                if ($owned.Add([uint32]$entry.ProcessId)) { $added = $true }
            }
        }
    } while ($added)
    $configPath = Join-Path $root '.data\desktop.json'
    $configIndex = [Array]::IndexOf($Arguments, '--config')
    if ($configIndex -ge 0 -and $configIndex + 1 -lt $Arguments.Count) { $configPath = $Arguments[$configIndex + 1] }
    if (Test-Path -LiteralPath $configPath) {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $backendPort = if ($config.backend_port) { $config.backend_port } else { 8000 }
        $dataPath = if ($config.data_dir) { $config.data_dir } else { Join-Path $root '.data' }
        $tokenPath = Join-Path $dataPath 'operator.token'
        $listener = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
        if ($listener -and $owned.Contains([uint32]$listener[0].OwningProcess) -and (Test-Path -LiteralPath $tokenPath)) {
            try {
                $headers = @{ Authorization = 'Bearer ' + (Get-Content -LiteralPath $tokenPath -Raw).Trim() }
                $null = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$backendPort/shutdown" -Headers $headers -TimeoutSec 5
                Wait-Process -Id $listener[0].OwningProcess -Timeout 10 -ErrorAction SilentlyContinue
            } catch { Write-Host 'Graceful shutdown unavailable; stopping remaining owned processes.' -ForegroundColor DarkYellow }
        }
    }
    foreach ($entry in $snapshot | Where-Object { $owned.Contains([uint32]$_.ProcessId) }) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.ProcessId)"
        if ($current -and $current.CreationDate -eq $entry.CreationDate) {
            Write-Host "Stopping $($entry.Name) (PID $($entry.ProcessId))" -ForegroundColor DarkYellow
            try { Stop-Process -Id $entry.ProcessId -Force -ErrorAction Stop }
            catch { if (Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue) { throw } }
        }
    }
    Write-Host "Previous Ageatopia process cleanup complete." -ForegroundColor DarkGray
}

Write-Host "[2/4] Checking Python environment..." -ForegroundColor DarkCyan

if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.11 -m venv (Join-Path $root "dist\python\venv")
        if ($LASTEXITCODE -ne 0) {
            & $launcher.Source -3 -m venv (Join-Path $root "dist\python\venv")
        }
    } else {
        $systemPython = Get-Command python -ErrorAction Stop
        & $systemPython.Source -m venv (Join-Path $root "dist\python\venv")
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project virtual environment." }
}

& $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Agent Utopia requires Python 3.11 or later." }

& $python -c "import fastapi, textual"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $root "requirements.lock")
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
    & $python -m pip install -e $root
    if ($LASTEXITCODE -ne 0) { throw "Agent Utopia installation failed." }
}
Write-Host "[3/4] Preparing local services..." -ForegroundColor DarkCyan

Push-Location $root
try {
    & $python -m gui.desktop @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
