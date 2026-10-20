# Automation

The point of automation, for one person, is that you stop doing the boring 60%. Everything here
runs unattended and writes a report you can skim in the morning.

---

## 1. CLI reference

```powershell
pbrforge <verb> [options]
```

### `make` — one material, end to end

```powershell
pbrforge make <input> [--preset enscape] [--out DIR] [--res 4096]
                      [--method auto|lattice|heal|spectral|quilt]
                      [--module 190mm] [--no-ai] [--profile]
```

```powershell
# Simplest possible
pbrforge make "D:\photos\brick.jpg" --preset enscape --out D:\textures\brick

# Real-world module so texel density and displacement are physical
pbrforge make brick.jpg --module 190mm --res 4096 --preset vray

# Force a method because you disagree with the auto choice
pbrforge make concrete.jpg --method spectral --preset vray

# With a per-material override of any parameter
pbrforge make wood.jpg --set delight.strength=0.45 --set roughness.base=0.4
```

### `batch` — a folder

```powershell
pbrforge batch <indir> --out <outdir> [--preset P] [--res N]
                       [--manifest overrides.csv]
                       [--jobs 4] [--dry-run] [--resume] [--skip-existing]
```

```powershell
# Everything in a folder, consistent settings
pbrforge batch D:\photos\kerala_project --out D:\textures\kerala --preset enscape --res 4096

# See what it would do first. Always do this on an unfamiliar folder.
pbrforge batch D:\photos --out D:\textures --dry-run

# Per-file overrides from a spreadsheet
pbrforge batch D:\photos --out D:\textures --manifest overrides.csv
```

`overrides.csv` — only the columns you need:

```csv
file,category,module_mm,method,preset,roughness_base,res
brick_red.jpg,brick,190,lattice,enscape,0.8,4096
lobby_marble.jpg,marble,,none,vray,0.2,8192
oak_floor.jpg,wood,180,lattice,vray,0.4,4096
```

`--resume` is what makes a 300-file overnight batch survivable: a crash at file 217 leaves the
first 216 cached, and the second run starts at 217.

### `convert` — an existing library

```powershell
pbrforge convert <dir> --from specgloss --to metalrough [--in-place --backup]
pbrforge convert <dir> --to unreal-orm --dry-run
```

Detects what each file is, converts conventions, repacks, renames, writes a report. See
[FEATURES.md](FEATURES.md) §2.1 — the highest-value verb in the tool.

### `audit` — a library health report

```powershell
pbrforge audit <dir> [--html report.html] [--min-density 1.0] [--scene dir]
```

Reports texel density, missing maps, non-POT, 8-bit normals, JPEG data maps, near-duplicates
(perceptual hash), total VRAM, implausible albedo, and unused materials. Sortable HTML output.

### `noise` — procedural base layers

```powershell
pbrforge noise perlin --out dirt.png --size 2048 --octaves 5 --seed 1
pbrforge noise grunge --out grunge.png --size 4096 --contrast 0.6
pbrforge noise brick --module 190mm --size 4096 --out brick_mask.png
```

Output is always tileable, always 16-bit. These are the blend layers that make a clean texture
look photographed.

### `stress` — the tiling contact sheet

```powershell
pbrforge stress <material> --out stress.png
```

Renders 1× / 4× / 12× tiling, grazing angle, all five environments, plus an FFT repetition peak
measurement, into one PNG. Attach it to the material and stop re-litigating "does this tile?".

### `explain` — teach the metric

```powershell
pbrforge explain seam
pbrforge explain density --material brick_wall_red
```

Prints how a metric is computed, what this material scored, and what to do about a bad score.

### Housekeeping

```powershell
pbrforge doctor                  # environment readiness, including the live server
pbrforge selftest                # synthetic round-trip, no input files needed
pbrforge stop                    # stop a server started here; --strict exits 1 if none
pbrforge cache --size            # report
pbrforge cache --prune 30d       # drop cached artifacts older than 30 days
pbrforge cache --prune --keep-latest
pbrforge models list|pull|verify
pbrforge studio [--port 7788] [--no-browser]
pbrforge presets --check         # validate every preset against a sample material
pbrforge agent install|status|uninstall
```

`pbrforge stop` is safe to call blindly from a script or a shutdown hook: it exits 0 when there was
nothing to stop, and it refuses to terminate a PID it cannot confirm is PBRForge. Use `--strict`
only when the exit code is the thing you are testing. It is the correct way to stop the server in a
CI job or a build script — **not** `Stop-Process` on the PID returned by `Start-Process`, which is
the PyInstaller bootloader rather than the process holding the port. See `DEPLOYMENT.md` §13.5.

