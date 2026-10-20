# =============================================================================
#  build-portable.ps1 -- produce a single-file pbrforge.exe
#
#  Result: dist\pbrforge.exe  (~11-14 MB, no Python required on the target PC)
#
#  Run from anywhere:
#      powershell -ExecutionPolicy Bypass -File deploy\build-portable.ps1
#
#  Options:
#      -Clean      wipe build\ and dist\ first
#      -Test       run the built exe's self-test before declaring success
#      -Zip        also produce dist\pbrforge-<version>-portable.zip
#
#  The build is verified, not assumed. A one-file PyInstaller build can succeed
#  while silently omitting the ui\ folder, and the failure only appears when
#  someone runs the exe on the PC they wanted to use it from -- which is the
#  worst possible time to find out. So the script runs the exe afterwards.
# =============================================================================

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Test = $true,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Push-Location $root
try {
    Write-Host ""
    Write-Host "  PBRForge - portable build" -ForegroundColor Cyan
    Write-Host "  $root"
    Write-Host ""

    # ---------------------------------------------------------------- Python
    $python = $null
    foreach ($candidate in @('python', 'python3')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { $python = $cmd.Source; break }
    }
    if (-not $python) {
        throw "Python is not on PATH. Install it from python.org and re-run."
    }

    $version = (& $python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
    if ([version]$version -lt [version]'3.11') {
        throw "Python $version found, but 3.11 or newer is required (tomllib)."
    }
    Write-Host "  python      $python  ($version)"

    # The Microsoft Store Python sandboxes the filesystem and intermittently
    # fails to write into build\ -- an error that reads as a PyInstaller bug.
    if ($python -like '*WindowsApps*') {
        Write-Host "  note        Microsoft Store Python detected." -ForegroundColor Yellow
        Write-Host "              The Store build sandboxes the filesystem and can fail"
        Write-Host "              mid-build. The python.org build is recommended."
    }

    # ------------------------------------------------------------ PyInstaller
    $have = & $python -c "import PyInstaller, sys; sys.stdout.write(PyInstaller.__version__)" 2>$null
    if (-not $have) {
        Write-Host ""
        Write-Host "  PyInstaller is not installed." -ForegroundColor Yellow
        Write-Host "  Installing it now (build-machine only; it is not a runtime dependency):"
        Write-Host ""
        & $python -m pip install --upgrade pyinstaller
        if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed." }
        $have = & $python -c "import PyInstaller, sys; sys.stdout.write(PyInstaller.__version__)"
    }
    Write-Host "  pyinstaller $have"

    # ---------------------------------------------------------------- sources
    $ui = Join-Path $root 'ui\prototype.html'
    if (-not (Test-Path $ui)) {
        throw "ui\prototype.html is missing. The exe would ship without a UI."
    }
    $entry = Join-Path $root 'pbrforge\__main__.py'
    if (-not (Test-Path $entry)) {
        throw "pbrforge\__main__.py is missing -- there is nothing to build."
    }

    if ($Clean) {
        Remove-Item -Recurse -Force (Join-Path $root 'build') -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force (Join-Path $root 'dist')  -ErrorAction SilentlyContinue
        Write-Host "  cleaned     build\ dist\"
    }

    # ------------------------------------------------------------------ build
    Write-Host ""
    Write-Host "  building..." -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $python -m PyInstaller (Join-Path $root 'deploy\pbrforge.spec') --noconfirm --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
    $sw.Stop()

    $exe = Join-Path $root 'dist\pbrforge.exe'
    if (-not (Test-Path $exe)) { throw "PyInstaller reported success but $exe does not exist." }
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "  built       dist\pbrforge.exe  ($mb MB in $([math]::Round($sw.Elapsed.TotalSeconds,1))s)" -ForegroundColor Green

    # ----------------------------------------------------------------- verify
    # Run the artefact, not the source tree. `version` proves the exe starts;
    # `selftest --quick` proves it can bind a socket, write a file and serve the
    # UI, which is what actually matters on the target PC.
    Write-Host ""
    Write-Host "  verifying the built exe..." -ForegroundColor Cyan

    # PBRFORGE_NO_PAUSE stops the exe from holding its console open waiting for a
    # keypress. The heuristic that decides "this was double-clicked from Explorer"
    # cannot tell that apart from "a build script ran me with no console", and an
    # unbuildable hang is worse than a window that closes early. See
    # pbrforge/cli.py:_pause_if_double_clicked.
    $env:PBRFORGE_NO_PAUSE = '1'

    $out = & $exe version 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "The built exe exited $LASTEXITCODE on 'version'.`n$out" }
    Write-Host "  $($out.Trim())"

    if ($Test) {
        $cfg = Join-Path $env:TEMP ("pbrforge-buildtest-" + [guid]::NewGuid().ToString('N').Substring(0,8))
        try {
            $env:PBRFORGE_CONFIG_DIR = $cfg
            $env:PBRFORGE_LIBRARY    = Join-Path $cfg 'library'
            # The FULL suite, not --quick. This is the one run where a broken
            # build must not slip through, and --quick used to skip the seam
            # metric -- the flagship number -- to save three seconds.
            $out = & $exe selftest 2>&1 | Out-String
            $code = $LASTEXITCODE
            Write-Host $out
            if ($code -ne 0) { throw "The built exe failed its own self-test (exit $code)." }
            # No count here on purpose. It said "12/12" until the suite grew to 13,
            # then to 14, and a stale number in a success message is worse than no
            # number -- the pass/fail detail is printed directly above by the exe.
            Write-Host "  self-test   passed inside the bundle" -ForegroundColor Green
        } finally {
            Remove-Item Env:\PBRFORGE_CONFIG_DIR -ErrorAction SilentlyContinue
            Remove-Item Env:\PBRFORGE_LIBRARY    -ErrorAction SilentlyContinue
            Remove-Item Env:\PBRFORGE_NO_PAUSE   -ErrorAction SilentlyContinue
            Remove-Item -Recurse -Force $cfg -ErrorAction SilentlyContinue
        }
    }

    # -------------------------------------------------------------------- zip
    if ($Zip) {
        $v = (& $exe version --json | ConvertFrom-Json).version
        $zip = Join-Path $root "dist\pbrforge-$v-portable.zip"
        Remove-Item $zip -ErrorAction SilentlyContinue
        Compress-Archive -Path $exe -DestinationPath $zip -CompressionLevel Optimal
        $zmb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
        Write-Host "  archive     dist\pbrforge-$v-portable.zip  ($zmb MB)" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "  Done. To use it:" -ForegroundColor Green
    Write-Host "    .\dist\pbrforge.exe doctor      check this machine"
    Write-Host "    .\dist\pbrforge.exe tls init    HTTPS so LAN clients get WebGPU"
    Write-Host "    .\dist\pbrforge.exe serve       start, then open the LAN URL"
    Write-Host ""
    Write-Host "  Or build the installable .exe:  deploy\build-installer.ps1"
    Write-Host ""
}
finally {
    Pop-Location
}
