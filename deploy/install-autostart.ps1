# =============================================================================
#  install-autostart.ps1 -- make the server come back after a reboot
#
#  Split out from install-server.ps1 because it is the one step you may want to
#  undo independently. The server is often started by hand for a while first, and
#  then promoted to "always on" once the library is worth sharing.
#
#      powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1
#      powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1 -Remove
#      powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1 -Status
#
#  WHY A SCHEDULED TASK AND NOT A WINDOWS SERVICE
#
#  The obvious move is a service. It is the wrong move here, and the reason is
#  the console.
#
#  A Windows service runs in session 0, isolated from the desktop, with no
#  console attached. PBRForge prints two things to its console that the user
#  genuinely needs and cannot get anywhere else:
#
#       the LAN URL to open on the other PC
#       the pairing code to type there
#
#  As a service, that output goes nowhere. The server would be running, healthy,
#  and completely inaccessible in practice -- you would be left reading the
#  pairing code out of a log file that nobody told you about, and "run the server"
#  would stop being something you can do by double-clicking a file.
#
#  A logon task runs as the logged-in user, in the interactive session, with a
#  real console. It survives reboots, which is the actual requirement. It also
#  gets the user's own environment, so the library path and the SMB mapping to a
#  NAS are the same ones the user sees.
#
#  The genuine trade-off, stated plainly: a logon task does not start until
#  somebody logs in, and it is not restarted by the Service Control Manager. If
#  the machine is a headless always-on box with no interactive login, use the
#  Task Scheduler option "Run whether user is logged on or not" (which needs the
#  account password) or the Docker path in DEPLOYMENT.md, not this script.
# =============================================================================

[CmdletBinding()]
param(
    [string]$TaskName = 'PBRForge Server',
    [int]$Port = 7788,
    [string]$InstallDir,
    [switch]$Remove,
    [switch]$Status,
    [switch]$AtStartup
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Ok   { param([string]$t) Write-Host "  [ok]   $t" -ForegroundColor Green }
function Skip { param([string]$t) Write-Host "  [--]   $t" -ForegroundColor DarkGray }
function Bad  { param([string]$t) Write-Host "  [fail] $t" -ForegroundColor Red }
function Info { param([string]$t) Write-Host "  $t" -ForegroundColor Gray }

Write-Host ""
Write-Host "  PBRForge - autostart" -ForegroundColor Cyan
Write-Host "  ====================" -ForegroundColor Cyan

# ------------------------------------------------------------------- -Status
if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Info "not registered: $TaskName"; exit 0 }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Info "task       $TaskName"
    Info "state      $($task.State)"
    Info "runs       $($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
    Info "in         $($task.Actions[0].WorkingDirectory)"
    Info "last run   $($info.LastRunTime)  (result $($info.LastTaskResult))"
    Info "next run   $($info.NextRunTime)"
    exit 0
}

# ------------------------------------------------------------------- -Remove
if ($Remove) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Skip "nothing to remove: $TaskName"; exit 0 }

    # Stop-ScheduledTask ends the task's process tree, which does stop the server --
    # but it knows nothing about server.pid, so the record is left behind claiming a
    # PID that may already be recycled. Running the app's own stop first clears it
    # and gives the same guard (refuse unless the PID is confirmed) as everywhere
    # else. Then Stop-ScheduledTask is the belt to that braces.
    $installedExe = Join-Path (Split-Path -Parent $PSScriptRoot) 'pbrforge.exe'
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    try {
        if (Test-Path $installedExe) {
            $out = & $installedExe stop 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) { Info "pbrforge stop: $($out.Trim())" }
        } elseif ($python) {
            Push-Location (Split-Path -Parent $PSScriptRoot)
            try {
                $out = & $python -m pbrforge stop 2>&1 | Out-String
                if ($LASTEXITCODE -eq 0) { Info "pbrforge stop: $($out.Trim())" }
            } finally { Pop-Location }
        }
    } catch {
        Info "pbrforge stop was not available: $_"
    }

    if ($task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName
        Info "stopped the running instance"
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Ok "removed: $TaskName"
    Info "The server will no longer start automatically. The install and your"
    Info "library are untouched."
    exit 0
}

# ------------------------------------------------------------------- install
$dir = if ($InstallDir) { $InstallDir }
       else { Join-Path $env:LOCALAPPDATA 'Programs\PBRForge\current' }

if (-not (Test-Path $dir)) {
    Bad "no install found at $dir"
    Info "Run deploy\install-server.ps1 first, or pass -InstallDir <dir>."
    exit 1
}
Info "install    $dir"

# A packaged install has pbrforge.exe and no Python package; a source install
# has pbrforge\__main__.py and needs Python. Detect which one is in front of us
# rather than assuming the source-tree layout inside an installer.
$installedExe = Join-Path $dir 'pbrforge.exe'
if (Test-Path $installedExe) {
    $runExe = $installedExe
    $runArgs = "serve --port $Port"
    Info "mode       packaged"
} else {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) {
        Bad "python is not on PATH and there is no pbrforge.exe in $dir."
        Info "A scheduled task inherits no shell profile, so an aliased or"
        Info "profile-defined python will not be found when the task fires."
        exit 1
    }
    if (-not (Test-Path (Join-Path $dir 'pbrforge\__main__.py'))) {
        Bad "$dir contains neither pbrforge.exe nor the pbrforge package."
        exit 1
    }
    $runExe = $python
    $runArgs = "-m pbrforge serve --port $Port"
    Info "mode       source"
    Info "python     $python"
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Skip "already registered: $TaskName"
    Info "Use -Remove first if you want it rebuilt."
    exit 0
}

$action = New-ScheduledTaskAction -Execute $runExe `
    -Argument $runArgs `
    -WorkingDirectory $dir

# AtLogOn is the default because it is the one that keeps the console visible.
# -AtStartup exists for a headless box, with the caveat above.
$trigger = if ($AtStartup) { New-ScheduledTaskTrigger -AtStartup }
           else { New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME }

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew      # two servers on one port is a confusing failure

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description "Runs the PBRForge texture server so other PCs on the LAN can connect. The console shows the URL and the pairing code." | Out-Null
} catch {
    Bad "could not register the task: $($_.Exception.Message)"
    Info "Registering a task for the current user should not need elevation."
    Info "If this machine is domain-managed, group policy may forbid it; start"
    Info "the server from start-server.cmd instead."
    exit 1
}

Ok "registered: $TaskName"
Info "trigger    $(if ($AtStartup) { 'at system startup' } else { 'at logon' })"
Info "action     $runExe $runArgs"
Info "in         $dir"

Write-Host ""
Write-Host "  Test it now:   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host "  Check it:      .\install-autostart.ps1 -Status" -ForegroundColor White
Write-Host "  Undo it:       .\install-autostart.ps1 -Remove" -ForegroundColor White
Write-Host ""
