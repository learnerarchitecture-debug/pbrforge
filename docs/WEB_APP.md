# 🌐 PBRForge web app

The web app is the normal way to use PBRForge. It is a static HTML/JavaScript application served by
the local Python server, with no build step, no framework and no external assets.

The simple front door is Auto Forge: drop a photo, set a job, forge. A **Studio Lab** section adds
Capture, Surface Lab, Projects, Set Doctor, Delivery and Vault & audit behind one toggle when you
want the deeper controls. Both run through the same local worker.

---

## Start and stop

```powershell
python -m pbrforge serve
```

The server prints:

- a local URL (`http://127.0.0.1:7788` by default);
- a **pairing code** when `server.auth` is enabled.

Open the URL, enter the code once, and the device receives a session cookie. Use `pbrforge stop` from
any terminal to stop the server. Do not kill the process that appears to own the terminal; the
lifecycle code tracks the real socket owner.

Writes require a CSRF token. The UI fetches it from `GET /api/session` and sends it with every
state-changing request. API clients must do the same; a missing or mismatched token is refused with
`403`. Requests are also rejected when the `Host` header does not match the configured address
(DNS-rebinding guard) or when a real `Origin` does not match the server. A form post that reports
`Origin: null` — seen in some privacy-hardened Chromium shells — is accepted only when
`Sec-Fetch-Site` is not `cross-site`.

| Setting | Default | Effect |
|---|---:|---|
| `server.port` | `7788` | HTTP port |
| `server.https_port` | `7443` | HTTPS port when TLS is enabled |
| `server.bind` | `0.0.0.0` | LAN binding; use `127.0.0.1` for this machine only |
| `server.auth` | `true` | Pairing code + cookie sessions |
| `server.tls` | `false` | HTTPS; required for some browser APIs over a LAN |
| `server.max_upload_mb` | `512` | Hard upload cap |

```powershell
python -m pbrforge config --set bind=127.0.0.1 --set port=7788
python -m pbrforge serve
```

---

## The Studio flow

### 1. Choose or drop a photo

The file input and the drop zone accept any image the browser can decode. The app converts it to a
PNG in a canvas before upload:

| Input | Behaviour |
|---|---|
| PNG | Sent as PNG; alpha is preserved |
| JPEG, WebP, AVIF | Decoded by the browser, re-encoded as PNG |
| HEIC/HEIF | Works only when that browser exposes a decoder; the Python side cannot decode it |
| Anything undecodable | Named error before any upload; no silent fallback |

The upload is capped at 2048 px on the long edge for 512/1024/2048 output, and 4096 px only when the
4096 resolution is selected. This keeps a phone photo from becoming a 20 MB request.

### 2. Set the job

| Control | Default | Why it exists |
|---|---|---|
| **Name** | Source stem | Library folder and material title; slugified for filenames |
| **Preset** | `generic-pbr` | Chooses renderer-specific names and channel packing |
| **Category** | `unknown` | `brick` inverts height by default (mortar is recessed); `marble`/`unknown` keep bright-is-high |
| **Resolution** | `1024` | Working long edge; 512/1024/2048/4096 |
| **Height** | `auto` | `auto`, `normal`, or `invert`; category supplies the auto default |
| **Metalness** | `auto` | `auto` honestly returns dielectric + confidence 0; `none`/`full` are explicit |
| **Seamless** | on | Runs the lattice/offset/band-heal cascade |
| **De-light** | on | Removes low-frequency illumination before deriving height |
| **Auto levels** | **off** | Deliberate: aggressive percentile stretching can crush brick shadows |
| **White balance** | off | Grey-world/white-patch correction when the photo has a cast |
| **Square crop** | off | Centre-crop before resizing |

The UI sends these as query parameters to `POST /api/jobs`; the PNG bytes are the request body.

### 3. Watch progress

The UI polls `GET /api/jobs/<id>` and shows:

- current stage and human-readable message;
- percentage, elapsed time and stage boundaries;
- notes and warnings as the pipeline produces them;
- a hard error with the stage and the fix, instead of a silent fallback.

Progress is deliberately coarse. The pipeline reports stage transitions, not a fake smooth bar.

