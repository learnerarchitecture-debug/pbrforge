# Features

Two parts:

- **§1** — the build list (what is required, in order).
- **§2** — **suggested features** I would add, ranked, with effort estimates. This is the section
  worth reading if you are deciding what to do next.

Effort key: **S** ≈ an evening · **M** ≈ a weekend · **L** ≈ a few weekends · **XL** ≈ weeks.

---

## 1. Build list

### Phase 1 — Core (get to first real material)

| # | Feature | Notes | Effort |
|---|---|---|---|
| 1.1 | Image wrapper: colour space, bit depth, tiling-safe resize | The foundation. Do it properly or everything downstream inherits the bug. | M |
| 1.2 | Content-addressed cache + stage DAG + re-run from any stage | Makes iteration fast enough to be pleasant. | M |
| 1.3 | `ingest` + PBR-set auto-detection | Multi-file drops are common. | M |
| 1.4 | `preprocess`: crop, WB, levels, vignette, denoise, dejpeg | Boring, high value. | M |
| 1.5 | **Lattice detection + snap-crop** | The flagship. Do it before anything else. | L |
| 1.6 | `seamless`: heal (Poisson/Telea band-limited) + offset search | The reliable fallback when lattice fails. | L |
| 1.7 | `seamless`: spectral for noise-like classes | Needed for concrete, gravel, sand, grass. | M |
| 1.8 | Seam scoring + automatic escalation + honest reporting | The thing that makes it trustworthy. | S |
| 1.9 | `delight` (classical) with mean-luminance restore | | M |
| 1.10 | `derive_height` → `derive_normal` → `derive_ao` → `derive_rough` | The map set. | L |
| 1.11 | `pack` + naming + colour-space hard gates | Correctness lives here. | M |
| 1.12 | `qc` metrics + verdict | | M |
| 1.13 | Presets: **Enscape, V-Ray, Generic PBR** | Only the ones you actually use. | M |
| 1.14 | CLI: `make`, `batch`, `doctor`, `selftest` | | M |
| 1.15 | Library on disk + SQLite index | | S |
| 1.16 | Studio UI: viewport, map stack, controls, export | See [UI_DESIGN.md](UI_DESIGN.md). The prototype is the spec. | L |
| 1.17 | Stage timeline with version history + restore/fork | The trust feature. | M |

**Exit criteria:** you can drop a photo of a tile, get a correct Enscape and V-Ray package, and
the numbers say it is right.

### Phase 2 — Depth

| # | Feature | Notes | Effort |
|---|---|---|---|
| 2.1 | Seam brush + mask editing | When auto fails, 20 seconds of painting should fix it. | M |
| 2.2 | Lattice overlay with drag-to-correct period | Non-negotiable UX per [PIPELINE.md](PIPELINE.md) §1.3. | S |
| 2.3 | Macro variation + detail-map split | The difference between "tileable" and "convincing". | M |
| 2.4 | Alphamatte + decontamination for foliage/decals | | M |
| 2.5 | A/B compare, diff overlay | | S |
| 2.6 | Batch queue UI + per-job warnings | | M |
| 2.7 | Presets: Corona, Blender, Unreal, Unity, glTF, Lumion/Twinmotion | | M |
| 2.8 | Handoff snippets per renderer | High value, trivial to build. | S |
| 2.9 | Command palette (`Ctrl+K`) | | S |
| 2.10 | Detached viewport window | | S |

### Phase 3 — Optional

| # | Feature | Notes | Effort |
|---|---|---|---|
| 3.1 | Quilting / graph-cut synthesis | For structured non-lattice textures. | L |
| 3.2 | AI: tiling-aware super-resolution | Best value/risk ratio of any AI feature. | M |
| 3.3 | AI: seam refinement (constrained diffusion) | Off by default, always labelled. | XL |
| 3.4 | AI: semantic matting | | M |
| 3.5 | AI: classification → auto preset | The "it just works" feature. | M |
| 3.6 | AI: photo → full PBR (intrinsic decomposition) | Ill-posed; ship with the classical fallback. | XL |
| 3.7 | KTX2 / BCn compression | Only if you do realtime work. | M |
| 3.8 | Blender addon | Pull materials straight from the local server. | M |
| 3.9 | Wang tile / variation atlas | For big floors. | L |
| 3.10 | MaterialX / OpenPBR export | Forward-looking. | M |

---

## 2. Suggested features

Things I would build that are **not** in the obvious list. Ranked by
(value ÷ effort) for a single user's own library.

### 2.1 ★ Batch-convert an existing library — `pbrforge convert`

**S.** You already have a texture library. It is probably a mess: spec/gloss sets from an old
workflow, non-POT images, misnamed files, missing rough/metal, 8-bit normals that band, alpha
maps that are actually roughness maps. One command that walks the tree, detects what each file
is, converts conventions, repacks for a target renderer, renames, and writes a report is worth
more on day one than any algorithm.

```powershell
pbrforge convert D:\textures --from auto --to unreal-orm --dry-run
pbrforge convert D:\textures --from specgloss --to metalrough --in-place --backup
```

