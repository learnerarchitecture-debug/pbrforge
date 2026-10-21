# 🧱 PBRForge

### Photo → seamless PBR material, locally, in one pass.

Drop in a photograph of brick, stone, wood, tile or fabric. PBRForge decodes it, removes the
lighting, finds the repeat, heals the seam, derives height, normal, AO, roughness and metalness,
then packs the set for **Enscape, V‑Ray, Corona, Blender, Unreal, Unity, glTF and Lumion**.

No account. No cloud. No telemetry. A local server and a local library, built for one person.

<p>
  <img alt="version" src="https://img.shields.io/badge/version-0.2.0-E0A458">
  <img alt="python" src="https://img.shields.io/badge/python-3.12%2B-3776AB">
  <img alt="runtime" src="https://img.shields.io/badge/runtime-stdlib%20only-2E7D32">
  <img alt="licence" src="https://img.shields.io/badge/licence-personal%20use-8E5CC7">
</p>

---

## ✨ What is in 0.2.0

| Area | State | What it actually does |
|---|---|---|
| 🖼️ **Web app** | ✅ built | Drop a photo, choose preset/polarity, watch every stage, inspect the maps in 2D/WebGL, download the zip, reopen anything from the library |
| ⚙️ **Pipeline** | ✅ built | 15 stages from `ingest` to `qc`; one authoritative height field; seam score recomputed from the packed maps |
| 🎛️ **CLI** | ✅ built | `serve`, `make`, `batch`, `library`, `config --set`, `update`, `doctor`, `selftest`, `stop` |
| 📦 **Jobs + library** | ✅ built | staging → promote → previews → zip → `job.json`; survives a server restart |
| 🔄 **Updates** | ✅ released | GitHub Release check, size/hash verification, opt‑in silent Inno install; the 0.2.0 release publishes the installer and `SHA256SUMS.txt` |
| 🧪 **Tests** | ✅ 18/18 | `selftest`, seam fixture separation, pack contract, synthetic 256/512/1024 end‑to‑end |
| 🪟 **Windows installer** | ✅ built + verified | Per‑user Inno installer; fresh install, in‑place reinstall over a running server, packaged autostart, start/stop and uninstall all exercised |
| 🤖 **AI** | ❌ absent | Deliberately classical. No model downloads, no account, no training |

> **Honest boundary:** the browser can convert any image it can decode (JPEG, PNG, WebP, AVIF,
> sometimes HEIC) to PNG before upload. The Python server and CLI are PNG‑only and refuse anything
> else with a named fix. They never pretend to decode a format they cannot.

---

## 🪟 Windows installer (recommended on Windows)