---

## 2. Watch folders

```powershell
pbrforge agent install --watch D:\inbox --out D:\textures
pbrforge agent status
```

A background process (a normal Python process registered as a scheduled task — **not** a Windows
service, because services make debugging miserable) watches a folder and processes anything that
settles.

**Intent in the filename:**

```
brick_wall_red__tile__190mm__vray.jpg
        │         │      │       └── preset override
        │         │      └────────── real-world module
        │         └───────────────── category hint
        └─────────────────────────── material name
```

Anything dropped in `inbox/` comes out fully specified with zero clicks. Files without the
convention get the default preset.

**Stability check:** a file is only processed after its size and mtime stop changing for 3 seconds.
Without this, you will process a half-copied file and get a cryptic decoder error. It is the most
common bug in every watch-folder tool ever written.

**Debounce:** a 500 ms per-path debounce, because copying a folder fires four events per file.

**Failures** are moved to `inbox/_failed/` with a sibling `.error.txt` explaining what happened.

---

## 3. Scheduled runs

Windows Task Scheduler, with the environment variables inlined so the task works regardless of
what the user environment looked like at registration time.

```powershell
# Monthly library audit
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -Command `"cd C:\dev\pbrforge; .\.venv\Scripts\python.exe -m pbrforge audit D:\textures --html D:\reports\audit-$(Get-Date -f yyyy-MM).html`""
$trigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At 9am
Register-ScheduledTask -TaskName "PBRForge Audit" -Action $action -Trigger $trigger

# Nightly inbox drain
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -Command `"cd C:\dev\pbrforge; .\.venv\Scripts\python.exe -m pbrforge batch D:\inbox --out D:\textures --manifest D:\inbox\overrides.csv --resume --skip-existing`""
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
Register-ScheduledTask -TaskName "PBRForge Nightly" -Action $action -Trigger $trigger

# Cache hygiene (weekly)
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -Command `"cd C:\dev\pbrforge; .\.venv\Scripts\python.exe -m pbrforge cache --prune 21d`""
Register-ScheduledTask -TaskName "PBRForge Cache" -Action $action `
  -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am)
```

**Also set, in the task properties:**

- "Run whether user is logged on or not" — no console window flashing.
- "Start the task only if the computer is on AC power" — a laptop will otherwise die mid-batch.
- Do not check "Stop the task if it runs longer than…" for the nightly batch; a 4k export of 300
  textures legitimately takes hours.

---

## 4. Project config

`project.pbr.yaml` in a project root makes every command consistent without repeating flags.

```yaml
project: Bhikaji_Cama_Palace
preset: vray-6
resolution: 4096
naming: "{project}_{material}_{map}_{res}"
conventions:
  normal_orientation: opengl
  roughness_mode: roughness
  units: mm

defaults:
  brick_module_mm: 190
  tile_module_mm: 600
  texel_density_target: 3.0
  delight_strength: 0.7

materials:
  - name: lobby_marble
    source: photos/lobby_marble.jpg
    category: marble
    tiling: none                 # honest — a slab has no repeat unit
    note: "use as 2x2 atlas with randomised UV offset"

  - name: facade_brick
    source: photos/facade_brick.jpg
    category: brick
    module_mm: 190
    res: 8192                    # hero surface, seen close

  - name: corridor_carpet
    source: photos/corridor_carpet.jpg
    category: fabric
    res: 2048                    # background surface
```

```powershell
pbrforge make --project .
```

Then every material in that project gets consistent naming, units and density — which is what
stops a project drifting into eleven naming conventions and four different brick sizes.

---

## 5. Recipes

### Overnight: 300 phone photos → a usable library

```powershell
pbrforge batch D:\photos\2026-10-site-visit --out D:\textures\site \
  --preset enscape --res 4096 --jobs 2 --resume
# jobs 2, not 8: VRAM contention is the bottleneck, not CPU
```

### Audit before a big project, so you actually know your library

```powershell
pbrforge audit D:\textures --html D:\reports\pre-project.html --min-density 1.0
# open it, sort by texel density, fix the worst 20 before the deadline
```

### Convert an old spec/gloss library to modern metal/rough

