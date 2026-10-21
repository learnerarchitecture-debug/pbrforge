# ⌨️ PBRForge CLI

Every command runs from the repository root with `python -m pbrforge`. The CLI is standard-library
only, and the same code powers the web app.

---

## Quick reference

| Command | Use |
|---|---|
| `pbrforge` | Start the local server (same as `serve`) |
| `pbrforge serve` | Start the web app and print its URL/pairing code |
| `pbrforge make PHOTO.png` | Convert one PNG into a material |
| `pbrforge batch FILE_OR_DIR ...` | Convert many PNGs serially |
| `pbrforge library` | List finished materials |
| `pbrforge library --open [SLUG]` | Open the library or one material folder |
| `pbrforge config` | Show effective settings |
| `pbrforge config --set KEY=VALUE` | Persist settings and rewrite `config.toml` |
| `pbrforge update` | Check GitHub for a newer release |
| `pbrforge update --install` | Download and launch the installer |
| `pbrforge doctor` | Check Python, resources, optional dependencies |
| `pbrforge selftest` | Run the 18 end-to-end checks |
| `pbrforge stop` | Stop the server through the supported lifecycle path |

Global flags `--json`, `--config-dir`, `--library`, `--cache`, `--tls/--no-tls` and
`--no-auth` are accepted where they make sense. `--json` prints machine-readable output and
suppresses the human progress lines.

---

## `make`

```powershell
python -m pbrforge make .\photos\brick.png --name "Red brick" --category brick --resolution 1024
```

| Flag | Default | Notes |
|---|---|---|
| `PHOTO.png` | required | Must be a real PNG; the CLI does not convert JPEG/WebP |
| `--name TEXT` | file stem | Library folder and title |
| `--preset NAME` | `generic-pbr` | One of the nine export presets |
| `--category` | `unknown` | `unknown`, `brick`, `marble`; brick flips height |
| `--resolution PX` | `1024` | 256–4096 working long edge |
| `--metal` | `auto` | `auto` returns dielectric + confidence 0 |
| `--height` | `auto` | `auto`, `normal`, `invert` |
| `--auto-levels` | off | Explicitly opt into percentile stretching |
| `--white-balance` | off | Neutralise a colour cast |
| `--square` | off | Centre-crop to a square |
| `--no-seamless` | off | Skip seam healing |
| `--no-delight` | off | Skip illumination flattening |
| `--open` | off | Open the result folder after success |
| `--json` | off | Print the final job record as JSON |

The process exits `0` on success and non-zero on failure. A failure prints the stage and the fix and
leaves the staging folder in place.

Example success:

```text
  source   C:\photos\brick.png
  name     Red brick
  preset   generic-pbr  ·  1024px  ·  worker 20261021-200906-85246d

    2.0%  ingest         decoding the source
   15.2%  preprocess     preprocess: 1024x1024 ...
   ...
   90.0%  publish        moving the packed material into the library

  material Red brick
  folder   C:\Users\me\Documents\PBRForge\library\red_brick
  qc       pass
  time     43.1 s
  zip      C:\Users\me\Documents\PBRForge\library\red_brick\PBRForge-red_brick-1024.zip
```

---

## `batch`

```powershell
python -m pbrforge batch .\photos --resolution 1024
python -m pbrforge batch .\a.png .\b.png --category brick --name-prefix "wall "
```

- Directories are expanded with `*.png` (non-recursive, sorted).
- Non-PNG files are skipped with a named line, not silently.
- Jobs run **serially** with one worker; memory stays predictable.
- `-v/--verbose` shows every stage; the default shows one line per file.
- `--json` prints the array of job records.

The summary is `done/total` plus the failed count. The exit code is `0` only when every attempted
file succeeded.

---

## `library`

```powershell
python -m pbrforge library
python -m pbrforge library --json
python -m pbrforge library --open
python -m pbrforge library --open red_brick
```

The table reads each material's `job.json` and `material.json`; it does not require the server to be
running. `--open` uses the platform opener (Explorer on Windows, `open` on macOS, `xdg-open` on
Linux). Without a slug it opens the library root.

---

## `config`

```powershell
python -m pbrforge config                     # effective settings
python -m pbrforge config --paths             # resolved paths only
python -m pbrforge config --write             # write defaults if missing
python -m pbrforge config --set port=7788 --set update_check=false
```

Accepted keys are the `Config` field names, plus aliases:

| Alias | Field |
|---|---|
| `check` | `update_check` |
| `auto_install` | `update_auto_install` |
| `repo` | `update_repo` |

Values are coerced from the current field type (booleans accept `1/0`, `true/false`, `yes/no`,
`on/off`; integers must parse; paths are expanded). `--set` rewrites the **whole** canonical
`config.toml` and therefore replaces comments. That is deliberate: there is one writer and no
half-merged configuration.

Canonical file:

```toml
[server]
bind            = "0.0.0.0"
port            = 7788
https_port      = 7443
tls             = false
auth            = true
discovery       = true
open_browser    = false
max_upload_mb   = 512

[paths]
library         = "C:\\Users\\me\\Documents\\PBRForge\\library"
cache           = "C:\\Users\\me\\AppData\\Roaming\\pbrforge\\cache"
temp            = "C:\\Users\\me\\AppData\\Local\\Temp\\pbrforge"

[performance]
max_workers     = 0
vram_budget_mb  = 0
cpu_mem_budget_mb = 6144

[defaults]
preset          = "generic-pbr"
resolution      = 4096
deterministic   = true
ai_enabled      = false

[updates]
check           = true
auto_install    = false
repo            = "learnerarchitecture-debug/pbrforge"

[ui]
theme           = "dark"
accent          = "amber"
```

> The config default `resolution = 4096` is used by non-job defaults such as `selftest`. The job API
> and `make` default to **1024** for a usable first run.

---

## `update`

```powershell
python -m pbrforge update --check
python -m pbrforge update --check --json
python -m pbrforge update --install
```

| Flag | Meaning |
|---|---|
| `--check` | The default action; one GitHub Releases API request |
| `--install` | Download the selected installer, verify it, and launch it |
| `--repo OWNER/NAME` | Override `updates.repo` for this run |

The command never installs unless `--install` is passed (or `auto_install = true` is combined with a
manual server check). The published `v0.2.0` release is visible to this check; from 0.2.0 the result
is `up to date`. If no release exists, it prints `no GitHub release exists yet` and exits successfully
— there is nothing to install.

See [UPDATES.md](UPDATES.md).

---

## `doctor`, `selftest`, `stop`

```powershell
python -m pbrforge doctor
python -m pbrforge selftest
python -m pbrforge stop
```

- `doctor` checks Python, resource paths, optional imaging libraries and server state. It ends with
  a clear ready/not-ready line and never installs anything.
- `selftest` is the contract test: 18 checks covering config, auth, PNG, cache, lifecycle, derived
  maps, tileability, colour space, server routes and the pack layout.
- `stop` is the only supported way to stop the server. It verifies the PID owns the socket before
  terminating it and refuses to touch an unconfirmed process.

---

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Success, or check completed with nothing to do |
| `1` | User-actionable failure (missing file, pipeline error, failed batch member) |
| `2` | Invalid arguments (argparse) |

Errors print the stage, the reason and a fix. `-v` is only for unexpected tracebacks.
