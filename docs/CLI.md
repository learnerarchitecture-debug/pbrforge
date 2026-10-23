# PBRForge CLI

Every command runs as `python -m pbrforge <command>`. A source checkout needs Python 3.11+ and the
`cryptography` package (vault, release signatures, TLS). The packaged installer has both and exposes
the same commands through the installed `pbrforge.exe`.

The CLI and the web app share one worker, one configuration and one library. A material forged in
the browser appears in `pbrforge library`, and vice versa.

---

## Quick reference

| Command | Use |
|---|---|
| `pbrforge` | Start the local server (same as `serve`) |
| `pbrforge serve` | Start the web app and print its URL/pairing code |
| `pbrforge url` | Print the addresses other PCs should use |
| `pbrforge code` | Show the pairing code; `--rotate` revokes every device |
| `pbrforge stop` | Stop the server through the supported lifecycle path |
| `pbrforge make PHOTO.png` | Convert one PNG into a material |
| `pbrforge batch FILE_OR_DIR ...` | Convert many PNGs serially |
| `pbrforge capture F1.png F2.png ...` | Merge a bracketed stack, optionally forge it |
| `pbrforge surface` | Generate a tileable procedural material |
| `pbrforge project` | Create, edit and render ordered material projects |
| `pbrforge setdoctor DIR` | Scan or repair a PBR texture set into a new folder |
| `pbrforge deliver DIR` | Write MaterialX/glTF/USD/Blender files |
| `pbrforge library` | List and filter finished materials |
| `pbrforge watch DIR` | Forge new PNGs dropped into a folder |
| `pbrforge plugin` | List or run an explicit local plugin |
| `pbrforge vault` | Encrypted backup, restore, verify or inspect |
| `pbrforge release` | Create an Ed25519 key, sign a file or verify one |
| `pbrforge security` | Audit the local security posture |
| `pbrforge update` | Check GitHub for a newer release |
| `pbrforge doctor` | Check whether this machine can run PBRForge |
| `pbrforge selftest` | Prove the install works end to end |
| `pbrforge discover` | Find PBRForge servers on the LAN |
| `pbrforge tls` | Create and manage the LAN certificate |
| `pbrforge cache` | Inspect or prune the artifact cache |
| `pbrforge config` | Show or change the configuration file |
| `pbrforge version` | Print the version |

Global flags accepted where they make sense:

| Flag | Effect |
|---|---|
| `--json` | Machine-readable output |
| `--config-dir DIR` | Use a different configuration root |
| `--library DIR` | Override the library folder |
| `--cache DIR` | Override the artifact cache |
| `--tls` / `--no-tls` | Force HTTPS or plain HTTP for server commands |
| `--no-auth` | DANGEROUS: skip pairing entirely. Local debugging only. |

Exit codes: `0` success, `1` a user-fixable failure (printed with a `why` and `fix`), `2` a usage or
environment error, `130` interrupted with Ctrl+C.

---

## Server and pairing

```powershell
python -m pbrforge serve
python -m pbrforge serve --host 127.0.0.1 --port 7788
python -m pbrforge serve --tls
python -m pbrforge url
python -m pbrforge code
python -m pbrforge code --rotate
python -m pbrforge stop
python -m pbrforge stop --strict     # exit 1 when nothing was running
```

`serve` prints the bound URL and, when auth is enabled, the pairing code. The code is derived from
the server secret and stays the same until `--rotate`; rotating revokes every paired device at once.

The server is a standard-library `ThreadingHTTPServer` with one pipeline worker. It is intended for
one person on a LAN, not as a shared service.

---

## Auto Forge

```powershell
python -m pbrforge make .\photo.png
python -m pbrforge make .\photo.png --resolution 2048 --category brick --json
python -m pbrforge batch .\photos --name-prefix wall_ -v
python -m pbrforge batch .\a.png .\b.png --no-seamless --metal none
```

Job options shared by `make`, `batch`, `capture`, `surface` and `watch`:

| Option | Default | Meaning |
|---|---|---|
| `--name` | file stem | Material name |
| `--preset` | `generic-pbr` | Export preset |
| `--category` | `generic` | Material type; `brick` flips height polarity |
| `--resolution` | `1024` | Working long edge, 256–4096 |
| `--metal` | `auto` | Metalness decision; `auto` is honestly dielectric by default |
| `--height` | `bright-high` | `bright-high` or `bright-recessed` |
| `--auto-levels` | off | Stretch the histogram (can clip) |
| `--white-balance` | off | Neutralise a colour cast |
| `--square` | off | Centre-crop to a square |
| `--no-seamless` | off | Skip the seam heal |
| `--no-delight` | off | Skip illumination flattening |

