# PBRForge — Deployment Guide

How to get PBRForge running on one machine and usable from the others, on a local
network, without a cloud account and without a degree in PKI.

> **0.2.0 status:** the Windows installer and source deployment are both supported. The installer
> was exercised for fresh install, in-place reinstall over a running server, packaged autostart and
> uninstall on Windows 11. Docker and systemd remain documented design routes, not part of that
> test pass. See section 1.1 and [INSTALLATION.md](INSTALLATION.md).

This document is the operational half of the project. `README.md` says what
PBRForge is, `INSTALLATION.md` covers a single developer machine, and this file
covers the case the whole server exists for: **your workstation does the work,
every other PC on the LAN just opens a URL.**

---

## 1. Choose a model

Three supported shapes. Pick one; do not blend them, because the failure modes
are different and mixing them makes diagnosis guesswork.

| | **A. Thin server** ⭐ | **B. Per-PC install** | **C. Container on a NAS** |
|---|---|---|---|
| **Runs the server on** | one always-on PC | every PC separately | the NAS / a Linux box |
| **Other PCs need** | a browser | Python + the package | a browser |
| **Library lives** | on the server, local disk | on the NAS / SMB share | on the NAS, bind-mounted |
| **Setup effort** | ~15 min once | ~15 min × N machines | ~30 min once |
| **Survives the workstation sleeping** | no | yes | yes |
| **Library speed** | local disk, fast | SMB, slower | local to the NAS |
| **Heavy renders offloadable** | no | no | yes, if the NAS is strong |
| **Good for** | 2–5 PCs, one of which is usually on | one person, several machines, no always-on box | a NAS that is already always on |
| **Documented in** | §4, §5 | §4 with `--no-auth`, §12 | §10 |

**Recommendation: model A.** It is the only one where the expensive part (the
pipeline, the cache, the library index) exists in exactly one place. Model B has
you maintaining N Python installs and N caches, and the caches never agree.
Model C is better than A *if* the NAS is genuinely always on and you do not mind
the container caveats in §10.4.

### 1.1 What "serving" means in 0.2.0

The server, web app, job manager and the full 15-stage pipeline are implemented and exercised from
source. A LAN client can upload a photo, watch the job, inspect and download the resulting material.
Pairing, HTTPS, discovery and `doctor`/`selftest` are present.

