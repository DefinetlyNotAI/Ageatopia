$ErrorActionPreference = "Stop"

$nativeRoot = $PSScriptRoot
$cacheRoot = Join-Path $nativeRoot ".packages"
$sdkVersion = "1.0.4258.31"
$packageName = "microsoft.web.webview2.$sdkVersion.nupkg"
$packagePath = Join-Path $cacheRoot $packageName
$packageSha256 = "56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9"
$sdkRoot = Join-Path $cacheRoot "sdk"
$includeRoot = Join-Path $sdkRoot "build\native\include"
$importLibraryRoot = Join-Path $sdkRoot "build\native\x64"
$loaderPath = Join-Path $sdkRoot "runtimes\win-x64\native\WebView2Loader.dll"
$sourcePath = Join-Path $nativeRoot "ageatopia.c"
$outputRoot = Join-Path $nativeRoot "build"
$outputPath = Join-Path $outputRoot "ageatopia.exe"

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $hash = $algorithm.ComputeHash($stream)
        return [BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $packagePath) -or (Get-Sha256 $packagePath) -ne $packageSha256) {
    $downloadPath = Join-Path $cacheRoot "webview2-$sdkVersion.download.nupkg"
    $packageUrl = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$sdkVersion/$packageName"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $packageUrl -OutFile $downloadPath -UseBasicParsing
    if ((Get-Sha256 $downloadPath) -ne $packageSha256) {
        Remove-Item -LiteralPath $downloadPath -Force
        throw "The downloaded WebView2 SDK package failed its SHA-256 check."
    }
    Move-Item -LiteralPath $downloadPath -Destination $packagePath -Force
}

if ((Get-Sha256 $packagePath) -ne $packageSha256) {
    throw "The cached WebView2 SDK package failed its SHA-256 check."
}

if (-not (Test-Path -LiteralPath (Join-Path $includeRoot "WebView2.h")) -or
    -not (Test-Path -LiteralPath $loaderPath)) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stagingRoot = Join-Path $cacheRoot ("extract-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null
    $cacheBoundary = [System.IO.Path]::GetFullPath($cacheRoot).TrimEnd([char]'\') + "\"
    $resolvedStagingRoot = [System.IO.Path]::GetFullPath($stagingRoot)
    if (-not $resolvedStagingRoot.StartsWith(
            $cacheBoundary,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The SDK extraction path escaped the native package cache."
    }
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($packagePath, $stagingRoot)
        New-Item -ItemType Directory -Path $sdkRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $stagingRoot "*") -Destination $sdkRoot -Recurse -Force
    } finally {
        Remove-Item -LiteralPath $resolvedStagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $includeRoot "WebView2.h")) -or
    -not (Test-Path -LiteralPath (Join-Path $importLibraryRoot "WebView2Loader.dll.lib")) -or
    -not (Test-Path -LiteralPath $loaderPath)) {
    throw "The pinned WebView2 SDK package is missing required x64 files."
}
if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "The native host source is missing: $sourcePath"
}

$vswhereCandidates = @()
if ($env:ProgramFiles) {
    $vswhereCandidates += Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe"
}
if (${env:ProgramFiles(x86)}) {
    $vswhereCandidates += Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
}
$vswherePath = $vswhereCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$vcvarsPath = $null
if ($vswherePath) {
    $vsInstallPath = & $vswherePath -latest -products "*" -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -eq 0 -and $vsInstallPath) {
        $candidateVcvars = Join-Path $vsInstallPath "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path -LiteralPath $candidateVcvars) {
            $vcvarsPath = $candidateVcvars
        }
    }
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}

$compileLine = 'cl.exe /nologo /TC /W4 /DUNICODE /D_UNICODE /D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 /I"{0}" /Fo"{1}\ageatopia.obj" /Fe"{2}" "{3}" /link /SUBSYSTEM:WINDOWS /LIBPATH:"{4}" WebView2Loader.dll.lib comctl32.lib comdlg32.lib ole32.lib shell32.lib user32.lib winhttp.lib' -f $includeRoot, $outputRoot, $outputPath, $sourcePath, $importLibraryRoot

if ($vcvarsPath) {
    $commandLine = 'call "{0}" >nul && {1}' -f $vcvarsPath, $compileLine
    & $env:ComSpec /d /s /c $commandLine
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC failed to build the x64 Ageatopia host."
    }
} else {
    $compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
    if (-not $compiler) {
        throw "Visual Studio Build Tools with the x64 C++ workload were not found."
    }
    & $compiler.Source /nologo /TC /W4 /DUNICODE /D_UNICODE /D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 "/I$includeRoot" "/Fo$outputRoot\ageatopia.obj" "/Fe$outputPath" $sourcePath /link /SUBSYSTEM:WINDOWS "/LIBPATH:$importLibraryRoot" WebView2Loader.dll.lib comctl32.lib comdlg32.lib ole32.lib shell32.lib user32.lib winhttp.lib
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC failed to build the x64 Ageatopia host."
    }
}

Copy-Item -LiteralPath $loaderPath -Destination (Join-Path $outputRoot "WebView2Loader.dll") -Force
Write-Host "Built $outputPath"
