# 🛠️ Installing PBRForge

**0.2.0 status:** two supported routes. The Windows installer is built, published and exercised on
Windows 11. Source installation remains fully supported on Windows, macOS and Linux. The installer
is unsigned, so SmartScreen needs one explicit **Run anyway** click.

---

## 🪟 Windows installer (recommended on Windows)

Download **`PBRForge-Setup-0.2.0.exe`** from the
[latest release](https://github.com/learnerarchitecture-debug/pbrforge/releases/latest), run it, and
follow the wizard. It installs per‑user in `%LOCALAPPDATA%\Programs\PBRForge` without Administrator.

| Decision | Default | What it means |
|---|---|---|
| Desktop and Start Menu shortcuts | on | Start PBRForge, **Doctor**, firewall helper and uninstaller |
| Local HTTPS certificate | on | Generates a local CA/leaf in `%APPDATA%\pbrforge\tls` for LAN WebGPU |
| Windows firewall rule | off | An Administrator prompt is needed; the Start Menu has a helper |
| Autostart at logon | off | Registers a per‑user scheduled task named **PBRForge Server** |

The config and library live **outside** the install directory, so upgrades and uninstalls do not
overwrite them. Uninstall asks whether to delete them; the default is to keep both.

The installer does **not** modify `PATH`. Use the Start Menu shortcuts, or invoke the executable
directly:

```powershell
$pbrforge = "$env:LOCALAPPDATA\Programs\PBRForge\pbrforge.exe"
& $pbrforge doctor
& $pbrforge selftest
& $pbrforge serve
```

To verify the download manually, fetch `SHA256SUMS.txt` from the same release and compare:

```powershell
Get-FileHash .\PBRForge-Setup-0.2.0.exe -Algorithm SHA256
```

What the 0.2.0 build was tested against: silent fresh install, server start/ping/stop, packaged
autostart registration/removal, reinstall over a running server, and silent uninstall with config
preservation. It has not been tested on a second machine.

---

## 1. What you need (source route)

| Requirement | Why |
|---|---|
| **Python 3.12+** | `tomllib`, modern typing syntax and the tested runtime |
| **A local checkout** | No pip package is published; run from the repository |
| **A browser** | Chrome/Edge/Firefox; the UI is dependency-free |
| **Windows 10/11** | The tested platform in 0.2.0; the stdlib server is portable |

The package itself uses **only the Python standard library**. No pip install is needed to decode a
PNG, run the 15 stages, serve the UI or pack a material. `doctor` reports optional libraries if you
have them, but they are not required.

---

## 2. Get the code

Keep the checkout **outside OneDrive / Dropbox / a synced Documents folder**. Sync churn and file
locks during a 4k job are a real source of permission errors.

```powershell
cd C:\dev
git clone https://github.com/learnerarchitecture-debug/pbrforge.git
cd pbrforge
```

If you already use Microsoft Store Python, it works, but `doctor` prints a note. The python.org
installer is the less surprising choice on Windows.

---

## 3. Verify the checkout

```powershell
python -m pbrforge doctor
python -m pbrforge selftest
```

Expected:

- `doctor` ends with a clear **ready to run** line;
- `selftest` reports **18/18**.

The self-test covers config round-trip, auth, PNG read/write and refusals, cache locking, lifecycle,
derived maps, tileability, colour-space gates, resampling, the server routes, the pack layout and the
seam metric fixtures. It does not download anything.

If `python` is not found, install Python 3.12 from python.org and enable **Add python.exe to PATH**.
There is no `py` launcher requirement in this project.

---

## 4. Start it

```powershell
python -m pbrforge serve
```

The server prints the URL and, when pairing is enabled, a code. Open the URL and enter the code once.

```text
PBRForge 0.2.0
UI       http://127.0.0.1:7788
code     ABCD-EFGH
```

Then use the Studio:

1. drop a photo;
2. choose category/resolution;
3. press **Forge material**;
4. inspect the QC verdict and maps;
5. download the zip.

The default library is:

```text
C:\Users\<you>\Documents\PBRForge\library
```

Stop it from any terminal with:

```powershell
python -m pbrforge stop
```

Do not kill the bootloader/PID that merely looks like the server; `stop` verifies which process owns
the socket.

---

## 5. Configuration

```powershell
python -m pbrforge config
python -m pbrforge config --paths
python -m pbrforge config --set port=7788 --set update_check=false
```

`config.toml` lives under the config directory (`%APPDATA%\pbrforge` by default on Windows). The
canonical writer replaces the whole file, so comments are lost when you use `--set`; hand-edit it if
you want to keep comments.

For an isolated test:

```powershell
python -m pbrforge serve --config-dir C:\temp\pbrforge-test --no-auth
```

| Setting | Default | Note |
|---|---:|---|
| `server.bind` | `0.0.0.0` | Use `127.0.0.1` for this machine only |
| `server.port` | `7788` | HTTP |
| `server.auth` | `true` | Pairing code + cookie |
| `server.max_upload_mb` | `512` | Hard cap |
| `paths.library` | `Documents\PBRForge\library` | Finished materials |
| `defaults.resolution` | `4096` | Non-job default; UI/CLI jobs default to 1024 |
| `updates.check` | `true` | Allows an explicit check |
| `updates.auto_install` | `false` | Never install silently by default |

---

## 6. Photo formats

The **web app** converts anything the browser can decode (PNG, JPEG, WebP, AVIF, sometimes HEIC) to
PNG before upload. The **Python pipeline and CLI** accept **PNG only**.

This is deliberate. Refusing a non-PNG with a named fix is safer than a decoder that silently
misreads a format. If you need a JPEG in the CLI, convert it first with any image editor or the
browser UI.

---

## 7. Optional acceleration

There is **no required acceleration layer** in 0.2.0. The classical stages run on the standard
library. If you install optional numeric libraries, `doctor` will report them; do not assume the
pipeline automatically switches to them. The current job manager runs one worker to keep memory
predictable.

```powershell
# Optional, for TLS on a LAN
python -m pip install cryptography
```

TLS is only needed when browsers require a secure context for a LAN address. Local `127.0.0.1` does
not.

---

## 8. Updates

```powershell
python -m pbrforge update --check
```

The 0.2.0 GitHub Release is published, so this build reports:

```text
current  0.2.0
latest   0.2.0
status   up to date
```

A newer release must contain an asset named exactly `PBRForge-Setup-<version>.exe`. PBRForge verifies
its size and, when GitHub supplies a digest, its SHA‑256 before offering to launch it. There is no
background poller and `auto_install` is off by default. See [docs/UPDATES.md](docs/UPDATES.md).

---

## 9. Installer / packaging status

`installer/pbrforge.iss` plus `deploy/build-installer.ps1` produced
`PBRForge-Setup-0.2.0.exe` with Inno Setup 6.7.3. The build was exercised end to end:

| Check | Result |
|---|---|
| Portable one‑file executable | built; packaged `selftest` passed |
| Installer compile and SHA‑256 | built; checksum written to `dist\SHA256SUMS.txt` |
| Silent fresh install, `/CURRENTUSER` | exit 0; config/library outside install |
| Start → ping → `stop` | server answered and the port was released |
| Package‑aware autostart | task pointed at `pbrforge.exe serve`; removed cleanly |
| Reinstall while the server was running | installer stopped the server, replaced the exe, released the port |
| Silent uninstall | install dir, shortcuts and uninstall key removed; user config preserved |

The installer is not code‑signed. The install/upgrade/uninstall cycle was run on one Windows 11
machine, not a second PC. A version‑to‑version update has not been exercised because 0.2.0 is the
newest release.

---

## 10. Common problems

| Symptom | Cause / fix |
|---|---|
| SmartScreen blocks the installer | It is unsigned. **More info → Run anyway**, or verify `SHA256SUMS.txt` first |
| `pbrforge` is not recognised after install | The installer does not edit `PATH`; use the Start Menu or the full path under `%LOCALAPPDATA%\Programs\PBRForge` |
| `python` opens the Microsoft Store | Store alias; install python.org Python and reopen the terminal |
| `doctor` says port busy | `python -m pbrforge stop`, then start again |
| 401 / blank page | Open `/pair?next=/` and enter the code from the server console |
| Upload refused | The browser could not decode it; convert to a real PNG first |
| Result looks too dark | Auto-levels was on; regenerate with it off |
| Brick mortar is raised | Choose category **Brick** or height **invert** |
| Job fails, library unchanged | Correct: failed jobs stay in `cache/staging/<id>` for inspection |
| `PermissionError` under OneDrive | Move the checkout and library outside the synced folder |

More: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