### 4. Inspect the result

The result panel shows:

| View | What is rendered |
|---|---|
| **3D** | A WebGL lit preview using albedo, normal, roughness, AO and metallic; tiled with `fract(uv*2)` |
| **Albedo / Normal / Roughness / Metallic / AO / Height** | 2×2 tiled map views; click a card for a full lightbox |
| **QC** | Verdict (`pass`, `warn`, `fail`) and measured reasons |
| **Stats** | Resolution, map count, job id, forge time (or `—` for a library record with no duration) |
| **Actions** | Download the zip, copy the folder path, open the material again later |

The 3D preview is a preview, not a renderer certification. Use the exported maps in your renderer for
final judgement.

### 5. Library

`GET /api/library` lists finished materials. Each card shows the QC badge, preset, the resolution
**actually written**, creation time, tags, note, favourite state and derived texel density when a
physical size is set. Clicking a card loads the material detail from disk, so the library survives a
server restart and a missing in-memory job.

The **Edit** action writes tags, a note and physical width/height to the sidecar `meta.json`; it does
not touch generated provenance. Filters cover text, tag, category and favourites. See
[STUDIO.md](STUDIO.md#library-metadata-and-calibration).

---

## Studio Lab

Studio Lab is progressively disclosed: the Auto Forge flow is unchanged, and the lab stays closed
until you open it. Each panel has its own status line and refuses unsupported operations with a name.

| Panel | What it does | Honest limit |
|---|---|---|
| **Capture stack** | Upload 2–12 PNG frames, merge with median/mean/min/max and optionally forge | No geometric alignment or EXR merge |
| **Surface Lab** | Generate a tileable procedural source; preview locally; Forge through the pipeline | Direct maps have no metallic map until one exists |
| **Projects** | Create an ordered layer stack and render it | Ordered stack, not a node graph |
| **Set Doctor** | Scan or repair a texture set into a new folder | Filename role guesses can be wrong |
| **Delivery** | Write MaterialX, glTF, USD and Blender files | Not a vendor certification |
| **Vault & audit** | Encrypted backup/restore and a local security audit | Passphrase loss is permanent |

Full behaviour and limits: [STUDIO.md](STUDIO.md). Vault and signature details:
[SECURITY.md](SECURITY.md).

---

## Job lifecycle

```text
upload PNG
  → validate signature, size and content type
  → staging folder under <cache>/staging/<job-id>/
  → one worker runs the 15-stage pipeline
  → promote:
       export/maps          → <library>/<slug>/maps
       export/export/<preset> → <library>/<slug>/export/<preset>
       material.json        → <library>/<slug>/material.json
       qc_report.json       → <library>/<slug>/qc_report.json
  → write previews, README.txt and the zip
  → write job.json
  → mark done
```

A failed job is **never** promoted. Its staging folder remains for inspection and its error is
reported with the failing stage. The zip deliberately excludes `job.json`; the record lives beside
the material and points at the zip.

### Library layout

```text
<library>/<slug>/
├─ maps/                    master PNGs
├─ previews/                UI-sized PNGs
├─ export/<preset>/         renderer-ready files
├─ material.json
├─ qc_report.json
├─ job.json
├─ README.txt
└─ PBRForge-<slug>-<px>.zip
```

The exact filenames and channel packing come from the preset. See
[PRESETS.md](PRESETS.md) for the renderer contract.

---

## HTTP API

The API is same-origin and authenticated by the pairing cookie. State-changing requests also need the
session-bound CSRF token from `GET /api/session`; cross-origin writes are refused with `403`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/ping` | Liveness and version (the only unauthenticated endpoint) |
| `GET` | `/api/session` | Session state and CSRF token |
| `GET` | `/api/health` | Server/job health |
| `GET` | `/api/presets` | Available export presets |
| `GET` | `/api/security` | Local security audit |
| `POST` | `/api/jobs?...` | Upload PNG bytes, create a job; `202` with `{job}` |
| `GET` | `/api/jobs/<id>` | Job record; works after restart via `job.json` |
| `GET` | `/api/jobs/<id>/map/<kind>` | Preview map (`albedo`, `normal`, …) |
| `GET` | `/api/jobs/<id>/download` | Result zip |
| `POST` | `/api/capture` | Multipart capture stack merge (and optional forge) |
| `GET`/`POST` | `/api/surface` | Surface preview or Forge |
| `GET` | `/api/projects` | List material projects |
| `POST` | `/api/projects` | Create a project |
| `GET`/`POST` | `/api/projects/<id>` | Show or update a project |
| `POST` | `/api/projects/<id>/render` | Render a project through the pipeline |
| `GET` | `/api/projects/<id>/source` | Project source PNG |
| `POST` | `/api/vault` | Encrypted vault backup (streams the `.pbrvault`) |
| `GET` | `/api/library` | Finished materials; supports query/tag/category/favorite filters |
| `GET` | `/api/library/<slug>` | Material detail, maps and links |
| `PATCH` | `/api/library/<slug>` | Metadata: tags, favourite, note, `size_mm` |
| `GET` | `/api/library/<slug>/map/<kind>` | Library preview map |
| `GET` | `/api/library/<slug>/download` | Library zip |
| `POST` | `/api/library/<slug>/delivery` | Write DCC delivery files |
| `GET`/`POST` | `/api/library/<slug>/doctor` | Scan or repair a material's texture set |
| `GET` | `/api/update` | Cached release check; `?check=1` forces one |
| `POST` | `/api/update/install` | Download and launch a verified installer |
| `POST` | `/api/settings` | Persist `update_check` / `update_auto_install` |
| `POST` | `/api/logout` | Clear the session cookie |

### Upload parameters

| Query key | Values | Default |
|---|---|---|
| `name` | text | `material` |
| `filename` | source filename | `upload.png` |
| `preset` | preset name | config default |
| `category` | `unknown`, `brick`, `marble` | `unknown` |
| `resolution` | `256`–`4096` | `1024` |
| `metal` | `auto`, `none`, `full` | `auto` |
| `height` | `auto`, `normal`, `invert` | `auto` |
| `seamless` | `0`/`1` | `1` |
| `delight` | `0`/`1` | `1` |
| `auto_levels` | `0`/`1` | `0` |
| `white_balance` | `0`/`1` | `0` |
| `square` | `0`/`1` | `0` |

Example (after pairing and saving the cookie):

```bash
curl -X POST "http://127.0.0.1:7788/api/jobs?name=Red%20brick&category=brick&resolution=1024" \
  -H "Content-Type: image/png" \
  --data-binary @wall.png
```

The response is `202 {"job": {...}}`; poll the `id` from that record.

---

## Updates in the UI

The update dialog shows the current version, the latest release tag, the selected asset and the
release notes. It is honest about the three possible states:

| State | UI message |
|---|---|
| No release published | “no GitHub release exists yet” |
| Release exists, no installer asset | “the latest release has no Windows installer asset” |
| Update available | Version, asset name, size and **Install update** |

The `v2.0.0` release is published, so a fresh check from this version reports **up to date**. The
other messages remain the honest result when the API or asset list does not contain what is needed.
A v2.0+ installer must also carry a valid Ed25519 signature before **Install update** will launch it.

`auto_install = true` means a **manual** check may start the verified installer without another
click. It does not poll in the background. The default is `false`. See
[UPDATES.md](UPDATES.md) for the verification and installer contract.

---

## Logs and troubleshooting

| Symptom | Check |
|---|---|
| Page is blank or 401 | Open `/pair?next=/` and enter the code printed by `pbrforge serve` |
| Upload refused as not PNG | The browser could not decode the source; convert it to PNG first |
| Job fails during ingest | Source is truncated, not actually PNG, or above the configured cap |
| Result is dark / contrasty | Auto levels was probably enabled; regenerate with it off |
| Brick mortar is raised | Choose category **Brick** or height **invert** |
| Update says no release | Correct only if the GitHub API returns none; `v2.0.0` is published, so check the repo setting and network |
| Update refuses to install | Signature missing or mismatched; check `PBRForge-Setup-<version>.exe.sig` on the release |
| Port already in use | `pbrforge stop`, then `pbrforge serve`; do not kill an unrelated PID |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
