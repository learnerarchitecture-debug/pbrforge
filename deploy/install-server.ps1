# =============================================================================
#  install-server.ps1 -- set this machine up as the PBRForge server
#
#  This is the "one machine hosts, the others just open a URL" installer. It is
#  the source-tree equivalent of running the packaged installer, and it does the
#  five things that are easy to get wrong by hand:
#
#     1. installs to a versioned directory with a `current` junction, so an
#        upgrade is atomic and a rollback is one command
#     2. keeps config, the secret and the TLS material OUT of the install
#        directory, so upgrading never logs out every paired client
#     3. opens the firewall (private profile only) so other PCs can connect
#     4. creates the certificate, so LAN clients keep WebGPU
#     5. registers a logon task so the server comes back after a reboot
#
#  Usage -- from the repository root, in a normal PowerShell window:
#
#      powershell -ExecutionPolicy Bypass -File deploy\install-server.ps1
#
#      -Port 7788            the port clients will use
#      -Machine              install for all users (needs Administrator)
#      -NoAutostart          do not register the logon task
#      -NoFirewall           do not touch the firewall
#      -NoTls                skip certificate generation
#      -Source <dir>         install from a different directory
#
#  Nothing here is destructive. Running it twice is safe: every step checks the
#  current state first and reports what it found.
# =============================================================================

