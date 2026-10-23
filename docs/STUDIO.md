# PBRForge Studio Lab

Studio Lab is the v2 layer behind the simple Auto Forge door. It is deliberately **not** a node
editor, a renderer, or a cloud workspace. Every operation writes local files and reports what it
actually did.

---

## Capture stacks

### What it does

`capture` merges 2–12 PNG frames of the same subject into one PNG source:

| Mode | Use |
|---|---|
| `median` | Reject transient details such as people or traffic |
| `mean` | Reduce random sensor noise |
| `min` | Keep the darkest sample per channel |
| `max` | Keep the brightest sample per channel |

Each frame is exposure-normalised against the first frame before merging. The merged PNG then goes
through the normal 15-stage pipeline.

### CLI

```powershell
python -m pbrforge capture frame-1.png frame-2.png frame-3.png `
  --out merged.png --mode median --json
```

| Flag | Default | Meaning |
|---|---|---|
| `--mode median|mean|min|max` | `median` | Merge rule |
| `--out PATH` | `merged.png` | Output PNG |
| `--no-normalize` | off | Disable exposure normalisation |
| `--json` | off | Machine-readable result |

### Honest limits

- **PNG only.** Convert JPEG/WebP frames in the browser first, or with an external tool.
- Frames must share one size.
- Exposure alignment only. There is **no geometric alignment, registration or deghosting**; use a
  tripod or a fixed camera.
- This is not an HDR merge and does not create EXR radiance data.

The web UI accepts a multi-file selection and sends a multipart request to `POST /api/capture`.
The server opens the frames, merges them in a temporary folder, submits the merged PNG and returns a
job record.

---

## Surface Lab

### Patterns and controls

| Control | Values |
|---|---|
| Pattern | `brick`, `tile`, `plank`, `panel`, `stone`, `noise` |
| Size | 64–1024 px |
| Weathering | `none`, `dust`, `grime`, `moss`, `wear` |
| Weather amount | 0.0–1.0 |
| Seed | integer |
| Variant | integer |
| Rows / cols | 1–40 / 1–80 |
| Mortar | 0.0–1.0 |
| Hue | `warm`, `cool`, `neutral` |

Brick, tile, plank and panel generators are periodic by construction. Stone and noise use
seam-aware synthesis. Generation is deterministic for the same seed and parameters.

### Two paths

1. **Preview / direct maps** (`surface --out DIR`): writes `albedo.png`, `height.png`, `normal.png`,
   `roughness.png` and `ao.png`. This is a convenience for inspecting or repairing a source; it does
   **not** run QC or add the material to the library.
2. **Forge** (`/api/surface` or **Forge surface** in Studio Lab): sends the generated albedo through
   the normal pipeline, so the resulting library material has the usual QC, previews, zip and
   provenance. The requested working resolution can be larger than the procedural pattern; the
   pipeline records the pixels it actually wrote.

The server does not accept an arbitrary output directory. It writes to its own staging/cache.
A direct map set has no metallic map; Set Doctor therefore refuses to repack it as ORM until a
metallic map exists.

### CLI

```powershell
python -m pbrforge surface --pattern brick --size 512 --seed 42 `
  --weathering dust --weather-amount 0.25 --out .\surface
python -m pbrforge surface --list
```

`--forge` can submit the generated surface to the library through the normal worker.

---

## Material projects

A project is a source PNG plus an **ordered, non-destructive layer stack**. The source is copied
into the project store. Rendering never replaces the source.

### Layer kinds

| `kind` | Parameters | Effect |
|---|---|---|
| `offset` | `x`, `y` integers | Periodic pixel offset |
| `surface` | pattern, size, seed, weathering, weather_amount, variant, rows, cols, mortar, hue, opacity | Blend a generated surface |
| `weather` | mode (`dust`, `grime`, `moss`, `wear`), amount | Add weathering |
| `grade` | brightness, contrast, saturation, warmth in 0–1, neutral 0.5 | Photographic grade |

Layers are validated when written and again when loaded. Unknown kinds and non-object entries are
rejected rather than ignored. Optional blend fields are limited to `normal`, `multiply`, `screen` and
`overlay` where the layer supports them.

### CLI

```powershell
python -m pbrforge project create .\source.png --name "Brick wall" --layers layers.json
python -m pbrforge project list
python -m pbrforge project show brick_wall
python -m pbrforge project update brick_wall --layers layers.json
python -m pbrforge project render brick_wall
python -m pbrforge project delete brick_wall --yes
```

Example `layers.json`:

```json
[
  {"kind": "grade", "brightness": 0.52, "contrast": 0.55, "saturation": 0.5, "warmth": 0.5},
  {"kind": "weather", "mode": "grime", "amount": 0.08}
]
```

`project render` writes a merged PNG and submits it through the normal pipeline. The resulting
library record points back to the project id and the job id. Re-rendering creates a new job; it does
not mutate older library materials.

### Honest limits

- Ordered stack only. No node graph, masks, painting, brush engine, keyframes or history undo.
- Layer order is the edit. There is no per-layer mask channel.
- Project options currently cover preset, category, resolution, metal, height, seamless, delight,
  auto-levels, white balance and square crop.
- `offset` is a periodic pixel shift, not a UV transform for a 3D model.

---

## Set Doctor

Set Doctor scans a folder of PNGs and guesses each file's role from its filename:
`albedo/basecolor/diffuse`, `normal/nrm`, `roughness/rough/glossiness/gloss`,
`metallic/metal/metalness`, `ao/occlusion`, `height/displacement`, `orm/mra`, `opacity/alpha`, and
similar variants. Guessing is explicitly reported; it is not a semantic image classifier.