This is the single highest-value thing on this list, and it needs no new algorithms.

### 2.2 ★ Library audit — `pbrforge audit`

**S.** A project-wide report:

- texel density per material (and per material *band*, if you list your scene's materials)
- missing maps (no normal? no roughness? using flat 0.5?)
- non-power-of-two, oversized, 8-bit normals, JPEG normals
- duplicate and near-duplicate textures (perceptual hash)
- total VRAM footprint at the current streaming budget
- materials whose albedo is outside the plausible range for their category
- materials never used in any scene (candidates for archiving)

Output as a sortable HTML page. Run it monthly. This is the "know your own library" feature that
nobody builds and everybody needs.

### 2.3 ★ Convert a photo *of a render* into a material

**M.** Take a screenshot of a material in a *different* renderer or from a reference image you
found, and back out a usable map set. Slightly ethically grey depending on the source, and
technically the same as the main pipeline — but the use case is real: matching an existing
building's materials from a site photo, or recovering a material from an old project where you
lost the source maps. The honest version of this ships with a note in `material.json` recording
that the source was a render.

### 2.4 ★ "Match to scene" calibration

**M.** Your material looks right in isolation and wrong in the scene. Two causes, both fixable:
albedo mean outside the scene's exposure, or IOR/wetness disagreement with the neighbours.

Feature: pick two or three materials already in your scene, and the tool normalises the new
material's albedo mean and roughness distribution to theirs. Small feature, removes a whole
class of "why does this one look pasted on" problems.

### 2.5 ★ Procedural base-layer library — `pbrforge noise`

**S.** A generator for seamless procedural maps: value/Perlin/worley noise, gradient ramps, brick
and plank patterns, dirt/grunge overlays, water streaks, edge-wear masks, dust. All output as
tileable 16-bit maps.

Why it matters even though you have photos: these are the *blend layers*. A 30% dust overlay and
a 20% grunge multiply takes a clean tile from "obviously CG" to "photographed", and it costs one
slider. You cannot buy this as a drop-in for your specific material.

### 2.6 Variation atlas — "this brick, 6 ways"

**M.** Generate N variants of a material that share an identity but not a pixel: different macro
variation, slightly different roughness distribution, different wear amount, subtle hue drift.
Export as a numbered set. For a large floor, alternate the variants across a UV grid and the
repetition problem largely disappears.

Combine with **Wang tiles** (4 edge types = 256 tiles, but 16 is enough in practice) for a
genuinely repetition-free floor with no extra renderer work.

### 2.7 Smart crop suggestion for non-tileable materials

**S.** For marble, murals and facades — where tiling is impossible — the tool should suggest a
crop that *minimises* the visible seam rather than pretending to fix it, plus the honest
alternative: *"use this as a 2×2 atlas with a randomised UV offset, or a single non-tiling map
on a 3 m panel."* Telling the user what *not* to do is a feature.

### 2.8 Auto-scale from the image

**M.** Estimate real-world size from content: count brick courses and multiply by the standard
module; detect plank width from the number of boards across the frame; use paving slab dimensions.
Then propose a texel density without the user typing anything. Confidence-scored, always
overridable, and it makes 2.5 and the export tips actually usable for someone who does not know
the answers.

### 2.9 Clipboard paste → material

**S.** `Win+Shift+S` a region of a reference image, `Ctrl+V` in the app, get a material. Zero
friction for the "I saw a nice tile in a client's reference PDF" workflow, which is how
archviz actually starts.

### 2.10 Rebuild-from-render check

**M.** Render the material on a sphere with a known HDRI, then compare that render to the source
photo. If they disagree materially, the pipeline got something wrong (usually roughness or
height amplitude). This is a genuinely novel QC idea: **it closes the loop**. It is the difference
between "the maps look plausible" and "the maps reproduce the input", and it is a metric no
competitor has.

### 2.11 Reference pinning

**S.** Pin the source photo, or any reference image, beside the viewport. Half the editing loop is
"does this still look like the thing I photographed", and having the answer visible at all times
removes a lot of context switching.

### 2.12 Eyedropper with dual readout

**S.** Click anywhere in the viewport and get the sRGB value, the linear value, the roughness, the
height, the AO, and the albedo-vs-lighting ratio at that pixel. Cheap to build out of data you
already have, and it is how you debug "why is this patch darker".

### 2.13 Saved views / bookmarks

**S.** Save a camera + tiling + environment combination as "close-up floor spec check",
"wall at 2 m", "grazing angle". One keystroke returns to exactly that QC view. Turns a manual
routine into a checklist.

### 2.14 Project template (`project.pbr.yaml`)

**S.** Per-project naming, resolution, preset, and real-world module.

```yaml
project: Bhikaji_Cama_Palace
preset: vray-6
resolution: 4096
naming: "{project}_{material}_{map}_{res}"
defaults:
  brick_module_mm: 190
  tile_module_mm: 600
  texel_density_target: 3.0
materials:
  - name: lobby_marble
    source: photos/lobby_marble.jpg
    category: marble
    tiling: none            # honest: slab, do not tile
```

Then `pbrforge make --project .\hotel\` processes everything with consistent naming and units.
This is what stops a project drifting into eleven naming conventions.

### 2.15 Watch folder with intent

**M.** Not just "process anything dropped in" — a watched folder with a filename convention that
carries intent:

```
inbox/brick_wall_red__tile__190mm__vray.jpg
                 │       │       └── preset override
                 │       └────────── category hint
                 └────────────────── material name
```

Drop a file named that way and it comes out fully specified, no clicks. Batch overnight, review
in the morning.

### 2.16 Seam "stress test" report

**S.** One button that renders the material at 1×, 4×, 12× tiling, at grazing angle, under all
five environments, and reports the worst case with an FFT repetition peak. Ships as a single PNG
contact sheet. Attach it to the material and you never re-litigate "does this tile?" in six
months.

### 2.17 Anisotropy and direction preservation

**M.** Brushed metal, wood grain, weaves, and drawn curtains have a direction, and generic tiling
destroys it. Detect the dominant orientation from the gradient structure tensor, expose a
"preserve direction" axis lock, and bias the synthesis along it. Without this, every brushed
metal you process comes out wrong in an obvious way.

### 2.18 Worn / wet / dirty generators

**M.** One-click derived variants built from maps you already computed: use cavity for dirt
accumulation, curvature for edge wear, the ambient-occlusion map for water pooling, and a mask
for moss. Real materials are not pristine, and "as-built" is what archviz clients actually want
to see. Roughness changes for wetness too — wet surfaces are less rough, and that is what sells it.

### 2.19 Speed: the "quick look" tier

**S.** A 512 px, half-second pipeline result available the instant a file is dropped, refined to
full resolution in the background. Enormous perceived-performance win for almost no work, because
most decisions are made from the first impression anyway.

### 2.20 Export presets as shareable files

**S.** Presets are already JSON. Make them droppable, versioned, and diffable, so when a renderer
changes you fix one file and re-export. Add a `--check-presets` verb that re-validates every
preset against a sample material and reports what broke.

### 2.21 Nightly reprocess with a diff report

**M.** When you improve an algorithm, you want to know which materials changed and by how much —
but you do not want to lose the old ones. A scheduled run that reprocesses the library, compares
against the previous output, and writes a report ("14 materials improved, 3 changed materially,
2 got worse — here they are side by side") is how a personal tool gets better instead of
accumulating a library of inconsistent results.

### 2.22 Sandbox / "explain this" panel

**S.** A read-only panel showing, for every stage, what it did, why it chose that, the parameters
used, and the metric before/after. Half of learning a tool is understanding what it did to your
image. It also doubles as the bug report — "here is what it decided" is the whole ticket.

### 2.23 Undo that survives a reprocess

**M.** Versioning is in the core, but the *UI* habit is what matters: `Ctrl+Z` should walk back
through pipeline decisions, not just brush strokes. Every stage change is an undoable action with
a name ("seam method → spectral").

### 2.24 Local model warm-pool

**S.** Keep downloaded AI models resident between jobs and unload on a timer. Cold starts of 20+
seconds make AI features feel broken even when they are fast.

### 2.25 `pbrforge explain <metric>`

**S.** A CLI verb that prints how a metric is computed, what this material scored, and what to do
about a bad score. The tool teaching its own QC vocabulary is how you learn to trust the numbers
instead of arguing with them.

---

## 3. Anti-features (do not build)

Kept explicit so they do not creep back in.

| Do not build | Why |
|---|---|
| Accounts, cloud sync, telemetry | Nothing to sync with; and your client textures are nobody's business. |
| A `pbrforge` SaaS | See the original scoping document, §11.3. |
| `.sbsar` export | Requires Adobe's restricted Substance Automation Toolkit licence. |
| Full node-graph authoring | That is Substance Designer. Generate maps, do not become a compositor. |
| Photogrammetry / mesh reconstruction | Different product, 10× the scope. |
| "AI makes it perfect, one click" | It cannot, and the claim destroys trust the first time it is wrong. |
| Training on your own captures without a curated, labelled set | You will produce a worse model than the off-the-shelf one and spend three months doing it. |
| A mobile app | Nobody tiles textures on a phone. |
| Auto-upload for "processing" | Bandwidth, privacy, and it makes the tool useless offline. |
| Progress bars you cannot compute | Use elapsed time. Honest beats pretty. |

---

## 4. What "done" looks like

The tool is finished when this session takes under five minutes and no manual map editing happened:

1. `Ctrl+V` a photo of a tile from a client's reference PDF.
2. It auto-classifies, detects a 4 × 8 course lattice, snaps the crop, derives six maps,
   scores 96, and flags "texel density 1.9 px/mm — low for this viewing distance".
3. You drag the roughness remap slider, check the floor view at 8× tiling, and flip the green
   channel once because the renderer wanted DirectX.
4. You pick V-Ray, 4k, write the MaxScript, and open it in Max — where the material appears with
   correct gamma, correct displacement in millimetres, and a triplanar node already added.
5. Six months later you open the material, read the QC report card, and know exactly why every
   number is what it is.