Download **[PBRForge-Setup-0.2.0.exe](https://github.com/learnerarchitecture-debug/pbrforge/releases/latest)**
and run it. It installs in `%LOCALAPPDATA%\Programs\PBRForge` without Administrator.

- **No Python needed.** The executable, UI, docs and deploy scripts are bundled.
- **Your data is separate.** Config stays in `%APPDATA%\pbrforge`; the library stays in
  `Documents\PBRForge`; uninstalling preserves both unless you explicitly ask it not to.
- **Network options are opt‑in.** The firewall rule and logon autostart are unchecked; the local
  HTTPS certificate is created by default so LAN previews keep WebGPU.
- **Unsigned build.** Windows SmartScreen will show a warning because there is no paid code‑signing
  certificate. Choose **More info → Run anyway**.
- **No PATH change.** Use the Start Menu shortcuts, or run
  `%LOCALAPPDATA%\Programs\PBRForge\pbrforge.exe` directly.

The release also publishes `SHA256SUMS.txt` for a manual checksum check.

---

## 🚀 Quickstart (source, Windows/macOS/Linux)

Requirements: **Python 3.12+**, nothing else. The source route is standard library only.

```powershell
git clone https://github.com/learnerarchitecture-debug/pbrforge.git
cd pbrforge
python -m pbrforge doctor        # ends in "ready to run"
python -m pbrforge selftest      # 18/18
python -m pbrforge serve
```

`serve` prints a local URL and a one‑time pairing code. Open the URL, enter the code if asked, then:

1. **Drop a photo** onto the studio card.
2. Pick a **category** (`Brick` flips height so mortar is recessed; `Unknown` keeps bright‑is‑high).
3. Pick a **resolution** (512, 1024, 2048, 4096; 1024 is the default and a good first run).
4. Press **Forge material**.
5. Watch progress, inspect the lit preview and the seven map views, then **Download .zip**.

Everything lands under the configured library folder, typically:

```text
%USERPROFILE%\Documents\PBRForge\library\<material>\
```

---

## 🧭 Three ways to work

### 1. Web app — the normal way

```powershell
python -m pbrforge serve
```

The UI is a hand‑written, dependency‑free HTML/JS app served from `ui/prototype.html`. It speaks
only to the local server; there is no CDN, external font, analytics call or remote asset.

| Screen | What you can do |
|---|---|
| **Studio** | Drop/upload a photo, preview it, name it, choose preset, category, resolution, height polarity, metalness, seamless and de‑light toggles |
| **Progress** | Stage, percentage, elapsed time, per‑stage notes, cancel/reload guidance |
| **Result** | Lit WebGL preview, 2×2 tiled map views, QC verdict, map downloads, zip download, folder path |
| **Library** | Every finished material, QC badge, dimensions, creation time, reopen detail, download again |
| **Updates** | Current version, latest GitHub release, asset name, explicit **Install** action when available |

### 2. CLI — repeatable and batch‑friendly

```powershell
# One photo, 1024 px, brick height polarity
python -m pbrforge make .\wall.png --name "Red brick" --category brick --resolution 1024

# Everything in a folder, serially, one worker
python -m pbrforge batch .\photos --resolution 1024

# List the library / open one folder
python -m pbrforge library
python -m pbrforge library --open red_brick

# Change a setting and write canonical config.toml
python -m pbrforge config --set update_check=false --set resolution=2048

# Check GitHub (does not download unless you ask)
python -m pbrforge update --check
```

### 3. Library — the output is the product

Each material is a self‑contained folder plus a zip:

```text
<library>/red_brick/
├─ maps/                   six or seven master PNGs (albedo, normal, AO, roughness,
│                          height, metallic, optional opacity/macro)
├─ previews/               browser-sized previews used by the library cards
├─ export/
│  ├─ generic-pbr/         renderer‑ready names, colour spaces and channel packing
│  └─ <other preset>/      one folder per exported preset
├─ material.json           parameters, hashes, provenance, dimensions, QC
├─ qc_report.json          measured metrics + verdict + thresholds
├─ job.json                job identity, timings, notes, map URLs, zip path
├─ README.txt              human‑readable map guide
└─ PBRForge-<name>-<px>.zip
```

`material.json` is the authority. `previews/` and the zip are conveniences and can be rebuilt.
A failed job is never moved into the library; its staging folder is left behind for inspection.

---

## 🧪 The pipeline

```text
ingest → preprocess → lattice → seamless → delight
       → derive_height → derive_normal → derive_ao
       → derive_rough → derive_metal → variation
       → alphamatte → upscale → pack → qc
```

| Stage | Decision it owns | Honest limit |
|---|---|---|
| **ingest** | Decode PNG, separate alpha into an `opacity` input, record source class | PNG only in Python; browser handles the rest |
| **preprocess** | White balance, guarded levels, denoise/de‑JPEG, square/content crop, resize | Auto‑levels is **off by default** because aggressive stretching can crush brick shadows |
| **lattice** | Find repeat periods and confidence | Below 0.75 confidence it snaps the crop size only; it does not pretend to know the exact tile |
| **seamless** | Lattice snap, offset search, band heal, mirror fallback | Reports the measured seam score; never says “seamless” without the number |
| **delight** | Remove low‑frequency illumination, restore mean luminance | A single photo cannot recover true albedo; the residual is reported |
| **derive_height** | One height field from the de‑lit image | `brick` defaults to inverted (light mortar is recessed); `auto/normal/invert` is explicit |
| **derive_normal** | Sobel normal in OpenGL tangent space | Not a scanned normal map; derived from the same height field by construction |
| **derive_ao** | Multi‑scale AO on a high‑pass height field | Dark albedo is not treated as a cavity |
| **derive_rough** | Hybrid relief/cavity/specular evidence, roughness or glossiness mode | One photo is weak evidence; distribution is reported |
| **derive_metal** | explicit zero / one / painted mask | `auto` returns dielectric with confidence 0 rather than guessing |
| **variation** | Non‑tiling macro field + subtle albedo/roughness variation | Deliberately not tileable; exported as a separate map |
| **alphamatte** | Pass through real alpha; never invent a matte | Feathering and edge decontamination are future work |
| **upscale** | Declared disabled path | No silent bilinear substitute masquerading as super‑resolution |
| **pack** | Nine presets, filenames, colour spaces, channel packing | EXR and JPEG are refused; data maps never receive sRGB |
| **qc** | Recompute seam score, clipping, normal unit/blue/XY, roughness distribution | A warning is shown; a fail is not hidden behind a green badge |

### Seam metric

For a wrap edge and interior samples:

```text
e = max(1, wrap / p95(interior))
score = round(100 / e)
```

`e = 1.00` → `100/100`. The self‑test fixtures pin the scale: ramp `192×192` goes from `e=191` to
`e=1`; flat `32×32` and the synthetic brick both score `100`. A broken tile must separate from a
healed tile by a wide margin, not by a rounding error.

---

## 🎨 Height, AO and metal: the three places tools usually lie

| Problem | PBRForge behaviour |
|---|---|
| **Brick mortar looks raised** | Choosing category `Brick` inverts the height field by default. Light mortar becomes low; bricks become high |
| **Dark bricks look like holes in AO** | AO is computed from a high‑pass height field, so albedo darkness alone cannot create a cavity |
| **Every photo is called metal** | `auto` metalness returns `0.0` with confidence `0` and a warning. Choose `none`, `full` or paint a mask |
| **Auto‑levels ruins a photo** | Off by default in the UI, API and CLI. Enable it deliberately; QC reports clipped whites |
| **“Seamless” but visibly tiled** | The seam score is recomputed from the packed export and written to `qc_report.json`; the UI shows the verdict |

---

## 🔄 Updates, without a background phoning‑home service

The design is deliberately small:

1. `pbrforge update --check` (or the UI button) makes **one** GET to the GitHub Releases API.
2. The version is compared semantically with `pbrforge.__version__`.
3. If a newer release exists, PBRForge selects the expected asset
   `PBRForge-Setup-<version>.exe` — never “the first exe in the list”.
4. On explicit install, it verifies the advertised SHA‑256 when supplied and always verifies size.
5. It launches the Inno Setup installer silently, then exits so Windows can replace files. The server
   stays stopped after a silent update; start PBRForge again from the Start Menu or logon task.

Settings live in `config.toml`:

```toml
[updates]
check           = true     # allow an explicit/manual check
auto_install    = false    # default: never install without a click
repo            = "learnerarchitecture-debug/pbrforge"
```

There is no telemetry, no background upload, no mirror fallback, and no “update failed, deleted your
working copy” path. The 0.2.0 release is published; a check from this version reports that it is
current:

```text
current  0.2.0
latest   0.2.0
status   up to date
```

If a release is missing or has no installer asset, the tool says so by name instead of downloading
something arbitrary.

---

## 🧰 CLI reference

```text
pbrforge serve                    start the local web app (default with no args)
pbrforge make PHOTO.png           convert one PNG
pbrforge batch FILE_OR_DIR...     convert a folder serially
pbrforge library [--open SLUG]    list/open finished materials
pbrforge config --set KEY=VALUE   persist settings (repeatable)
pbrforge update [--check|--install]
pbrforge doctor                   environment check
pbrforge selftest                 18/18 end‑to‑end checks
pbrforge stop                     the single supported stop path
pbrforge url | code | discover    LAN helpers
pbrforge tls init                 local HTTPS for LAN clients
```

Useful `make` flags:

```text
--preset generic-pbr|enscape|vray-6|corona|blender|unreal|unity|gltf|lumion
--category unknown|brick|marble
--resolution 512|1024|2048|4096
--metal auto|none|full
--height auto|normal|invert
--auto-levels --white-balance --square
--no-seamless --no-delight
--open --json
```

---

## 🗂️ Repository map

```text
pbrforge/                  Python package (authoritative)
├─ core/                   params, typed image, PNG codec, colour gate, ops, pipeline
├─ stages/                 the 15 stages, each with declared inputs/outputs
├─ export/                 presets and writers
├─ server/                 stdlib HTTP server, auth, discovery, upload/job/library/update API
├─ jobs.py                 staging, worker, promotion, previews, zip, job records
├─ update.py               GitHub release check, verification, silent installer launch
├─ cli.py                  make/batch/library/config/update/serve/doctor/selftest
└─ config.py               canonical TOML load/save and update settings
ui/prototype.html          the dependency-free web app
docs/                      design, pipeline, presets, QC, deployment and API notes
installer/                 Inno Setup source used for the 0.2.0 installer
deploy/                    portable/LAN/service build and install scripts
```

The root‑level `cli.py`, `config.py`, `core/`, and `server/` directories from the earlier scaffold
have been removed. `pbrforge/` is the only implementation.

---

## 📚 Documentation

| Read this | When |
|---|---|
| **[docs/WEB_APP.md](docs/WEB_APP.md)** | Using the web app, API routes, job lifecycle, library layout, update controls |
| **[docs/CLI.md](docs/CLI.md)** | Exact command syntax and examples |
| **[docs/UPDATES.md](docs/UPDATES.md)** | Release check, verification and installer contract |
| **[docs/PIPELINE.md](docs/PIPELINE.md)** | Algorithm design, every stage and parameter |
| **[docs/PRESETS.md](docs/PRESETS.md)** | Renderer setup and map conventions |
| **[docs/QC_METRICS.md](docs/QC_METRICS.md)** | Metric formulas, thresholds and what “warn” means |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | How the pieces fit together |
| **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | Symptom → cause → fix |
| **[INSTALLATION.md](INSTALLATION.md)** | Windows installer, source setup and environment notes |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | LAN, HTTPS, firewall and service notes |
| **[CHANGELOG.md](CHANGELOG.md)** | What changed and what is still planned |

### Known limits in 0.2.0

- **PNG only** in the Python pipeline. The browser converts what it can decode before upload.
- **One worker by default.** 1024 px synthetic brick ≈ 43 s; 512 px ≈ 10 s; 256 px ≈ 3 s on the test machine.
- **No real photograph has been calibrated yet.** The synthetic brick fixture and metric vectors are green; photographic tuning is the next honest step.
- **The installer is unsigned**, and the fresh-install/reinstall/uninstall cycle was exercised on one Windows 11 machine. It has not been tested on a second PC.
- **No newer release exists yet**, so the update path has been checked against the GitHub API and the published asset, but it has not installed a real version-to-version upgrade.
- **AI upscale, matting and seam refinement are absent by design**, not silently stubbed.

---

## 🛡️ Privacy and licence

- Files stay on this machine unless you explicitly open the GitHub update check.
- No accounts, analytics, telemetry, model downloads or training on your images.
- **Personal use only.** See [LICENSE.txt](LICENSE.txt).

---

<p align="center"><em>Made for a personal texture library. The export is the product.</em></p>