The **Windows installer was exercised on Windows 11 for the 0.2.0 build**: silent fresh install,
start/ping/stop, packaged autostart, reinstall while the server was running, and uninstall with config
preservation. It has not been run on a second machine, and the Docker/systemd routes were not part of
this pass. Source deployment (this document's paths, config, library and cache split) remains fully
supported. The implementation status is tracked in `README.md` and `CHANGELOG.md`; when this file and
those disagree, those win.

---

## 2. What goes where

The single most important design decision in this deployment, and the reason
upgrades are safe:

> **Code goes in an install directory. Identity goes in a config directory. Data
> goes in a library. They are three different places and they are never the same
> place.**

| Item | Default path (Windows) | Default path (Linux) | Contains | Safe to delete? |
|---|---|---|---|---|
| **Install (packaged)** | `%LOCALAPPDATA%\Programs\PBRForge\` | `/opt/pbrforge` | `pbrforge.exe`, docs, deploy scripts | yes — reinstall |
| **Install (source)** | wherever you cloned | wherever you cloned | the package | yes — re-clone |
| **Config** | `%APPDATA%\pbrforge\` | `~/.config/pbrforge` | `config.toml`, `secret.key`, `tls/` | **no** — see §2.1 |
| **Library** | `Documents\PBRForge\library` | `~/PBRForge/library` | your textures and materials | **no** |
| **Cache** | `<config>\cache` | `~/.config/pbrforge/cache` | derived maps, content-addressed | yes, freely |
| **Temp** | `%TEMP%\pbrforge` | `/tmp/pbrforge` | in-flight job scratch | yes, when idle |
| **Client trust** | `<config>\tls\client-trust\` | same | `ca.pem`, `trust-ca.ps1`, `READ-ME.txt` | regenerable |

Override any of them with `PBRFORGE_*` environment variables, which take
precedence over `config.toml`:

| Variable | Meaning |
|---|---|
| `PBRFORGE_CONFIG_DIR` | the whole config directory |
| `PBRFORGE_LIBRARY` | library root |
| `PBRFORGE_CACHE` | cache root |
| `PBRFORGE_TEMP` | scratch root |
| `PBRFORGE_BIND` | bind address (`0.0.0.0` = reachable on the LAN) |
| `PBRFORGE_PORT` / `PBRFORGE_HTTPS_PORT` | ports |
| `PBRFORGE_AUTH` | `1`/`0` — pairing required |
| `PBRFORGE_TLS` | `1`/`0` — HTTPS |
| `PBRFORGE_NAME` | the display name clients see |

### 2.1 The three things in `config\` and what losing them costs

| File | What it is | If you lose it |
|---|---|---|
| `secret.key` | the HMAC key that signs session cookies **and** derives the pairing code | every paired client is logged out; the pairing code changes; nothing else breaks |
| `tls\ca.pem`, `tls\ca.key` | your local certificate authority | every client stops trusting the server and shows a warning until you redeploy a new CA |
| `tls\server.pem`, `tls\server.key` | the leaf certificate for this machine's addresses | same as above; regenerate with `pbrforge tls init --force` |
| `config.toml` | your settings | recreated with defaults — so ports and paths silently change back |

This is why the installer never touches `config\` and why the container section
makes such a fuss about the volume. Deleting `config\` is not a disaster — it is
a re-pair — but doing it by accident on an upgrade, which is what happens when
config lives inside the install directory, is a bug report waiting to be filed.

---

## 3. Ports

| Port | Proto | Purpose | Must be open for |
|---|---|---|---|
| **7788** | TCP | HTTP — the app and the API | client PCs |
| **7443** | TCP | HTTPS — same, plus WebGPU on clients | client PCs |
| **7789** | UDP | discovery beacon: `pbrforge discover` | optional |

Rules created by `deploy\allow-firewall.cmd` are named `PBRForge TCP 7788`,
`PBRForge TCP 7443`, `PBRForge UDP 7789`, and are scoped to the **private**
profile. They are not opened on public networks, deliberately: a laptop that
joins a café Wi-Fi does not silently start serving its texture library.

`serve --span 8` walks forward from the requested port if it is taken, so a
running dev server on 7788 does not stop you starting a second instance — the
banner tells you which port it actually got.

---

## 4. Model A — the thin server, in fifteen minutes

### 4.1 On the machine that will host

**Packaged (recommended).** Copy `PBRForge-Setup-<version>.exe` to the host and
run it. It installs without Administrator, puts the executable in
`%LOCALAPPDATA%\Programs\PBRForge`, and offers three checkboxes that matter:

| Checkbox | Leave it |
|---|---|
| Create a desktop shortcut | on |
| Open the Windows firewall (private networks only) | **on** — see §6 |
| Start the server automatically when I log in | on if the host is usually on |
| Create a local HTTPS certificate | **on** — see §5 |

**From the source tree.** From the repository root, in a normal PowerShell
window:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-server.ps1
```

That script is the source equivalent of the installer and does the same five
things: versioned install with a `current` junction, config outside the install,
firewall, certificate, logon task. It also runs `doctor` at the end so you see
the state of the machine before you try to use it. Add `-Port 9000` to host on a
different port, `-NoAutostart` to skip the task, `-Machine` for an all-users
install (needs an elevated shell).

**Either way, verify:**

```powershell
# Packaged: the installer does not add itself to PATH.
& "$env:LOCALAPPDATA\Programs\PBRForge\pbrforge.exe" doctor
& "$env:LOCALAPPDATA\Programs\PBRForge\pbrforge.exe" selftest

# Source:
python -m pbrforge doctor      # should end in "ready to run"
python -m pbrforge selftest    # 18/18
```

`doctor` is the front door. It reports Python, GPU, paths, ports, network, TLS,
auth, the bind address and the firewall rule, and it says what to do about each
thing it does not like. If it ends in `ready to run`, the host is fine.

### 4.2 Start it

```powershell
# Source:
python -m pbrforge serve

# Packaged:
& "$env:LOCALAPPDATA\Programs\PBRForge\pbrforge.exe" serve
```

or launch **PBRForge** from the Start Menu or desktop. The console shows the
address and the code, which is the entire point of it being a console app:

```
  PBRForge 0.1.0  ·  listening on 0.0.0.0:7788

    This PC                http://127.0.0.1:7788
    LAN (Wi-Fi)            http://192.168.1.100:7788

    Pairing code           K7QM-3XPZ
    Library                C:\Users\you\Documents\PBRForge\library
    Config                 C:\Users\you\AppData\Roaming\pbrforge\config.toml
    TLS                    off (LAN clients fall back to WebGL2 rather than WebGPU;
                           run 'pbrforge tls init' to enable it)

    Ctrl+C to stop
    Or from any other window:  pbrforge stop
```

Ctrl+C stops it in that window. From anywhere else — a different terminal, after a
logon-task start, or after you have closed the console — use `pbrforge stop` (§13.5).
The banner names it because Task Manager will show you the wrong process.

### 4.3 On every other PC

Open the **https** URL in a browser, enter the pairing code once, done.

No installation. No Python. No browser extension. The session is a signed cookie
valid for 90 days, so this is a once-per-machine step.

Give the client a proper shortcut so it behaves like an installed app:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\make-client-shortcut.ps1 `
    -Server https://192.168.1.100:7443
```

That script first checks that a server actually answers at that address — so a
typo is caught immediately rather than becoming "the shortcut is broken" — then
creates a shortcut that launches the server in a chromeless `--app=` window,
with its own taskbar entry and Alt-Tab identity. It looks like an application
because it is being used as one.

### 4.4 Which address to give people

Prefer **https** and prefer the **stable** address:

| Address form | Use when |
|---|---|
| `https://192.168.1.100:7443` | the host has a static/reserved IP — set a DHCP reservation |
| `https://hostname.local:7443` | mDNS resolves on your network (common on macOS/iOS, patchy on Windows) |
| `https://hostname:7443` | the machine is in DNS — add `--host-name` to `tls init` for it |
| `http://192.168.1.100:7788` | fallback only — costs you WebGPU on clients, see §5.1 |

A DHCP-assigned address will change eventually, and when it does every bookmark
and every client shortcut breaks at once. The fix is a DHCP reservation on the
router, and it is worth doing the day you set this up rather than the day it
breaks.

---

## 5. HTTPS, and why it is not optional

This is the one part of the deployment that is genuinely load-bearing rather than
hygiene, and it is worth understanding rather than just following.

### 5.1 The secure-context rule

Browsers grant privileged APIs only in a **secure context**, which is:

- `https://` — any address, or
- `http://localhost` / `http://127.0.0.1` — the loopback exception.

`http://192.168.1.100:7788` is **neither**. It is plain HTTP to a non-loopback
address, so it is not a secure context, and the browser silently withholds:

| API | On `https://192.168.x.x` | On `http://192.168.x.x` |
|---|---|---|
| **WebGPU** | available | **unavailable** |
| Clipboard write | available | restricted |
| Service workers (offline, PWA install) | available | unavailable |
| `navigator.gpu`, GPU-side processing | available | unavailable |
| WebGL2 | available | available |
| Canvas, WASM, IndexedDB, the whole app | available | available |

So the tool does not *break* without HTTPS. It quietly becomes a worse version
of itself on every machine except the host, and — this is the bad part — quietly
is the operative word. There is no error. The preview just uses WebGL2, and you
conclude that the LAN is slow.

The UI's server badge reports this honestly. Hover it in the top bar; on a plain
HTTP origin the tooltip says so.

```
  http://192.168.x.x        secure origin required for WebGPU; this page is on http:
```

### 5.2 Create the certificate

On the host, once:

```powershell
python -m pbrforge tls init
```

That generates a local certificate authority and a leaf certificate whose Subject
Alternative Names cover:

- `localhost` and `127.0.0.1`
- the machine's hostname and `<hostname>.local`
- **every LAN IPv4 address the machine currently has**

Certificate generation needs one of two backends, and `doctor` tells you which is in play:

| Backend | When | Cost |
|---|---|---|
| `cryptography` | bundled into the installed `.exe` if the build machine had it, or `pip install cryptography` in a source checkout | ~3.6 MB on the exe |
| the `openssl` CLI | anything else. Found on `PATH` or in the usual install locations — **Git for Windows ships one**, so most machines already have it | nothing |

The second row is why the installed build is not a second-class citizen here. Check with:

```powershell
pbrforge doctor | findstr openssl
```

If neither backend exists you get a clear message and an HTTP server — a working deployment with a
worse preview. On the packaged build this is nearly always solved by installing Git.

Check what you have at any time:

```powershell
python -m pbrforge tls status
```

It prints the backend, the paths, the expiry, the names covered, and whether the config currently
has `tls` on or off — because "the certificate is fine" and "the server is using it" are two
different questions and only one of them is the obvious one.

`status` also detects **SAN drift** — the case where the certificate is valid but
no longer covers this machine's address, because the router handed out a
different IP. That produces a browser warning that looks like a broken
certificate and is actually a stale one. The fix is:

```powershell
python -m pbrforge tls init --force
```

regenerating the CA and redeploying it to clients (§5.3), because a new leaf
under a new CA is not something existing clients already trust.

### 5.3 Install trust on each client

A self-signed certificate is not trusted by anything until you say so, and
"click through the warning" is not a fix — several browsers refuse to grant a
secure context on a page whose certificate the user has bypassed.

On the host:

```powershell
python -m pbrforge tls trust
```

That writes a **`client-trust\`** folder — full path
`%APPDATA%\pbrforge\tls\client-trust\` — containing:

| File | What it is |
|---|---|
| `ca.pem` | the certificate authority — the thing that actually needs installing |
| `trust-ca.ps1` | a Windows installer for it, current-user by default |
| `READ-ME.txt` | what to do, and what you lose by skipping it |

Copy that folder to each client PC — a USB stick, a network share, or just the
text of `ca.pem` pasted into a file — then run the script there:

```powershell
.\trust-ca.ps1              # current user only, no admin prompt
.\trust-ca.ps1 -Machine     # all users, needs an Administrator prompt
```

The default writes to `CurrentUser\Root`, which needs no elevation **and is
enough**: Chrome and Edge read that store, and Firefox on Windows reads the
Windows store too. The `-Machine` form is for a shared PC where every account
should get it. The script is idempotent — it checks the thumbprint first and says
`Already trusted` if you run it twice, so a second run costs nothing.

Restart the browser afterwards. An already-open tab keeps the certificate state
it loaded, so a page opened before the install will keep warning until it is
reloaded — which is the single most common reason someone concludes the script
did not work.

The generated script and its `READ-ME.txt` are Windows-only. For a macOS or Linux
client, `ca.pem` is all you need:

```bash
# macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca.pem
# Debian/Ubuntu
sudo cp ca.pem /usr/local/share/ca-certificates/pbrforge-local-ca.crt && sudo update-ca-certificates
# Fedora/RHEL
sudo cp ca.pem /etc/pki/ca-trust/source/anchors/pbrforge-local-ca.crt && sudo update-ca-trust
```

Firefox keeps its own certificate store on Linux and macOS, so it needs
`about:config` → `security.enterprise_roots.enabled = true` or a manual import via
Settings → Privacy & Security → Certificates → View Certificates → Authorities.
If that is more trouble than it is worth, use Chrome or Edge on that machine —
the WebGPU capability is the same.

### 5.4 The honest summary

| Situation | Effect | Worth the setup? |
|---|---|---|
| Host machine only, `http://127.0.0.1:7788` | loopback *is* a secure context — full WebGPU | nothing to do |
| LAN clients over plain HTTP | WebGL2 preview; every other feature works | acceptable to start |
| LAN clients over HTTPS with a trusted CA | full WebGPU everywhere | **yes** — that is what §5.2–5.3 buy you |

Start on HTTP if you want to. Move to HTTPS the first time you notice the preview
is doing less on a second machine.

---

## 6. The firewall, and the one symptom everybody hits

> "It works perfectly on the machine running it, and my laptop cannot see it at
> all."

That is the firewall, essentially every time. Windows Firewall blocks inbound
connections to a program that has no exception, and there is no useful error —
the connection simply times out. It reads like a broken server and is not one.

Fix it by running, **on the host**:

```
deploy\allow-firewall.cmd
```

Double-clickable. It requests Administrator itself, adds the three rules, prints
every address the machine can now be reached at, and is safe to run twice (it
checks for existing rules first). `--remove` deletes them again.

If you prefer to see what it did:

```powershell
netsh advfirewall firewall show rule name="PBRForge*"
```

Diagnosing a suspected firewall problem without touching the firewall: on a
client, `Test-NetConnection 192.168.1.100 -Port 7788` fails with a timeout when
blocked and succeeds instantly when not.

---

## 7. Pairing

Auth is **on by default**, and the reasoning is worth one paragraph: this server
holds your entire texture library. "It is only a LAN" is a reasonable assumption
on a home network and a bad one on a shared office network, an apartment Wi-Fi,
or a network with a guest VLAN that can route to yours. Pairing is once per
client, it costs nothing ongoing, and being wrong the other way is expensive.

### 7.1 How it works

| Property | Value |
|---|---|
| Code format | 8 characters, grouped `XXXX-XXXX` |
| Alphabet | Crockford-style, minus `I`, `L`, `O`, `U`, `0`, `1` — so there is no ambiguity when reading it aloud or typing it from a photo |
| Derived from | the `secret.key` HMAC, plus nothing else — so it is stable across restarts |
| Session | stateless signed cookie `<expiry>.<hmac>`, valid 90 days |
| Requires | no session table, no database, no server-side state |
| Rate limit | 5 failures, then a 30-second lockout |
| Exempt from auth | `/pair`, `/api/ping`, `/favicon.ico` |

Stable is the interesting property. A code regenerated on every start is a code
you can never look up after the fact — which matters enormously on a headless
NAS or a systemd server, where the console you would have read it from does not
exist. Because it is derived rather than random, you can always ask:

```powershell
python -m pbrforge code
```

### 7.2 Rotating the code

```powershell
python -m pbrforge code --rotate
```

This replaces the secret. **Every paired client is revoked immediately** — the
existing cookies stop verifying, and each machine must pair again with the new
code. There is no session table to clear and no per-device revocation because
there is no per-device identity; rotation is global by design. That is the right
trade for personal use: one command that provably locks everyone out, versus a
device management console nobody will maintain.

Use it when a laptop is lost, when a guest was given access, or just
periodically.

### 7.3 If a client cannot pair

| Symptom | Cause | Fix |
|---|---|---|
| Code rejected, `403` | typo — but the alphabet excludes the ambiguous characters, so this is rarer than you expect | re-read it, or `pbrforge code` on the host |
| Locked out for 30 s | 5 failures | wait 30 seconds |
| Asked to pair again after every restart | the config directory is not persisted | Docker: mount `/config` (§10.5). Otherwise check `PBRFORGE_CONFIG_DIR` |
| Asked to pair again after an **upgrade** | config was placed inside the install directory | move it; `install-server.ps1` and the installer both keep them separate |
| Pair page never appears | you are already paired, or auth is off | `pbrforge config` on the host shows the effective auth setting |
| Paired on one PC but not another | cookies are per-browser-profile | that is expected; pair each machine once |

---

## 8. Discovery

`pbrforge discover` broadcasts a UDP probe on port **7789** and lists every
server that answers. This exists so you can find the server without knowing its
IP — useful on a network where the host is a DHCP client and the address keeps
moving.

```powershell
python -m pbrforge discover
```

```
  pbrforge-nas   192.168.1.42:7788    pbrforge 0.1.0    library /srv/textures
```

| It works | It does not work |
|---|---|
| Both machines on the same subnet | Different subnets or VLANs |
| UDP 7789 allowed inbound on the host | The firewall rule missing (the `.cmd` adds it) |
| Host networking, or a real Windows/Linux host | **Docker bridge networking** (§10.4) — the broadcast cannot cross the bridge |
| The server answering on UDP | `serve --no-discovery` |

When discovery does not work, nothing is broken — it is a convenience, and the
fallback is to write the URL down once. `pbrforge url` on the host prints every
address it can be reached at, which is the reliable answer.

Unauthenticated and intentionally so: the response contains the server's name,
version, port, library path and platform, and nothing that grants access. A
pairing code is still needed to do anything with it.

---

## 9. Starting automatically

The installer's checkbox, or later:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1
powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1 -Status
powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1 -Remove
```

### 9.1 Scheduled task, not a Windows service

This is a deliberate choice with a real trade-off, so here it is rather than
buried in a script comment.

| | **Scheduled task** (chosen) | **Windows service** |
|---|---|---|
| Console visible | **yes** — the URL and pairing code | no — session 0, output nowhere |
| Survives reboot | yes, at logon | yes, at boot |
| Starts before login | no | yes |
| Restarts on crash | yes, 3 attempts | yes, by the SCM |
| Debuggable | double-click it | read the event log |
| Needs a login session | **yes** | no |

A service would run in session 0, isolated from the desktop, with no console.
PBRForge prints exactly two things to its console that a user needs and cannot
get anywhere else: **the LAN URL** and **the pairing code**. As a service both go
nowhere, the server runs perfectly, and nobody can connect to it — which is a
worse failure than not starting at all, because it looks like success.

Use `-AtStartup` for a headless box. Note the caveat: a startup task still needs
the account to exist and, for "run whether user is logged on or not", the account
password stored. For a genuinely headless always-on server, §10 or §11 is a
better fit than Task Scheduler.

The task is named **`PBRForge Server`** and runs:

```
python -m pbrforge serve --port 7788
```

with the working directory set to the install's `current` junction — which is
what makes §13.2 work.

---

## 10. Model C — Docker on a NAS or Linux box

For when the always-on machine is not a Windows PC.

### 10.1 Run it

From the repository root:

```sh
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f      # URL and pairing code
docker compose -f deploy/docker-compose.yml run --rm pbrforge code
```

### 10.2 Networking: pick one, on purpose

| | **Bridge** (default in the file) | **Host** |
|---|---|---|
| Port mapping | explicit, in the compose file | none — the port is set directly |
| **UDP discovery** | **broken** — beacons cannot cross the bridge | works |
| Works on Docker Desktop (Win/macOS) | yes | **no** — silently unreachable |
| Works on Synology/QNAP/Unraid/Linux | yes | yes |
| Client setup | use a fixed URL | same, plus `pbrforge discover` works |

The compose file defaults to bridge because it runs everywhere, and documents
host networking as an commented option because it is better where it works. This
is a Docker property, not a PBRForge limitation, and the fix is one line rather
than a code change.

### 10.3 Volumes, and why both matter

| Mount | Holds | Symptom if missing |
|---|---|---|
| `/config` | `secret.key`, `tls/`, `config.toml` | every restart un-pairs every client and rotates the code |
| `/library` | your textures | uploads land in the container and vanish on upgrade |

`/library` is a **bind mount**, not a named volume, on purpose: your textures must
be visible to the NAS file manager, to SMB, and to whatever backup job you run.
A named volume makes them invisible to all three, which is a data-loss footgun
dressed as a convenience.

The entrypoint checks whether `/config` is a real mount point and prints a loud
warning if it is not, because "everything works but I have to pair again every
time" is otherwise a genuinely hard bug to find.

### 10.4 NAS-specific gotchas

| NAS | Gotcha | Fix |
|---|---|---|
| **Synology** | Container Manager often runs as `root`; the share may be owned by another UID | set `user: "1026:100"` (or your share's owner) in compose |
| **Synology** | `network_mode: host` is available but Container Manager's UI may override it | set it in the compose YAML, not the UI |
| **QNAP** | Container Station rewrites some compose keys | re-apply the compose file after editing it in the UI |
| **Unraid** | `/mnt/user/...` paths; appdata belongs on the cache pool | `/config` → `/mnt/user/appdata/pbrforge`, `/library` → `/mnt/user/textures` |
| **Raspberry Pi / ARM** | the image builds from `python:3.12-slim-bookworm`, which is multi-arch | works, but the pipeline will be slow — fine as a file server, poor as a renderer |
| **Any** | `permission denied writing /config` | `sudo chown -R 1000:1000 ./config`, or set `user:` in compose |

### 10.5 Installing the certificate on clients

```sh
docker compose -f deploy/docker-compose.yml exec pbrforge tls trust
docker compose -f deploy/docker-compose.yml cp pbrforge:/config/client-trust ./client-trust
```

Then run `trust-ca.ps1` from `./client-trust` on each Windows client, or follow
`READ-ME.txt` for macOS/Linux.

### 10.6 Health and logs

```sh
docker compose -f deploy/docker-compose.yml ps        # health: healthy
docker compose -f deploy/docker-compose.yml logs -n 50
docker inspect --format '{{.State.Health.Status}}' pbrforge
```

The health check calls `/api/ping`, not a TCP connect — an open socket with a
wedged application would pass a connect check, and that is exactly the failure
you want reported.

Log rotation is configured (`10m` × 3). The default `json-file` driver is
unbounded, which on a NAS means a container quietly filling the volume over
months.

---

## 11. Model C alternative — systemd on Linux

No Docker. Install the package on the box and run it under systemd:

```sh
sudo useradd --system --home /var/lib/pbrforge --create-home pbrforge
sudo mkdir -p /opt/pbrforge /srv/textures
sudo chown pbrforge: /srv/textures /var/lib/pbrforge
# copy pbrforge/ and ui/ into /opt/pbrforge
sudo install -m 0644 deploy/pbrforge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pbrforge
sudo systemctl status pbrforge
```

Read `deploy/pbrforge.service` before installing it — it has three paths to
adjust and a comment block explaining the hardening, including why
`MemoryDenyWriteExecute` is deliberately *off*.

Getting the pairing code on a headless systemd host, which is the one thing
people get stuck on:

```sh
journalctl -u pbrforge -n 30 --no-pager          # the startup banner
sudo -u pbrforge python -m pbrforge code          # on demand
```

---

## 12. Model B — per-PC install with a shared library

Sometimes there is no always-on machine. Then: install PBRForge on every PC, and
point them all at one library on a NAS or a shared folder.

```powershell
python -m pbrforge serve --library "\\nas\textures\pbrforge" --no-auth --open
```

Or permanently, in `config.toml`:

```toml
[paths]
library = "\\\\nas\\textures\\pbrforge"
```

`--no-auth` is defensible **here and only here**: each PC is serving itself on
loopback, and network access is not being offered. It is not a convenience
setting; it is a statement that this instance is not reachable from outside the
machine.

### 12.1 The SMB caveat, which is real

Sharing a library over SMB works. Sharing a **cache** over SMB is where things
get interesting, and it is worth knowing why.

Byte-range locking — `msvcrt.locking` on Windows, `fcntl` on POSIX — is
**not reliable over SMB**. It is advisory in some configurations, silently
downgraded in others, and it depends on the server's implementation. A cache that
relies on it can corrupt entries in a way that is very hard to attribute
afterwards, because the corruption is a plausible-looking map rather than a
crash.

So PBRForge's cache uses **lock files** created with `O_CREAT | O_EXCL` instead:

| | Byte-range lock | **Lock file (`O_EXCL`)** |
|---|---|---|
| Works over SMB | unreliably | yes |
| Works over NFS | mostly | yes |
| Needs OS support | `msvcrt` / `fcntl` | no |
| Leaks on crash | no | yes — reclaimed after 600 s |
| Used by PBRForge | no | **yes** |

The trade-off is the one in the last row: a process killed mid-write leaves a
lock file behind. A stale lock older than **600 seconds** is reclaimed
automatically, so the worst case is a ten-minute stall on one cache entry, not a
deadlock. `python -m pbrforge cache` reports locks and lets you prune.

Practical guidance:

| Share | Fine for library | Fine for cache |
|---|---|---|
| Local disk | yes | yes — best |
| SMB from a NAS | yes | yes, with the 600 s caveat |
| OneDrive / Dropbox / Google Drive | **no** — sync churn, file locks, and long-path failures | no |
| A network drive that sleeps | yes | painful — the cache is chatty |

**Keep the cache local on each PC even when the library is on the NAS.** The
library is your data; the cache is derived and disposable. Sharing the derived
thing is all of the risk and none of the benefit.

---

## 13. Upgrades, rollback and backup

### 13.1 Packaged installs

Run the new installer. Same `AppId`, so Inno treats it as an upgrade rather than
a second product. The config directory is untouched, so paired clients stay
paired. Nothing to migrate.

The installer **stops a running server before it copies anything**, using the old
build's own `stop`. That is not politeness. §13.5 explains why the process you can
see in Task Manager is not the one holding the port, and therefore why an upgrade
over a live server is a case worth handling explicitly rather than hoping Inno
Setup's file-in-use handling covers it.

### 13.2 Source installs — versioned with a junction

`install-server.ps1` installs to:

```
%LOCALAPPDATA%\Programs\PBRForge\
    versions\0.1.0\      <- real files
    versions\0.2.0\
    current -> versions\0.2.0      (a junction)
```

Everything — the autostart task, the Start Menu shortcut, the launcher — points
at `current`. So:

| Task | Command |
|---|---|
| Upgrade | `install-server.ps1` again (new version dir, re-point `current`) |
| **Rollback** | re-point `current` at the older version dir |
| Verify which is live | `(Get-Item ...\current).Target` |

Rollback is one reparse-point update, with no window in which the install is
half-replaced. That is the whole reason for the junction rather than an
in-place copy.

Delete old `versions\` directories by hand when you are confident. Keep two.

### 13.3 Backing up

| What | Priority | Why |
|---|---|---|
| `library\` | **critical** | your textures. Not reproducible |
| `config\secret.key` | useful | losing it means re-pairing clients — annoying, not fatal |
| `config\tls\` | useful | losing it means redeploying the CA to every client |
| `config\config.toml` | useful | small, and it records your ports and paths |
| `cache\` | **skip** | derived, content-addressed, disposable |

A one-line backup of the two directories that matter:

```powershell
robocopy "$env:APPDATA\pbrforge" "D:\backup\pbrforge-config" /MIR
robocopy "$env:USERPROFILE\Documents\PBRForge" "D:\backup\pbrforge-library" /MIR
```

Restoring is the reverse. Restore `config\` before starting the server, and
clients will not need to re-pair.

### 13.4 Uninstalling

| Model | How | What is kept |
|---|---|---|
| Packaged | Apps & features, or the Start Menu entry | config + library (the uninstaller asks whether to delete them) |
| Source | delete the install dir | config + library |
| Docker | `docker compose down` | both volumes, unless you add `-v` |
| Task | `install-autostart.ps1 -Remove` | the install |
| Firewall | `allow-firewall.cmd --remove` | everything |

The uninstaller stops a running server, then removes the scheduled task and the firewall rules,
because a scheduled task pointing at a deleted executable fails on every subsequent login and looks
exactly like malware. It does the stop first for a less obvious reason — see the next section.

Without `/SUPPRESSMSGBOXES` the uninstaller asks whether to delete the config and library, and the
default on the *silent* path is **keep**. An unattended uninstall must never destroy a texture
library because it could not ask.

### 13.5 Stopping the server

```powershell
pbrforge stop              # from any window on the host
pbrforge stop --json       # {"stopped": true, "detail": "stopped pid 13844 ..."}
pbrforge stop --strict     # exit 1 if there was nothing to stop (for scripts)
```

This deserves its own section because the obvious approach does not work, and it fails in a way that
looks like a different problem.

**`pbrforge.exe` is not one process.** It is a PyInstaller `--onefile` build, which is a bootloader
that unpacks the bundle to a temporary `_MEIxxxxxx` folder and then runs the application in a
**child**. Both are called `pbrforge.exe`. The child is the one that binds the port; the bootloader
is the one Task Manager and `Start-Process` show you first. Ending the process you can see leaves
the server running, the port bound, and — the part that turns a papercut into a bug — **the
executable file locked**. That last consequence is why the uninstaller stopped deleting
`pbrforge.exe`: it could not, so it left it behind.

The same lock is why an **upgrade** needs the stop, and it is why the installer runs `pbrforge stop`
*before* copying anything rather than trusting Inno Setup's file-in-use handling. When Inno cannot
replace a file it falls back to scheduling a rename on the next reboot, so a tool whose entire job is
to be running would land in the state "upgraded, once you reboot" — with the old version still
serving in the meantime, and no obvious reason to reboot, because nothing said a reboot was needed.

So the server records itself instead. On startup, *after* the socket is bound, `serve` writes
`%APPDATA%\pbrforge\server.pid`:

```json
{ "pid": 13844, "port": 7788, "host": "0.0.0.0", "scheme": "http",
  "started": 1792296815.867, "version": "0.1.0" }
```

That `pid` is the child — the process actually holding the socket, which is the one worth stopping.
`pbrforge stop` reads it, terminates it, waits for the port to be released, and deletes the file.
Killing the child also ends the bootloader, which is sitting in `waitpid`.

**The guards matter more than the mechanism.** A PID file is a claim, not a fact: it survives a
crash, a power cut, and being copied between machines, and Windows recycles PIDs quickly. So `stop`
corroborates before it acts, and **refuses rather than guesses**:

| Record says | What `stop` does | Why |
|---|---|---|
| pid P, port N, and P owns N | stops P | positive identification: only the server binds that port |
| pid P, port N, but N has no listener | refuses; treats the record as stale | `server.pid` is written *after* binding, so if nothing is listening there, this record does not describe a server. The executable name must not override this — it is what stops `stop` from killing a *second* PBRForge belonging to another config directory |
| pid P, port N, but another pid owns N | refuses | a different process has the port; P is not serving |
| pid P is dead | cleans up the record, reports "nothing to stop" | normal after a crash or a reboot |
| the file is unparseable | says so and names the file | "nothing is running" would send you hunting for a server that does not exist |
| pid P is alive but unidentifiable | **does nothing**, prints what P is | a stale file naming a recycled PID must not kill your CAD application |

If no PID file exists at all, `stop` will still adopt a process that is listening on the configured
port **and** is unambiguously `pbrforge.exe`. A `python.exe` on the same port is left alone, because
"a Python process is listening on a port that appears in a config file" is not enough evidence to
terminate something on a machine that runs several Python services.

`pbrforge url` prints the same status, and `pbrforge doctor` reports it as a check — including the
live port, which can differ from the configured one if `serve --port` overrode it.

Ctrl+C still works in the console that owns the process. `pbrforge stop` is for every other case,
which is most of them: a shortcut launch, a logon task, or a console window you have since closed.

---

## 14. Troubleshooting

Ordered by how often each one actually happens.

| Symptom | Most likely cause | Fix |
|---|---|---|
| Works on the host, invisible from other PCs | **firewall** | `deploy\allow-firewall.cmd` on the host |
| Preview is slower or plainer on a client than on the host | no secure context → WebGL2 instead of WebGPU | use `https://`; §5. Hover the server badge in the top bar — it says so |
| Certificate warning on every client visit | the CA is not trusted on that client | `pbrforge tls trust` on the host, run `trust-ca.ps1` on the client (§5.3) |
| Certificate warning that appeared later, after working | the host's IP changed and the certificate no longer covers it | set a DHCP reservation; `tls init --force`; redeploy the CA |
| Asked to pair again on every restart | the config directory is not persisted | Docker: mount `/config`. Otherwise check `PBRFORGE_CONFIG_DIR` |
| Asked to pair again after an upgrade | config inside the install dir | move it out; both installers keep them separate for this reason |
| `403` on pairing | typo, or the code was rotated | `pbrforge code` on the host |
| `429` on pairing | 5 failures, 30 s lockout | wait 30 seconds |
| `port already in use` | another process — often a previous PBRForge | the message names the process; `doctor` names it too |
| `discover` finds nothing | UDP 7789 blocked, different subnet, or Docker bridge | add the firewall rule; §8; §10.2 |
| Server exits immediately on startup | an import error or a bad config | run `pbrforge doctor`; `python -m pbrforge serve` to see the traceback |
| SmartScreen warning on the installer | unsigned — signing needs a paid certificate | More info → Run anyway |
| `permission denied` writing `/config` (Docker) | NAS share owned by a different UID | `chown 1000:1000`, or set `user:` (§10.4) |
| Library writes fail, or files lock | the library is in OneDrive/Dropbox | move it to a real local path; §12.1 |
| Everything is slow, and `%TEMP%` is huge | temp on a full system drive | set `PBRFORGE_TEMP` to a scratch disk with room — the pipeline writes multi-GB intermediates |
| `doctor` says no GPU | a VM, a remote session, or old drivers | fine for the server; matters for browser preview only |
| Browser tab seems to hang on the first load | an old version cached via ETag | hard-reload (Ctrl+F5) |
| Task Manager shows `pbrforge.exe` but nothing answers a request | that is the bootloader, not the server; the real process is its child | `pbrforge stop` — §13.5 |
| `pbrforge stop` says *refusing to terminate* | a stale `server.pid` names a PID that is now some other program | working as intended. Delete `%APPDATA%\pbrforge\server.pid`, or start the server again to overwrite it |
| `pbrforge stop` says the record is *stale* | the server exited, or the PID was recycled after a crash | nothing to do; the file is cleaned up automatically |
| Uninstall left `pbrforge.exe` and `install-mode.txt` behind | the server was running and held the executable open | fixed in 0.1.0 — the uninstaller stops the server first (§13.5). If you see it, run `pbrforge stop` and uninstall again |
| Uninstall hung with no window, in a script | `MsgBox` ignores `/VERYSILENT`; only `SuppressibleMsgBox` honours it | fixed in 0.1.0. Use `/SUPPRESSMSGBOXES`, which now defaults to *keep your textures* |

### 14.1 The two commands that answer most questions

```powershell
python -m pbrforge doctor                    # on the host
python -m pbrforge selftest                  # end to end, ~7 seconds
```

`doctor` checks Python, elevation, GPU, paths, the live server, ports (and names the process
holding a busy port), network, TLS, auth, the bind address and the firewall rule.
`selftest` runs 13 real end-to-end tests — including binding on the real LAN address,
spawning and stopping a second server process, and fetching the UI — so "it works" is a
measured claim rather than an impression.

On a client PC with no install, the equivalent check is one line of PowerShell:

```powershell
Invoke-WebRequest https://192.168.1.100:7443/api/ping -UseBasicParsing | Select-Object -ExpandProperty Content
```

Any valid JSON back means the server is reachable and healthy, and the problem is
in the browser rather than the network.

---

## 15. Deployment checklist

Print this. Tick it.

**Host**

- [ ] Python 3.11+ on PATH (or the packaged exe, which needs none)
- [ ] `pbrforge doctor` ends in `ready to run`
- [ ] `pbrforge selftest` → 18/18
- [ ] Library path chosen — **not** inside OneDrive/Dropbox
- [ ] Cache and temp on a disk with room for multi-GB intermediates
- [ ] `pbrforge tls init` — certificate created
- [ ] `pbrforge doctor` reports no SAN drift
- [ ] Firewall rule added (`allow-firewall.cmd`)
- [ ] DHCP reservation for the host, so the address is stable
- [ ] Autostart configured, or a documented decision not to
- [ ] `pbrforge url` recorded somewhere you will find it in six months

**Each client**

- [ ] `https://` URL opens
- [ ] `trust-ca.ps1` run, and no certificate warning remains
- [ ] Paired with the code — once
- [ ] Server badge in the top bar is **green** and names the host
- [ ] Badge tooltip does **not** say "secure origin required for WebGPU"
- [ ] Shortcut created (`make-client-shortcut.ps1`)
- [ ] An image round-trips: open, process, export

**After a reboot**

- [ ] The server is running
- [ ] Clients still reach it
- [ ] Nobody had to re-pair

**Before you trust it**

- [ ] `pbrforge stop` actually stops it, and the port is free afterwards
- [ ] Uninstall tested once: install → run → uninstall, and the install directory is empty afterwards

---

## 16. Related documents

| Document | Covers |
|---|---|
| `README.md` | what PBRForge is, and the five architectural decisions |
| `INSTALLATION.md` | one developer machine, from nothing to running |
| `docs/ARCHITECTURE.md` | why the server is stdlib-only, and the module map |
| `docs/AUTOMATION.md` | the full CLI, and Task Scheduler recipes |
| `docs/TROUBLESHOOTING.md` | pipeline and image problems, as opposed to network ones |
| `docs/QC_METRICS.md` | what the seam score means, precisely |
| `INSTALLATION.md` §0 | the OneDrive caveat, which bites sooner than you expect |