```powershell
pbrforge convert D:\textures\legacy --from specgloss --to metalrough --in-place --backup
pbrforge audit D:\textures\legacy
```

The backup is a hard-link copy, so it is nearly free on NTFS.

### Enscape set from a client's reference PDF

```powershell
# 1. Win+Shift+S a region → Ctrl+V into the studio UI  (see FEATURES.md §2.9)
# 2. Or, from the command line:
pbrforge make "D:\refs\client_ref_tile.png" --preset enscape --res 2048 --module 600mm
```

### Generate a non-repeating floor

```powershell
pbrforge make marble_tile.jpg --preset vray --res 4096
pbrforge variants marble_tile --count 6 --out D:\textures\marble_variants
# then alternate the variants across the UV grid in the renderer
```

### Nightly reprocess after an algorithm improvement

```powershell
pbrforge batch D:\textures --reprocess --out D:\textures --resume --report D:\reports\reprocess.html
# the report diffs old vs new: improved / unchanged / regressed, with thumbnails
```

### Air-gapped: everything local, nothing downloads

```powershell
# Nothing in the pipeline requires network access. To be certain:
$env:PBRFORGE_OFFLINE = "1"
pbrforge make brick.jpg --preset vray --no-ai
```

---

## 6. Logging and reports

Every run writes two things next to nothing else — you should never have to hunt for output.

**Console** (rich, human-readable, for watching):

```
PBRForge 0.1.0 · brick_wall_red_01 · enscape · 4096
  ✓ ingest        0.4s   4032×3024 sRGB 8-bit  source sha 9f2c1ab4
  ✓ preprocess    1.8s   crop → 3024×3024 · vignette 0.5 · dejpeg 0.3
  ✓ lattice       0.6s   x=4 y=8  conf 0.91  snapped to 3008×3008
  ✓ seamless      0.2s   method=lattice-snap (exact)  seam 98.1
  ✓ delight       0.9s   strength 0.70 · specular 0.50 · mean restored 0.42
  ✓ derive        2.1s   height 16-bit · normal +Y 16-bit · ao 0.6 · rough 0.80
  ⚠ qc            0.3s   texel density 1.92 px/mm — low for close viewing
  ✓ pack          0.6s   5 maps · 5 files · 88 MB
  ● export        0.2s   D:\textures\brick_wall_red\export\enscape

  verdict  warn   seam 98 · structure 0.98 · albedo 0.42 · clip 0.03%
  total 6.9s   (cached stages: 0)
```

**Machine-readable** (`--json` or automatically into `material.json`): every stage's parameters,
timings, metrics, warnings, `pipeline_hash`, and AI provenance. This is what makes a bug
reproducible and what `pbrforge explain` reads.

**Failure format** — always four lines, always useful:

```
✗ derive_normal failed after 3.2s
  reason   VRAM exhausted (needed 1.4 GB, 0.6 GB free)
  stage    derive_normal · bit_depth 16 · 8192×8192
  fix      retry at 4096, or close the browser preview, or run with --cpu
  kept     maps from v003 are untouched
```

---

## 7. Resumability rules

- Each stage's artifact is written to a temp path and atomically renamed on success, so a crash
  never leaves a half-written artifact in the cache.
- The cache key includes the stage, its params hash, and its upstream artifact hashes. A crash
  mid-batch means the next run reuses everything already computed.
- `--resume` skips completed materials entirely; `--skip-existing` skips only if the output exists
  *and* its `material.json` matches the current pipeline hash.
- The batch writes a progress file (`batch.state.json`) so an interrupted run can be continued even
  after a reboot.
- Cancelling is safe at any point: the worst case is losing one material's current stage.

---

## 8. The scheduled-task gotchas

Learned the hard way, worth writing down:

| Symptom | Cause | Fix |
|---|---|---|
| Task runs but does nothing | `PATH` and venv activation are per-user and not present in the task context | Use the full path to `python.exe` in the venv; never rely on activation |
| Works manually, fails as a task | Working directory defaults to `System32` | `cd` in the command, or set "Start in" |
| GPU not used in a scheduled run | Some drivers need an interactive session for CUDA context creation | Run the task as the logged-in user, or accept CPU on that job |
| Task silently killed at the 72-hour limit | Default "stop after" setting | Uncheck it for batch jobs |
| Laptop dies mid-batch | Service ran on battery | "Start only if on AC power" |
| Two runs overlap and fight over the cache | No mutex | The cache uses file locking; the second run blocks with a clear message rather than corrupting |
