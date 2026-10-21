; =============================================================================
;  pbrforge.iss -- Inno Setup script for PBRForge-Setup-<version>.exe
;
;  Build with:  deploy\build-installer.ps1
;  Or directly: iscc installer\pbrforge.iss /DAppVersion=0.2.0
;
;  Design decisions, in the order they matter:
;
;  1. PER-USER BY DEFAULT. The installer runs without elevation, which means it
;     works on a locked-down work laptop, and it means the install lands in
;     %LOCALAPPDATA%\Programs, which is writable. A user can tick a box to get an
;     all-users install instead; `PrivilegesRequiredOverridesAllowed=dialog` is
;     what makes that choice available rather than deciding it for them.
;
;  2. THE LIBRARY AND THE CONFIG ARE NEVER TOUCHED. Not by the install, and not
;     by the uninstall. They live in %APPDATA%\pbrforge and <Documents>\PBRForge,
;     and they contain the user's textures, the pairing secret and the TLS
;     material. An installer that deletes a texture library during an upgrade is
;     unforgivable, and this one structurally cannot: it does not know those
;     paths, and the uninstall section deliberately omits them.
;
;  3. UPGRADES ARE IN-PLACE AND SILENT. Same AppId on every version, so Inno
;     treats a new build as an upgrade rather than a second product. The config
;     survives, which means paired clients stay paired across an upgrade.
;
;  4. THE FIREWALL AND THE AUTOSTART TASK ARE OPT-IN TASKS. They change machine
;     state outside the install directory, so they are checkboxes, not silent
;     side effects. Both are also available later as scripts in deploy\.
; =============================================================================

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef SourceRoot
  ; Default assumes the script sits in <root>\installer\
  #define SourceRoot ".."
#endif

#define AppName        "PBRForge"
#define AppPublisher   "learnerarchitecture-debug"
#define AppURL         "https://github.com/learnerarchitecture-debug/pbrforge"
#define AppExeName     "pbrforge.exe"
#define AppId          "{{8F3C1D42-6B7A-4E19-9A55-2C4E7B0D6A31}"
#define MinPython      "3.11"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; Per-user by default; the user may choose an all-users install in the dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes

; The portable exe is a single file, so the payload is small and compression is
; quick. lzma2/max is worth the extra few seconds on a ~12 MB payload.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}

WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile={#SourceRoot}\LICENSE.txt
InfoBeforeFile={#SourceRoot}\INSTALLATION.md
UninstallDisplayName={#AppName} {#AppVersion}

; No icon file is shipped yet. Rather than reference a missing .ico and fail the
; build, the exe is asked for its own icon.
SetupIconFile=
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";                      GroupDescription: "Shortcuts:"
Name: "startmenu";    Description: "Create a &Start Menu entry";                      GroupDescription: "Shortcuts:"
Name: "firewall";     Description: "Open the Windows firewall so other PCs can connect (private networks only)"; GroupDescription: "Networking:"; Flags: unchecked
Name: "autostart";    Description: "Start the server automatically when I log in";     GroupDescription: "Networking:"; Flags: unchecked
Name: "tls";          Description: "Create a local HTTPS certificate so LAN previews keep WebGPU"; GroupDescription: "Networking:"

[Files]
; The single-file executable: the CLI, the server and the UI all live inside it.
Source: "{#SourceRoot}\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Documents sit next to the executable, readable without running anything.
Source: "{#SourceRoot}\README.md";       DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "{#SourceRoot}\INSTALLATION.md";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\DEPLOYMENT.md";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\CHANGELOG.md";     DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\LICENSE.txt";      DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\REQUIREMENTS.txt"; DestDir: "{app}"; Flags: ignoreversion

; The whole docs folder: this is the product's documentation, and the pipeline
; reference in it is the reason to install rather than just download.
Source: "{#SourceRoot}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs

; Deploy scripts, so a user who installs the packaged build can still do
; everything the source-tree install does. They are also the repair path when
; something on the network is wrong.
Source: "{#SourceRoot}\deploy\allow-firewall.cmd";        DestDir: "{app}\deploy"; Flags: ignoreversion
Source: "{#SourceRoot}\deploy\install-autostart.ps1";     DestDir: "{app}\deploy"; Flags: ignoreversion
Source: "{#SourceRoot}\deploy\make-client-shortcut.ps1";  DestDir: "{app}\deploy"; Flags: ignoreversion
Source: "{#SourceRoot}\deploy\install-server.ps1";        DestDir: "{app}\deploy"; Flags: ignoreversion
Source: "{#SourceRoot}\ui\prototype.html";                DestDir: "{app}\ui";     Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";                 Filename: "{app}\{#AppExeName}"; Parameters: "serve"; Comment: "Start the server and show the LAN address"; Tasks: startmenu
Name: "{group}\Doctor (check this PC)";     Filename: "{app}\{#AppExeName}"; Parameters: "doctor"; Comment: "Check Python, ports, TLS and the network";   Tasks: startmenu
Name: "{group}\Open the firewall for LAN access"; Filename: "{app}\deploy\allow-firewall.cmd"; Comment: "Asks for Administrator, opens ports 7788/7443/7789 on private networks"; Tasks: startmenu
Name: "{group}\Uninstall {#AppName}";       Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{autodesktop}\{#AppName}";           Filename: "{app}\{#AppExeName}"; Parameters: "serve"; Tasks: desktopicon

[Run]
; 1. TLS. On by default, because TLS here is load-bearing rather than decorative:
;    browsers grant a secure context -- WebGPU, clipboard, service workers -- only on
;    https:// or localhost, so without a certificate every LAN client silently falls
;    back to WebGL2 and the preview is just quietly worse.
;
;    `tls init` can still fail: it needs either the bundled `cryptography` module or an
;    openssl CLI, and a build made on a machine without cryptography landing on a PC
;    without Git has neither. That is allowed to fail, and NO FLAG IS NEEDED to make it
;    so -- Inno Setup does not check [Run] exit codes. Probed rather than assumed: a
;    [Run] entry returning 3 in a /VERYSILENT install produced setup exit code 0 and
;    installed normally. `ignoreerrors` is the flag that looks right here; it is not
;    valid in [Run] (only in [UninstallRun]) and the compiler rejects it.
;
;    The failure is discoverable where it matters: `doctor` names the missing backend,
;    and the UI's server badge reports that clients are not on a secure origin.
Filename: "{app}\{#AppExeName}"; Parameters: "tls init"; \
    StatusMsg: "Creating a local HTTPS certificate..."; \
    Flags: runhidden; Tasks: tls

; 2. Firewall. Only meaningful in an elevated (all-users) install; in a per-user
;    install the rule cannot be added without a UAC prompt, so a Start Menu entry
;    is provided instead and this step is skipped.
Filename: "{app}\deploy\allow-firewall.cmd"; \
    StatusMsg: "Opening the firewall for LAN access..."; \
    Flags: runhidden; Tasks: firewall; Check: IsAdminInstallMode

; 3. Autostart, via the same script the source-tree install uses, so there is
;    exactly one implementation of the scheduled task.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\install-autostart.ps1"" -InstallDir ""{app}"""; \
    StatusMsg: "Registering the logon task..."; \
    Flags: runhidden waituntilterminated; Tasks: autostart

; 4. The one thing the user actually wants: the server, running, with its console
;    showing the URL and the pairing code.
Filename: "{app}\{#AppExeName}"; Parameters: "serve"; \
    Description: "Start the PBRForge server now and show the address to open on other PCs"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop a running server FIRST, and this is not a nicety.
;
; `pbrforge.exe` is a PyInstaller --onefile build, which is two processes: a
; bootloader, and the child that actually runs the app and holds the port. The
; child keeps the executable open, so an uninstall that skips this step leaves an
; 8.6 MB pbrforge.exe behind in the install directory with no uninstaller left to
; remove it. Found on the first install/uninstall cycle ever run.
;
; This runs the app's own `stop` rather than taskkill, because the app is the one
; place that knows how to identify the process safely -- it refuses to terminate a
; PID it cannot confirm is PBRForge, which is what stops a stale server.pid naming
; a recycled PID from killing an unrelated program.
Filename: "{app}\{#AppExeName}"; Parameters: "stop"; \
    StatusMsg: "Stopping the PBRForge server..."; \
    Flags: runhidden waituntilterminated; RunOnceId: "StopServer"

; Then remove the logon task, so uninstalling does not leave a scheduled task
; pointing at a deleted executable -- which produces a failed task on every
; subsequent login and looks like malware.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""try {{ Stop-ScheduledTask -TaskName 'PBRForge Server' -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'PBRForge Server' -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RemoveAutostart"

Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-NetFirewallRule -DisplayName 'PBRForge*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewall"

[UninstallDelete]
; The config, the library and the certificates are deliberately absent from this
; section: see the header note. What is listed here is everything the installer
; created that Inno does not already track.
;
; install-mode.txt is written by the [Code] section, not by a [Files] entry, so
; the uninstaller has no record of it and leaves it behind -- a stray file in an
; otherwise empty folder, which reads as a failed uninstall.
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files;          Name: "{app}\install-mode.txt"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Exe: String;
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;

  Exe := ExpandConstant('{app}\{#AppExeName}');
  if not FileExists(Exe) then
    exit;  { first install: there is nothing that could be running }

  { An UPGRADE, and the server is probably still up -- it is designed to keep
    running. Two reasons this cannot be skipped:

      1. The running process holds the old pbrforge.exe open, so copying the new
         one over it fails, and Inno Setup's fallback is to schedule a replace on
         the next reboot. For a tool whose whole job is to be running, "upgraded"
         then silently meaning "upgraded after you reboot" is a bad outcome.
      2. A packaged build is TWO processes: the PyInstaller bootloader and the
         child that owns the port. Closing "pbrforge.exe" in Task Manager closes
         the parent and leaves the child holding the port, which is exactly how
         people conclude the upgrade worked while the old version is still serving.

    So this calls the one authoritative stop path, which identifies the process by
    the port it owns rather than by name. Failure is ignored on purpose: an install
    must never be blocked by a server it could not stop, and Inno Setup's own
    in-use handling remains as the backstop.
  }
  if not WizardSilent then
    WizardForm.StatusLabel.Caption := 'Stopping a running server...';

  if Exec(Exe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    if ResultCode = 0 then
      Sleep(700);  { let the listener close before the new build tries to bind it }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Write a marker so the installed app (and any future uninstall) can tell a
      packaged install from a source-tree one without guessing from the path. }
    DataDir := ExpandConstant('{app}');
    SaveStringToFile(DataDir + '\install-mode.txt',
      'packaged' + #13#10 +
      'version={#AppVersion}' + #13#10 +
      'installed=' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + #13#10,
      False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigDir, LibraryDir: String;
  Response: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Tell the user exactly what was kept, and where it is. An uninstaller that
      silently leaves a few hundred megabytes of textures behind is as annoying
      as one that deletes them. }
    ConfigDir := ExpandConstant('{userappdata}\pbrforge');
    LibraryDir := ExpandConstant('{userdocs}\PBRForge');

    if DirExists(ConfigDir) or DirExists(LibraryDir) then
    begin
      { SuppressibleMsgBox, NOT MsgBox, and the difference is load-bearing.
        Plain MsgBox ignores /SILENT and /VERYSILENT: Inno Setup still opens the
        dialog, so an unattended uninstall -- a script, a clean-image build, a
        remote management tool -- blocks on a modal window that nobody can
        answer. The process never exits and the "delete" question is never even
        answered, which reads as a hung uninstaller.

        The default passed here is IDNO, so a suppressed run keeps the data.
        That is the only safe default for a question about deleting a texture
        library: an automated uninstall must never destroy the user's files
        because it could not ask. }
      Response := SuppressibleMsgBox(
        'PBRForge has been removed.' + #13#10 + #13#10 +
        'Your configuration and texture library have been KEPT:' + #13#10 +
        '    ' + ConfigDir + #13#10 +
        '    ' + LibraryDir + #13#10 + #13#10 +
        'The library holds your images; the config holds the pairing secret and' + #13#10 +
        'the HTTPS certificate. Your textures are not touched by this uninstaller.' + #13#10 + #13#10 +
        'Delete those two folders yourself if you want everything gone.' + #13#10 + #13#10 +
        'Delete them now?',
        mbConfirmation, MB_YESNO, IDNO);
      if Response = IDYES then
      begin
        DelTree(LibraryDir, True, True, True);
        DelTree(ConfigDir, True, True, True);
      end;
    end;
  end;
end;
