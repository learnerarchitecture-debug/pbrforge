# =============================================================================
#  make-client-shortcut.ps1 -- run this ON a client PC
#
#  Makes a desktop shortcut that opens the server in a chromeless window, so the
#  other machines get something that behaves like an installed app rather than a
#  bookmark someone has to remember to visit.
#
#      powershell -ExecutionPolicy Bypass -File deploy\make-client-shortcut.ps1 `
#          -Server http://192.168.1.100:7788
#
#      -Server <url>     the server, as printed by `pbrforge url` on the host
#      -Name <string>    shortcut name            (default: PBRForge)
#      -AllUsers         put it on the public desktop and the Start menu
#      -Remove           delete it again
#
#  WHY --app RATHER THAN A PLAIN .url FILE
#
#  A .url shortcut opens a normal browser tab: tabs, address bar, other tabs'
#  titles, the whole context. That is fine for a website and wrong for a tool.
#  Edge and Chrome both support --app=<url>, which opens a single chromeless
#  window with its own taskbar entry and Alt-Tab identity. It reads as an
#  application, which is what it is.
#
#  The trade-off, stated: --app windows share the browser's profile, so the
#  pairing cookie is shared with the user's normal browsing. That is desirable
#  here -- pair once, stay paired -- but it does mean clearing site data in the
#  browser also unpairs PBRForge.
#
#  Note on HTTPS: if the server has a certificate, use the https:// URL. On a
#  plain http:// LAN address the browser withholds secure-context APIs (a LAN IP
#  is not a secure origin), though the WebGL preview still works. `pbrforge
#  doctor` on the host says which case applies, and `pbrforge tls trust`
#  produces the certificate to install here.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$Name = 'PBRForge',
    [switch]$AllUsers,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Ok   { param([string]$t) Write-Host "  [ok]   $t" -ForegroundColor Green }
function Skip { param([string]$t) Write-Host "  [--]   $t" -ForegroundColor DarkGray }
function Bad  { param([string]$t) Write-Host "  [fail] $t" -ForegroundColor Red }
function Info { param([string]$t) Write-Host "  $t" -ForegroundColor Gray }

$Server = $Server.TrimEnd('/')
if ($Server -notmatch '^https?://') {
    Bad "-Server must start with http:// or https://"
    Info "Example: -Server http://192.168.1.100:7788"
    exit 1
}

Write-Host ""
Write-Host "  PBRForge - client shortcut" -ForegroundColor Cyan
Write-Host "  ==========================" -ForegroundColor Cyan
Info "server     $Server"

$desktop = if ($AllUsers) { [Environment]::GetFolderPath('CommonDesktopDirectory') }
           else { [Environment]::GetFolderPath('Desktop') }
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) "$Name.lnk"
$linkPath = Join-Path $desktop "$Name.url"
$appLink = Join-Path $desktop "$Name.lnk"

if ($Remove) {
    foreach ($p in @($linkPath, $appLink, $startMenu)) {
        if (Test-Path $p) { Remove-Item $p -Force; Ok "removed $p" }
    }
    Info "The pairing cookie in your browser is untouched, so re-creating the"
    Info "shortcut will not ask you to pair again."
    exit 0
}

# --------------------------------------------------------------- reachability
# Check before writing a shortcut, so a typo is caught here rather than becoming
# "the shortcut is broken".
try {
    $r = Invoke-WebRequest -Uri "$Server/api/ping" -TimeoutSec 5 -UseBasicParsing
    $ping = $r.Content | ConvertFrom-Json
    Ok "reachable: $($ping.app) $($ping.version) on $($ping.host)"
} catch {
    Bad "no PBRForge server answered at $Server/api/ping"
    Info "$($_.Exception.Message)"
    Info ""
    Info "Things worth checking, in order:"
    Info "  1. Is the server running? Its console should be showing a URL."
    Info "  2. Is this the right address? Run 'pbrforge url' on the host."
    Info "  3. Can you ping the host from here?  Test-Connection <host>"
    Info "  4. Firewall on the HOST. Run deploy\allow-firewall.cmd there --"
    Info "     it is the single most common cause."
    Info "  5. A VPN or a guest network can put you on a different subnet."
    exit 1
}

# ------------------------------------------------------------------- browser
# Edge is checked first: it ships with Windows 10 and 11, so it is always there.
$chromium = $null
foreach ($candidate in @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
)) {
    if ($candidate -and (Test-Path $candidate)) { $chromium = $candidate; break }
}
# A Chromium browser can also be pinned in the registry under a non-standard path.
if (-not $chromium) {
    foreach ($key in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
    )) {
        $p = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).'(default)'
        if ($p -and (Test-Path $p)) { $chromium = $p; break }
    }
}

if ($chromium) {
    Info "browser    $chromium"

    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($appLink)
    $sc.TargetPath = $chromium
    # --app gives the chromeless window. --new-window is not used: if a window for
    # this URL already exists, focusing it is the correct behaviour, not a second copy.
    $sc.Arguments = "--app=$Server"
    $sc.Description = "PBRForge texture server ($Server)"
    $sc.WorkingDirectory = Split-Path $chromium -Parent
    $sc.Save()
    Ok "created $appLink"

    if (-not $AllUsers) {
        New-Item -ItemType Directory -Force -Path (Split-Path $startMenu -Parent) | Out-Null
        $sc.Save()
        Copy-Item $appLink $startMenu -Force
        Ok "created $startMenu"
    }
} else {
    # No Chromium browser. Fall back to a .url file, which opens in whatever the
    # default browser is. Still a working shortcut, just an ordinary tab.
    Info "browser    no Edge or Chrome found; falling back to a plain URL shortcut"
    @"
[InternetShortcut]
URL=$Server
IconFile=%SystemRoot%\system32\SHELL32.dll
IconIndex=14
"@ | Set-Content -Path $linkPath -Encoding ASCII
    Ok "created $linkPath"
}

# ------------------------------------------------------------------ trust TLS
if ($Server -like 'https://*') {
    Write-Host ""
    Info "The server uses HTTPS, so this PC must trust its certificate or the"
    Info "browser will show a warning before every session."
    Info ""
    Info "On the host:   pbrforge tls trust"
    Info "That writes a client-trust folder. Copy it here and run trust-ca.ps1"
    Info "from it (it asks for elevation, which installing a CA root needs)."
    Info ""
    Info "Without a certificate, use the http:// address instead. The preview"
    Info "drops to WebGL; nothing else changes."
}

Write-Host ""
Write-Host "  Done. Double-click '$Name' on the desktop." -ForegroundColor Green
Write-Host "  The first time, enter the pairing code shown in the server's console." -ForegroundColor DarkGray
Write-Host ""
