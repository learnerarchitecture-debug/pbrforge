# 🔄 PBRForge updates

PBRForge uses **GitHub Releases** as its only update channel. There is no background poller, no
account, no telemetry and no mirror fallback.

---

## What is implemented

| Piece | State |
|---|---|
| Release check against the GitHub API | ✅ |
| Semantic version comparison | ✅ |
| Expected asset selection (`PBRForge-Setup-<version>.exe`) | ✅ |
| SHA-256 verification when the release publishes a digest | ✅ |
| Size verification on every download | ✅ |
| Silent Inno Setup launch | ✅ code path |
| UI settings (`check`, `auto_install`) persisted to `config.toml` | ✅ |
| GitHub Release with an installer asset | ❌ not published yet |
| Compiled Windows installer | ❌ not built in this release |

The absence of a release is reported plainly. Update checking is not allowed to invent an update.

---

## Check

```powershell
python -m pbrforge update --check
python -m pbrforge update --check --json
```

The check makes one `GET` to:

```text
https://api.github.com/repos/learnerarchitecture-debug/pbrforge/releases/latest
```

It compares the tag with `pbrforge.__version__` using numeric semantic-version components. The
result contains current/latest versions, whether an update is available, the selected asset, its
size, digest (when supplied), release notes, and an honest `error` string on failure.

The server caches the result for 24 hours on disk and reuses it for one hour in memory. Opening the
update dialog shows the cached result; **Check now** forces a fresh request.

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
5. Verify SHA-256 when GitHub supplies `digest` (`sha256:<hex>`); otherwise record that no digest was
   available and rely on the size/HTTPS.
6. Replace the `.part` file atomically.
7. Launch:

```text
PBRForge-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
                            /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS
```

8. Exit the running server so Windows can replace files. The installer's
   `PrepareToInstall` also calls `pbrforge stop` as a second guard.

If any verification fails, the downloaded file is never executed. The error names the expected and
received values.

### Auto-install

```toml
[updates]
auto_install = false
```

Default is **off**. When enabled, a manual update check that finds a verified installer may launch
it without another click. There is still no background poller.

The web dialog writes settings through `POST /api/settings`; the CLI writes the same fields through
`config --set`. Both rewrite the canonical `config.toml`.

---

## Release contract (for when the installer is built)

Publish a GitHub Release whose tag is `v<version>` and attach exactly:

```text
PBRForge-Setup-<version>.exe
```

The version inside the asset name must match `pbrforge.__version__` and the tag without the `v`.
The installer must:

- install per-user without Administrator;
- support `/VERYSILENT` and `/SUPPRESSMSGBOXES`;
- stop the running server before replacing files;
- preserve the user's library and `config.toml`;
- restart the server only if it was running before the upgrade;
- write an uninstall entry and provide a working uninstaller.

No release is created until those properties have actually been exercised. See
[INSTALLATION.md](../INSTALLATION.md) for the current source setup.

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
| Non-Windows install | Names the platform limit; no fake success |
| Failed update | Never deletes the library or config |

---

## CLI output

```text
  current  0.2.0
  latest   —
  status   no GitHub release exists yet
```

When a release exists:

```text
  current  0.2.0
  latest   0.3.0
  status   update available  ·  PBRForge-Setup-0.3.0.exe
  release  https://github.com/learnerarchitecture-debug/pbrforge/releases/tag/v0.3.0
```

---

## Why not an auto-updater framework?

An external updater adds a background service, a second trust boundary and a failure mode that can
take a working local tool offline. The update surface here is one GET, one expected asset name, one
size check, one optional digest check and one explicit installer launch. That is small enough to
inspect completely, which is the point.