### Actions

| Action | Result |
|---|---|
| **Scan** | Text/JSON report of roles, sizes, channels and unknown files |
| **Normal flip** | Convert between OpenGL (+Y up) and DirectX (−Y up) |
| **Invert gloss/roughness** | Turn a gloss map into roughness, or the reverse |
| **Invert metalness/metallic** | Normalise the scalar convention |
| **Repack ORM/MRA** | Pack AO, roughness and metallic channels after checking all three exist |
| **Canonical rename** | Write standard role names into the output folder |

### Safe-copy contract

- Output goes to a **new folder**. There is no in-place destructive mode.
- The source folder is never modified.
- Every requested action is preflighted before the output folder is created. A failed ORM repack on
  a set with no metallic map refuses up front and leaves no half-repaired folder.
- Existing non-empty output is refused unless `--overwrite` is explicit.
- Unknown files are copied, not deleted. Unknown roles are reported.

### CLI

```powershell
python -m pbrforge setdoctor .\incoming --json
python -m pbrforge setdoctor .\incoming --out .\fixed `
  --normal opengl --invert glossiness --repack orm --rename --json
```

The server exposes the same scan/repair as `GET`/`POST /api/library/<slug>/doctor`, writing the
repaired set beside the material rather than replacing it.

### Honest limits

- PNG only.
- Filename heuristics can be wrong; review the scan report before trusting the repair.
- It does not infer material identity, remove baked lighting, or reconstruct missing maps.
- Repacking assumes the conventional channel order (AO/Roughness/Metallic for ORM; the reverse for
  MRA). Check your renderer's expectation.

---

## Delivery

`delivery` writes portable interchange files beside a material:

```text
<material>/delivery/
  material.mtlx          MaterialX 1.38
  material.gltf          glTF 2.0 JSON fragment (external PNG textures)
  material.usda          USD UsdPreviewSurface ASCII
  blender_material.py    run from Blender's Text Editor
  textures/              copied PNG maps
  delivery.json          generated files, missing roles and notes
  README.txt             how to keep the folder together
```

The writers **copy the existing PNGs**; they do not re-encode pixels or silently change colour space.
MaterialX and USD can reference separate roughness and metallic maps. glTF 2.0 expects a packed
metallic-roughness texture; PBRForge registers the correct texture role and records a note when a
packed ORM/MRA file is used as-is, because channel order still needs checking in the destination tool.

### CLI

```powershell
python -m pbrforge deliver .\my_material --out .\delivery --name "Brick wall"
python -m pbrforge deliver .\my_material --json
```

Missing base roles are reported in `delivery.json` and in the CLI output. The tool does not invent a
map to fill a gap.

### Honest limits

- A structurally valid interchange file is **not** a vendor certification.
- The glTF output is a fragment, not a `.glb`; embed the external textures if your pipeline wants a
  single binary.
- The Blender script is a starting Principled BSDF graph; review shader details before production.
- There is no round-trip import from a DCC host.

---

## Library metadata and calibration

Generated provenance stays in `material.json`. User edits live in a sidecar `meta.json`:

```json
{
  "created": "2026-10-22T10:00:00Z",
  "updated": "2026-10-22T10:05:00Z",
  "tags": ["brick", "exterior"],
  "favorite": true,
  "note": "Sample wall; 2 m x 1 m",
  "size_mm": [2000, 1000],
  "resolution_px": 1024,
  "texel_density": 0.512
}
```

### Texel density

`texel_density` is derived, not accepted from the client:

```text
texel_density = resolution_px / size_mm[0]      (pixels per millimetre)
```

`resolution_px` is the last resolution **actually written** in `material.json`, not the requested
job resolution. This matters when a 512 px source is asked for at 1024 px: the library reports the
real 512 px map. The UI displays two decimals.

This is a metadata estimate for a square tile, not a UV measurement. It does not know about model
scale, UV islands, padding or a renderer's texel-density convention.

### CLI and API

The CLI supports filtering:

```powershell
python -m pbrforge library --tag brick --favorite
python -m pbrforge library --category brick --json
```

The web UI supports search, tag filters, favourites and an **Edit** action that writes tags, physical
width/height and a note. The API is `PATCH /api/library/<slug>`; the server derives
`resolution_px` and texel density itself. Invalid sizes are rejected.

---

## Watch folders

`watch` polls a folder for new PNGs and submits each unseen file to the normal worker.

```powershell
python -m pbrforge watch .\inbox --once --resolution 1024
python -m pbrforge watch .\inbox --interval 5 --recursive
```

| Behaviour | Detail |
|---|---|
| Detection | Polling `scandir`/`glob`, not OS notifications |
| Identity | SHA-256 of the file, stored in `.pbrforge-watch.json` |
| Rename | Renaming an unchanged file does **not** forge it twice |
| Edit | Editing the bytes changes the digest and triggers a retry |
| Failure | A bad file is remembered with its error; it does not stop the folder |
| State write | Atomic, written before waiting for the long-running job |
| Recursion | Optional with `--recursive` |
| Stop | Ctrl+C; no background service is installed |

This is deliberately boring: OneDrive, SMB shares and network drives disagree about filesystem
events, and a missed event is silent. Polling is portable and explainable.

---

## Plugin example

`plugins/texel-report` is a small local plugin that reports image sizes and the library's texel
density metadata. It demonstrates the explicit execution model:

```powershell
python -m pbrforge plugin list
python -m pbrforge plugin run texel-report "C:\path\to\material" --out .\report
```

Plugins are Python code with your user rights. They are path-contained and manifest-checked, but
**not sandboxed**. The HTTP server never scans or executes a plugin automatically.