Output is a library material folder with `material.json`, `job.json`, `qc_report.json`, `maps/`,
`previews/`, `export/<preset>/` and a zip. `make --open` opens the folder.

---

## Capture stacks

```powershell
python -m pbrforge capture f1.png f2.png f3.png --out merged.png
python -m pbrforge capture f1.png f2.png f3.png --mode mean --forge --name "Wall"
```

| Option | Default | Meaning |
|---|---|---|
| `--mode` | `median` | `median`, `mean`, `min` or `max` |
| `--out` | `merged.png` | Merged PNG path |
| `--no-normalize` | off | Do not equalise exposure first |
| `--forge` | off | Submit the merged PNG to the normal pipeline |

PNG only, 2–12 frames, one shared size. No geometric registration or EXR/HDR merge.

---

## Surface Lab

```powershell
python -m pbrforge surface --list
python -m pbrforge surface --pattern brick --size 512 --weathering dust --out .\surface
python -m pbrforge surface --pattern stone --seed 7 --forge --name "Stone wall"
```

| Option | Default | Meaning |
|---|---|---|
| `--pattern` | `brick` | `brick`, `tile`, `plank`, `panel`, `stone`, `noise` |
| `--size` | `512` | Output edge in pixels |
| `--seed` | `1234` | Deterministic generator seed |
| `--weathering` | `dust` | `none`, `dust`, `grime`, `moss`, `wear` |
| `--weather-amount` | `0.35` | 0.0–1.0 |
| `--variant` | `0` | Pattern variant |
| `--rows` / `--cols` | `6` / `12` | Layout controls |
| `--mortar` | `0.055` | Mortar fraction |
| `--hue` | `warm` | `warm`, `cool`, `neutral` |
| `--out DIR` | — | Write direct maps (no metallic, no QC) |
| `--forge` | off | Send the albedo through the normal pipeline |

`--out` is a direct map writer for preview/repair. `--forge` is the path that produces a library
material with QC, previews and provenance.

---

## Material projects

```powershell
python -m pbrforge project list
python -m pbrforge project create .\source.png --name "Brick wall" --layers layers.json
python -m pbrforge project show brick_wall --json
python -m pbrforge project update brick_wall --layers layers.json
python -m pbrforge project render brick_wall
python -m pbrforge project delete brick_wall --yes
```

A project is a copied source PNG plus an ordered layer stack. Rendering never replaces the source or
older library materials; it creates a new job.

