# PBRForge updates

PBRForge uses **GitHub Releases** as its only update channel. There is no background poller, no
account, no telemetry and no mirror fallback. A check happens only when you ask for it.

---

## What is implemented

| Piece | State |
|---|---|
| Release check against the GitHub API | ✅ |
| Semantic version comparison | ✅ |
| Expected asset selection (`PBRForge-Setup-<version>.exe`) | ✅ |
| SHA-256 verification when the release publishes a digest | ✅ |
| Size verification on every download | ✅ |
| Detached Ed25519 signature verification | ✅ |
| **Mandatory signature for v2.0+ auto-install** | ✅ |
| Silent Inno Setup launch | ✅ code path |
| UI settings (`check`, `auto_install`) persisted to `config.toml` | ✅ |
| GitHub Release with a signed installer asset | ✅ from `v2.0.0` |

The check returns a released version or a named failure. It is not allowed to invent an update, and
it never falls back to an arbitrary executable from the asset list.

---

## Check

```powershell
python -m pbrforge update
python -m pbrforge update --check --json
```

The check makes one `GET` to:

```text
https://api.github.com/repos/learnerarchitecture-debug/pbrforge/releases/latest
```

It compares the tag with `pbrforge.__version__` using numeric semantic-version components. The
result contains current/latest versions, whether an update is available, the selected asset, its
size, digest (when supplied), the signature asset when present, release notes, and an honest `error`
string on failure.

The server caches the result for 24 hours on disk and reuses it for one hour in memory. Opening the
update dialog shows the cached result; **Check now** forces a fresh request.

---

## Signatures

Every release from `v2.0.0` onward ships:

```text
PBRForge-Setup-2.0.0.exe
PBRForge-Setup-2.0.0.exe.sig
SHA256SUMS.txt
```

The `.sig` file is a small JSON document produced by:

```powershell
python -m pbrforge release sign .\PBRForge-Setup-2.0.0.exe `
  --key .\release-private.pem --output .\PBRForge-Setup-2.0.0.exe.sig
```

It contains the Ed25519 signature and the SHA-256 of the exact installer bytes. The public key is
embedded in `pbrforge/update.py` and mirrored in `deploy/release-signing-public.pem`; the private key
is never part of the repository.

### Verification order

1. Byte size must match the release asset metadata.
2. SHA-256 must match the release digest when GitHub supplies one.
3. The detached Ed25519 signature must verify against the embedded public key and the exact bytes.

For versions **2.0.0 and later**, a missing or invalid signature blocks auto-install with a named
reason:

```text
why      PBRForge 2.0+ requires a signature before auto-install
fix      download the installer from the release page and verify SHA256SUMS.txt
         manually, or ask the publisher to sign the asset
```

That refusal is deliberate. Unsigned updates are not “verified by HTTPS”; they are refused.

---

## Install

```powershell
python -m pbrforge update --install
```

or press **Install update** in the web UI. The install path is deliberately narrow:

1. Refuse unless `sys.platform == "win32"`.
2. Refuse if the release has no asset named `PBRForge-Setup-<latest>.exe`.
3. Download to the temp directory through a `.part` file.
4. Verify the byte count against the release asset size.
5. Verify SHA-256 when GitHub supplies `digest` (`sha256:<hex>`).
6. Download the `.sig` asset and verify Ed25519 (mandatory for v2.0+).
7. Replace the `.part` file atomically.
8. Launch:

```text
PBRForge-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
                            /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS
```

9. Exit the running server so Windows can replace files. The installer's
   `PrepareToInstall` also calls `pbrforge stop` as a second guard.

A silent update does not relaunch the server. Start **PBRForge** from the Start Menu (or let the
logon task start it) when the installer finishes. If any verification fails, the downloaded file is
never executed; the error names the expected and received values.

### Auto-install

```toml
[updates]
auto_install = false
```

Default is **off**. When enabled, a manual update check that finds a fully verified installer may
launch it without another click. There is still no background poller, and the signature requirement
is not relaxed by this setting.

The web dialog writes settings through `POST /api/settings`; the CLI writes the same fields through
`config --set`. Both rewrite the canonical `config.toml`.

---

## Release contract

Publish a GitHub Release whose tag is `v<version>` and attach exactly:

```text
PBRForge-Setup-<version>.exe
PBRForge-Setup-<version>.exe.sig
SHA256SUMS.txt
```

The version inside the asset name, the tag and `pbrforge.__version__` must agree. The 0.2.0 installer
was verified for per-user install without Administrator, silent switches, server stop before file
replacement, library/config preservation across install/upgrade/uninstall, and a working uninstaller.
It was **not code-signed** and remains the historical unsigned release.

The `2.0.0` build is exercised again for install, start/ping/stop, packaged selftest, packaged vault
backup/restore, signature verification and uninstall. It is still **not Authenticode-signed**, so
SmartScreen warns and the release notes say so. Release signatures protect the updater's chain of
custody; they do not buy SmartScreen reputation.

See [INSTALLATION.md](../INSTALLATION.md) for both install routes.

---

## Safety properties

| Property | Behaviour |
|---|---|
| Update check disabled | Returns `available: false` with an explanatory error; makes no request |
| No network | Honest error; the local build keeps working |
| 404 / no release | `no GitHub release exists yet` |
| Release without installer | Names the expected asset; does not download an arbitrary `.exe` |
| Truncated download | Refused before execution |
| Digest mismatch | Refused before execution |
| Missing signature on v2.0+ | Refused before execution |
| Signature mismatch | Refused before execution |
| Non-Windows install | Names the platform limit; no fake success |
| Failed update | Never deletes the library or config |

---

## CLI output

Up to date:

```text
  current  2.0.0
  latest   2.0.0
  status   up to date
```

Update available:

```text
  current  2.0.0
  latest   2.1.0
  status   update available  ·  PBRForge-Setup-2.1.0.exe
  signed   yes
  release  https://github.com/learnerarchitecture-debug/pbrforge/releases/tag/v2.1.0
```

If GitHub returns no release, the status is `no GitHub release exists yet`. That remains a supported
honest state, not a fake update.

---

## Why not an auto-updater framework?

An external updater adds a background service, a second trust boundary and a failure mode that can
take a working local tool offline. The update surface here is one GET, one expected asset name, one
size check, one digest check, one Ed25519 verification and one explicit installer launch. That is
small enough to inspect completely, which is the point.
