# =============================================================================
#  build-installer.ps1 -- produce the double-clickable PBRForge-Setup-<v>.exe
#
#  Chains the two stages and verifies both:
#      dist\pbrforge.exe                 the portable single-file server
#      dist\PBRForge-Setup-<version>.exe the installer a person can double-click
#
#  Usage:
#      powershell -ExecutionPolicy Bypass -File deploy\build-installer.ps1
#      ... -SkipPortable     reuse an existing dist\pbrforge.exe
#      ... -Version 2.0.0    override the version stamped into the file names
#
#  Inno Setup is not bundled with anything and is not a Python package, so the
#  script locates it, and if it is missing it says exactly what to install and
#  where to get it. Guessing or auto-downloading a compiler is not something a
#  build script should do quietly.
# =============================================================================

[CmdletBinding()]
param(
    [switch]$SkipPortable,
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Push-Location $root
try {
    function Ok   { param([string]$t) Write-Host "  [ok]   $t" -ForegroundColor Green }
    function Info { param([string]$t) Write-Host "  $t" -ForegroundColor Gray }
    function Bad  { param([string]$t) Write-Host "  [fail] $t" -ForegroundColor Red }
    function Head { param([string]$t) Write-Host ""; Write-Host "  $t" -ForegroundColor Cyan }

    Write-Host ""
    Write-Host "  PBRForge - installer build" -ForegroundColor Cyan
    Write-Host "  ==========================" -ForegroundColor Cyan

    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { Bad "Python is not on PATH."; exit 1 }

    # ------------------------------------------------------------- 1. portable
    Head "1. Portable executable"
    if ($SkipPortable) {
        if (-not (Test-Path 'dist\pbrforge.exe')) {
            Bad "-SkipPortable was given but dist\pbrforge.exe does not exist."
            exit 1
        }
        Info "reusing dist\pbrforge.exe"
    } else {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'deploy\build-portable.ps1')
        if ($LASTEXITCODE -ne 0) { Bad "the portable build failed."; exit 1 }
    }

    $exe = Join-Path $root 'dist\pbrforge.exe'
    if (-not (Test-Path $exe)) { Bad "$exe does not exist."; exit 1 }
    $exeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Ok "dist\pbrforge.exe  ($exeMB MB)"

    # -------------------------------------------------------------- 2. version
    Head "2. Version"
    if (-not $Version) {
        $Version = (& $exe version --json | ConvertFrom-Json).version
    }
    if (-not $Version) { Bad "could not determine the version."; exit 1 }
    Ok "version $Version"

    # ------------------------------------------------------------ 3. Inno Setup
    Head "3. Inno Setup"
    $iscc = $null
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )) {
        if ($candidate -and (Test-Path $candidate)) { $iscc = $candidate; break }
    }
    if (-not $iscc) {
        # winget/scoop shims do not always land in Program Files.
        $cmd = Get-Command iscc -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) {
        Bad "Inno Setup 6 was not found."
        Info ""
        Info "  Install it once on this build machine:"
        Info "      winget install JRSoftware.InnoSetup"
        Info "  or download from https://jrsoftware.org/isdl.php"
        Info ""
        Info "  Then re-run this script. The portable build in dist\ already works"
        Info "  on its own if you would rather not install a compiler."
        exit 1
    }
    Ok $iscc

    # ---------------------------------------------------------- 4. preflight
    Head "4. Preflight"
    $required = @('README.md','INSTALLATION.md','DEPLOYMENT.md','CHANGELOG.md',
                  'LICENSE.txt','REQUIREMENTS.txt')
    $missing = $required | Where-Object { -not (Test-Path (Join-Path $root $_)) }
    if ($missing) {
        # The .iss references these. Inno fails with a file-not-found pointing at
        # a line number in a generated script, which is a poor way to learn that a
        # document is missing.
        Bad "these files are referenced by the installer but do not exist:"
        $missing | ForEach-Object { Info "    $_" }
        Info ""
        Info "Write them first, or remove their [Files] entries from"
        Info "installer\pbrforge.iss."
        exit 1
    }
    Ok "all $($required.Count) referenced documents present"

    if (-not (Test-Path 'docs')) { Bad "docs\ is missing."; exit 1 }
    $docCount = (Get-ChildItem 'docs' -Filter '*.md' -Recurse).Count
    Ok "docs\ contains $docCount markdown files"

    # -------------------------------------------------------------- 5. compile
    Head "5. Compile"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # The /D values are quoted explicitly. ISCC is a native executable, so its
    # command line is split on whitespace -- and this repository's own path
    # contains a space ("Default Project"), which would otherwise truncate
    # SourceRoot at the space and send Inno looking for files in a directory
    # that does not exist.
    & $iscc (Join-Path $root 'installer\pbrforge.iss') `
        "/DAppVersion=`"$Version`"" `
        "/DSourceRoot=`"$root`""
    if ($LASTEXITCODE -ne 0) { Bad "ISCC failed with exit code $LASTEXITCODE."; exit 1 }
    $sw.Stop()

    $setup = Join-Path $root "dist\PBRForge-Setup-$Version.exe"
    if (-not (Test-Path $setup)) {
        Bad "ISCC reported success but $setup does not exist."
        exit 1
    }
    $setupMB = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Ok "PBRForge-Setup-$Version.exe  ($setupMB MB in $([math]::Round($sw.Elapsed.TotalSeconds,1))s)"

    # ------------------------------------------------------------- 6. manifest
    Head "6. SHA-256"
    $hashes = foreach ($f in @($exe, $setup)) {
        $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
        "{0}  {1}" -f $h, (Split-Path $f -Leaf)
    }
    $hashes | ForEach-Object { Info $_ }
    $hashes -join "`n" | Set-Content (Join-Path $root 'dist\SHA256SUMS.txt') -Encoding ASCII
    Ok "dist\SHA256SUMS.txt"

    Head "Done"
    Write-Host "  dist\pbrforge.exe                           portable, no installer" -ForegroundColor Green
    Write-Host "  dist\PBRForge-Setup-$Version.exe            double-click to install" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Copy the installer to another PC and run it. The server and the" -ForegroundColor DarkGray
    Write-Host "  imaging pipeline run from the bundled executable; no Python is needed." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Not code-signed, so SmartScreen will warn on first run on a new PC:" -ForegroundColor DarkGray
    Write-Host "  More info -> Run anyway. Signing needs a paid certificate and stops" -ForegroundColor DarkGray
    Write-Host "  being worth it for a personal build." -ForegroundColor DarkGray
    Write-Host ""
}
finally {
    Pop-Location
}