See [STUDIO.md](STUDIO.md#material-projects) for the layer schema and limits.

---

## Set Doctor

```powershell
python -m pbrforge setdoctor .\incoming --json
python -m pbrforge setdoctor .\incoming --out .\fixed `
  --normal opengl --invert glossiness --repack orm --rename --json
```

| Option | Meaning |
|---|---|
| `--out DIR` | New output folder; omit for scan-only |
| `--normal opengl\|directx` | Flip the normal map's green channel |
| `--invert roughness\|glossiness\|metalness` | Invert a data role |
| `--repack orm\|mra` | Pack AO/roughness/metallic into one image |
| `--rename` | Write canonical map names |
| `--overwrite` | Allow an existing output folder |

Repairs are preflighted **before** any output is created. A failed repack leaves no partial folder,
and the source is never modified.

---

## Delivery

```powershell
python -m pbrforge deliver .\my_material
python -m pbrforge deliver .\my_material --out .\dcc --name "Brick wall" --json
```

Writes `material.mtlx`, `material.gltf`, `material.usda`, `blender_material.py`, copied `textures/`,
`delivery.json` and a README. Missing base roles are reported, never invented.

---

## Library

```powershell
python -m pbrforge library
python -m pbrforge library --query brick --tag exterior --favorite --json
python -m pbrforge library --category brick
python -m pbrforge library --open
python -m pbrforge library --open brick_wall
```

User metadata (tags, favourite, note, physical size, texel density) is stored in `meta.json` beside
the generated `material.json`. Filters search name, slug and tags. Texel density uses the resolution
actually written, not the requested job resolution.

PATCH-style metadata edits are available through the web API:
`PATCH /api/library/<slug>` with `{"tags": [...], "favorite": true, "note": "...",
"size_mm": [2000, 1000]}`.

---

## Watch folders

```powershell
python -m pbrforge watch .\inbox --once
python -m pbrforge watch .\inbox --interval 5 --recursive -v
```

| Option | Default | Meaning |
|---|---|---|
| `--interval SEC` | `5` | Poll interval |
| `--once` | off | Scan once, then exit |
| `--recursive` | off | Include subfolders |
| `--name-prefix TEXT` | — | Prefix every material name |

Identity is the SHA-256 of the file, recorded in `.pbrforge-watch.json`. Renaming does not re-forge;
editing the bytes does. See [STUDIO.md](STUDIO.md#watch-folders).

---

## Plugins

```powershell
python -m pbrforge plugin list
python -m pbrforge plugin run texel-report "C:\materials\brick" --out .\report
python -m pbrforge plugin run my-plugin --dir .\dev-plugins --json
```

Plugins are explicit local Python. They are path-contained and manifest-checked, but **not
sandboxed**. The HTTP server never scans or executes them.

---

## Vault

```powershell
python -m pbrforge vault backup --output .\backups\studio.pbrvault
python -m pbrforge vault inspect .\backups\studio.pbrvault
python -m pbrforge vault verify .\backups\studio.pbrvault
python -m pbrforge vault restore .\backups\studio.pbrvault --output .\restored
python -m pbrforge vault restore .\backups\studio.pbrvault --output .\restored --overwrite
```

| Action | Meaning |
|---|---|
| `backup` (default) | Write an encrypted `.pbrvault` of config + library |
| `restore` | Decrypt into a new folder |
| `verify` | Check header, KDF parameters, chunk authentication and end marker |
| `inspect` | Show size, chunk count, KDF parameters and entry count without the passphrase |

The passphrase is prompted interactively and never stored. AES-256-GCM, scrypt `n=32768`, `r=8`,
`p=1`, 1 MiB authenticated chunks. Restore refuses a non-empty output unless `--overwrite` is given.
See [SECURITY.md](SECURITY.md#2-encrypted-vault).

---

## Release signing

```powershell
python -m pbrforge release keygen --private private.pem --public public.pem
python -m pbrforge release sign .\PBRForge-Setup-2.0.0.exe --key private.pem --output .\PBRForge-Setup-2.0.0.exe.sig
python -m pbrforge release verify .\PBRForge-Setup-2.0.0.exe --signature .\PBRForge-Setup-2.0.0.exe.sig --json
```

`release sign` writes a small JSON document with the Ed25519 signature and the SHA-256 of the exact
file bytes. `release verify` checks both. The update path for v2.0+ requires a valid signature before
auto-install; see [UPDATES.md](UPDATES.md).

---

## Security audit

```powershell
python -m pbrforge security
python -m pbrforge security --json
```

Reports TLS state, pairing state, secret permissions where the platform exposes them, vault key
configuration and the update-signature policy. It reports risks; it does not change your settings.

---

## Updates

```powershell
python -m pbrforge update
python -m pbrforge update --check
python -m pbrforge update --install
```

The check is opt-in and the only outbound network call in the product. For v2.0+ releases, a missing
or mismatched Ed25519 signature blocks auto-install with a named reason.

---

## Diagnostics

```powershell
python -m pbrforge doctor
python -m pbrforge doctor --full
python -m pbrforge selftest -v
python -m pbrforge selftest --quick
python -m pbrforge config
python -m pbrforge config --set port=7788 --set bind=127.0.0.1
python -m pbrforge cache --size
python -m pbrforge cache --prune 30
```

`selftest` is the contract test; `--quick` skips the slow texture checks. `doctor` reports
availability rather than guessing, and never installs anything.

---

## JSON output

Commands that mutate or report data accept `--json` and print exactly one JSON object to stdout.
Progress and warnings go to stderr, so a caller can pipe stdout to a parser safely. The shape is
stable within a major version; keys are added, not renamed, in minor releases.

---

## Isolated runs

```powershell
python -m pbrforge serve --config-dir .\test-cfg --library .\test-lib --cache .\test-cache
python -m pbrforge make .\photo.png --config-dir .\test-cfg --library .\test-lib --json
```

Every path override is honoured by the CLI, the server and the worker. This is how the selftest
avoids touching your real studio.