[CmdletBinding()]
param(
    [int]$Port = 7788,
    [int]$HttpsPort = 7443,
    [switch]$Machine,
    [switch]$NoAutostart,
    [switch]$NoFirewall,
    [switch]$NoTls,
    [string]$Source
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ------------------------------------------------------------------ helpers
function Say  { param([string]$Text, [string]$Colour = 'Gray')
                Write-Host "  $Text" -ForegroundColor $Colour }
function Head { param([string]$Text)
                Write-Host ""
                Write-Host "  $Text" -ForegroundColor Cyan
                Write-Host ("  " + ('-' * $Text.Length)) -ForegroundColor DarkGray }
function Step { param([string]$Text) Write-Host "  $Text" -ForegroundColor White }
function Ok   { param([string]$Text) Write-Host "  [ok]   $Text" -ForegroundColor Green }
function Warn { param([string]$Text) Write-Host "  [warn] $Text" -ForegroundColor Yellow }
function Bad  { param([string]$Text) Write-Host "  [fail] $Text" -ForegroundColor Red }
function Skip { param([string]$Text) Write-Host "  [--]   $Text" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --------------------------------------------------------------- locate source
$source = if ($Source) { (Resolve-Path $Source).Path }
          else { Split-Path -Parent (Split-Path -Parent $PSCommandPath) }

if (-not (Test-Path (Join-Path $source 'pbrforge\__main__.py'))) {
    Bad "No pbrforge package found in $source"
    Say "  Run this from the repository root, or pass -Source <dir>."
    exit 1
}

$isAdmin = Test-Admin

# --------------------------------------------------------------- destination
$version = (& python -c "import sys; sys.path.insert(0, r'$source'); import pbrforge; print(pbrforge.__version__)" 2>$null)
if (-not $version) { $version = (Get-Date -Format 'yyyyMMdd-HHmm') }
$version = $version.Trim()

$baseDir = if ($Machine) {
    if (-not $isAdmin) {
        Bad "-Machine installs to $env:ProgramFiles and needs Administrator."
        Say "  Re-run from an elevated PowerShell, or drop -Machine for a per-user install."
        exit 1
    }
    Join-Path $env:ProgramFiles 'PBRForge'
} else {
    Join-Path $env:LOCALAPPDATA 'Programs\PBRForge'
}

$versionDir = Join-Path $baseDir "versions\$version"
$currentDir = Join-Path $baseDir 'current'

Write-Host ""
Write-Host "  PBRForge - server install" -ForegroundColor Cyan
Write-Host "  =========================" -ForegroundColor Cyan
Say "source       $source"
Say "version      $version"
Say "destination  $versionDir"
Say "scope        $(if ($Machine) { 'all users' } else { 'current user only' })"
Say "ports        TCP $Port (http)  TCP $HttpsPort (https)  UDP 7789 (discovery)"

if (-not $isAdmin) {
    Write-Host ""
    Warn "Not running as Administrator."
    Say "  The install works, but the firewall step will need a separate"
    Say "  right-click. deploy\allow-firewall.cmd asks for elevation itself."
}

# ------------------------------------------------------------------- 1. copy
Head "1. Install files"
if (Test-Path $versionDir) {
    Skip "already present: $versionDir  (reusing it)"
} else {
    New-Item -ItemType Directory -Force -Path $versionDir | Out-Null
    foreach ($item in @('pbrforge', 'ui', 'docs', 'deploy')) {
        $from = Join-Path $source $item
        if (Test-Path $from) {
            Copy-Item -Recurse -Force $from (Join-Path $versionDir $item)
        }
    }
    foreach ($file in @('README.md','INSTALLATION.md','DEPLOYMENT.md','CHANGELOG.md',
                        'LICENSE.txt','REQUIREMENTS.txt')) {
        $from = Join-Path $source $file
        if (Test-Path $from) { Copy-Item -Force $from $versionDir }
    }
    # __pycache__ inside the copied tree is stale bytecode against a different
    # file layout, and it produces import errors that make no sense.
    Get-ChildItem -Recurse -Directory -Filter '__pycache__' -Path $versionDir -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Ok "copied to $versionDir"
}

# The `current` junction is what makes upgrades atomic: the launcher and the
# shortcut always point at `current`, so switching versions is one reparse-point
# update, with no window where the install is half-replaced.
if (Test-Path $currentDir) {
    $existing = Get-Item $currentDir -Force
    if ($existing.LinkType -eq 'Junction' -or $existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Skip "current junction already exists"
    }
}
if (-not (Test-Path $currentDir)) {
    New-Item -ItemType Junction -Path $currentDir -Target $versionDir | Out-Null
    Ok "current -> versions\$version"
} else {
    # Re-point only if it does not already resolve to this version.
    $target = (Get-Item $currentDir -Force).Target
    if ($target -and ($target.TrimEnd('\') -ne $versionDir.TrimEnd('\'))) {
        Remove-Item $currentDir -Force
        New-Item -ItemType Junction -Path $currentDir -Target $versionDir | Out-Null
        Ok "current -> versions\$version  (re-pointed)"
    } else {
        Ok "current -> versions\$version"
    }
}

# ------------------------------------------------------------------ 2. config
Head "2. Configuration"
# Config, the pairing secret and the TLS material live here, NOT in the install
# directory. That is the whole reason an upgrade does not un-pair every client:
# replacing versions\ swaps the code and leaves identity untouched.
$configDir = Join-Path $env:APPDATA 'pbrforge'
$configFile = Join-Path $configDir 'config.toml'
if (Test-Path $configFile) {
    Skip "config already exists: $configFile  (left untouched)"
} else {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    $library = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'PBRForge\library'
    @"
# PBRForge configuration -- written by deploy\install-server.ps1
# PBRFORGE_* environment variables override every value here.

[server]
bind          = "0.0.0.0"
port          = $Port
https_port    = $HttpsPort
tls           = $(if ($NoTls) { 'false' } else { 'true' })
auth          = true
discovery     = true
open_browser  = false
max_upload_mb = 512

[paths]
library = "$($library -replace '\\','\\')"
cache   = ""
temp    = ""

[defaults]
preset        = "generic-pbr"
resolution    = 4096
deterministic = true
ai_enabled    = false

[ui]
theme  = "dark"
accent = "amber"
"@ | Set-Content -Path $configFile -Encoding UTF8
    Ok "wrote $configFile"
    Ok "library will be $library"
}

# Verify the config actually parses. A typo here produces a server that starts
# with silently default settings, which is the worst kind of wrong.
# Single-line deliberately: passing a multi-line here-string as a native command
# argument works in most cases and fails in enough of them that it is not worth
# the elegance.
$check = & python -c "import sys; sys.path.insert(0, r'$versionDir'); from pbrforge.config import Config; c = Config.load(); print(c.serve_port, c.bind, c.auth, c.tls)" 2>&1
if ($LASTEXITCODE -eq 0) { Ok "config parses: serve_port/bind/auth/tls = $check" }
else { Bad "config did not parse: $check"; exit 1 }

# ----------------------------------------------------------------- 3. firewall
Head "3. Firewall"
if ($NoFirewall) {
    Skip "skipped (-NoFirewall)"
} elseif (-not $isAdmin) {
    Warn "Needs Administrator. Run this once, elevated:"
    Say "  $(Join-Path $versionDir 'deploy\allow-firewall.cmd')"
} else {
    foreach ($rule in @(@{n="PBRForge TCP $Port"; p='TCP'; l=$Port},
                        @{n="PBRForge TCP $HttpsPort"; p='TCP'; l=$HttpsPort},
                        @{n="PBRForge UDP 7789"; p='UDP'; l=7789})) {
        $existing = netsh advfirewall firewall show rule name="$($rule.n)" 2>&1 | Out-String
        if ($existing -notmatch 'No rules match') {
            Skip "rule already present: $($rule.n)"
        } else {
            netsh advfirewall firewall add rule name="$($rule.n)" dir=in action=allow `
                protocol=$($rule.p) localport=$($rule.l) profile=private | Out-Null
            if ($LASTEXITCODE -eq 0) { Ok "added rule: $($rule.n)" }
            else { Warn "could not add rule: $($rule.n)" }
        }
    }
}

# ---------------------------------------------------------------------- 4. TLS
Head "4. Certificate (HTTPS)"
if ($NoTls) {
    Skip "skipped (-NoTls). LAN clients fall back to WebGL2 and lose WebGPU."
} else {
    $hasCrypto = & python -c "import cryptography; print(cryptography.__version__)" 2>$null
    if (-not $hasCrypto) {
        Warn "The 'cryptography' package is not installed, so no certificate was made."
        Say "  Without HTTPS, browsers on other PCs refuse WebGPU because a LAN"
        Say "  address is not a secure origin. Everything else still works."
        Say "  To fix:  python -m pip install cryptography"
        Say "           python -m pbrforge tls init"
    } else {
        Push-Location $versionDir
        try {
            $out = & python -m pbrforge tls init 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) {
                Ok "certificate ready (cryptography $hasCrypto)"
                $out -split "`n" | Where-Object { $_ -match 'Covers|Expires' } |
                    ForEach-Object { Say $_.Trim() }
            } else {
                Warn "tls init failed:"; Say $out
            }
        } finally { Pop-Location }
    }
}

# ---------------------------------------------------------------- 5. autostart
Head "5. Start automatically"
if ($NoAutostart) {
    Skip "skipped (-NoAutostart)"
} else {
    $taskName = 'PBRForge Server'
    $pythonExe = (Get-Command python).Source
    $action = New-ScheduledTaskAction -Execute $pythonExe `
        -Argument "-m pbrforge serve" -WorkingDirectory $currentDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Skip "task already registered: $taskName  (leaving it alone)"
    } else {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description "Runs the PBRForge texture server so other PCs can connect after a reboot." | Out-Null
        Ok "registered logon task: $taskName"
        Say "  A task rather than a Windows service, deliberately: a service runs in"
        Say "  session 0 with no console, so the URLs and the pairing code -- the two"
        Say "  things you actually need -- would be invisible and undebuggable."
        Say "  Start it now:  Start-ScheduledTask -TaskName '$taskName'"
    }
}

# ------------------------------------------------------------------- 6. verify
Head "6. Verify"
Push-Location $currentDir
try {
    $env:PBRFORGE_PORT = "$Port"
    $out = & python -m pbrforge doctor 2>&1 | Out-String
    Write-Host $out
} finally {
    Pop-Location
    Remove-Item Env:\PBRFORGE_PORT -ErrorAction SilentlyContinue
}

# -------------------------------------------------------------------- summary
Head "Next"
$launcher = Join-Path $currentDir 'start-server.cmd'
@"
@echo off
REM Start the PBRForge server. The console shows the URL and pairing code.
title PBRForge server
cd /d "%~dp0"
python -m pbrforge serve %*
if errorlevel 1 pause
"@ | Set-Content -Path $launcher -Encoding ASCII
Ok "wrote $launcher"

# The matching stop script. Without it the only way to shut the server down is to
# find the right console window and press Ctrl+C -- and a server started by the
# logon task has no console window at all.
$stopper = Join-Path $currentDir 'stop-server.cmd'
@"
@echo off
REM Stop the PBRForge server started by start-server.cmd or the logon task.
title PBRForge stop
cd /d "%~dp0"
python -m pbrforge stop
if errorlevel 1 pause
"@ | Set-Content -Path $stopper -Encoding ASCII
Ok "wrote $stopper"

Write-Host ""
Write-Host "  1. Start it:      $launcher" -ForegroundColor White
Write-Host "  2. Read the URL and the pairing code from its console." -ForegroundColor White
Write-Host "  3. On another PC, open that URL and enter the code once." -ForegroundColor White
Write-Host "  4. Stop it:       $stopper" -ForegroundColor White
Write-Host ""
Write-Host "  Ticking 'Start automatically' in step 5 means this survives a reboot." -ForegroundColor DarkGray
Write-Host "  To remove everything: delete $baseDir" -ForegroundColor DarkGray
Write-Host "  and the scheduled task, and the rules named 'PBRForge *'." -ForegroundColor DarkGray
Write-Host ""
