# UI Design

> **Design reference, not a shipped-feature list.** The 2.0.0 interface is
> [`../ui/prototype.html`](../ui/prototype.html): Auto Forge plus Studio Lab, with a WebGL
> lit preview. Sections below that discuss WebGPU, node graphs or local AI describe proposals
> and were not implemented. See the status table in [README.md](../README.md).

The interface is the product. The algorithms are published papers; the thing you actually live
with every day is whether the tool tells you the truth quickly and gets out of the way.

**Live reference implementation:** [`../ui/prototype.html`](../ui/prototype.html) — open in
Chrome/Edge. It is not a mockup: it generates real height data, derives a real normal map with a
Sobel filter, and shades a real PBR sphere with the actual maps, so every slider behaves like the
shipped tool should.

---

## 1. Design principles

| # | Principle | What it means in practice |
|---|---|---|
| 1 | **Viewport is king** | The 3D preview never shrinks below 45% of the window width. Panels collapse before it does. |
| 2 | **Numbers, not vibes** | Every quality claim shows a number next to it (seam score, texel density, clip %). "Looks good" is not a status. |
| 3 | **Never lie about state** | No fake progress bars. If a stage is slow, say which stage and how long. If a result is a guess, badge it. |
| 4 | **Three steps by default, forty knobs if you want** | Upload → Adjust → Export. Everything else lives behind `Advanced`. |
| 5 | **Every action is undoable and non-destructive** | Edits create versions. Nothing overwrites. `Ctrl+Z` always works. |
| 6 | **Dark, because textures are the subject** | A dark neutral UI is the only honest way to judge a texture's brightness. No tinted chrome near the viewport. |
| 7 | **Keyboard-first** | Every control reachable without the mouse. A `Ctrl+K` command palette for everything not on screen. |
| 8 | **Show the failure loudly** | Bad seam → the viewport jumps to the worst spot and the score pill goes amber/red. |
| 9 | **No modal hell** | Only two modals exist: Export and Settings. Everything else is a panel or an inline popover. |
| 10 | **Latency honesty** | Dragging a slider updates a 1/4-res preview instantly; the full-resolution recompute is queued and shown as a pending badge, never blocking input. |

---

## 2. Layout

Three resizable columns, a top bar, a bottom timeline. Minimum useful window: **1440 × 900**.
Below that, the right panel collapses to an icon rail.

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│  ◆ PBRForge   brick_wall_red_01  ▾        [ Enscape ▾ ]   ● 92  Seam 94  ✓ QC        Export │  ← Top bar  56px
├──────────────┬──────────────────────────────────────────────────┬──────────────────────────┤
│ MAP STACK    │  ┌────────────────────────────────────────────┐  │  SEAM                     │
│              │  │                                            │  │   Method   [ Auto ▾ ]     │
│ ◉ Albedo     │  │                                            │  │   Heal     ──●────── 0.72 │
│ ◉ Height     │  │              VIEWPORT                      │  │   Band     ───●───── 96px │
│ ◉ Normal  ⁺  │  │                                            │  │   Lattice  [x] auto       │
│ ◉ Roughness  │  │        (sphere / plane / floor /           │  │                           │
│ ◉ Metallic   │  │         tiling grid / seam heat / A-B)     │  │  DE-LIGHT                 │
│ ◉ AO         │  │                                            │  │   Strength ──●────── 0.70 │
│ ○ Emissive   │  │                                            │  │   Specular [x]            │
│              │  └────────────────────────────────────────────┘  │                           │
│ ── SOURCE ── │  [ Sphere ][ Plane ][ Floor ][ 3×3 ][ 8×8 ][Heat][A|B]  1× ──●──  8×       │  ← Viewport toolbar
│ ▣ brick.jpg  │                                                  │  RELIEF                   │
│              │                                                  │   Strength ──●────── 1.00 │
│              │                                                  │   Amplitude ──●───── 3.0mm│
├──────────────┤                                                  │                           │
│ PIPELINE     │                                                  │  SURFACE                  │
│ ✓ ingest     │                                                  │   Roughness ──●───── 0.85 │
│ ✓ lattice    │                                                  │   Metallic  ──●────── 0.00│
│ ✓ seamless   │                                                  │   AO        ──●────── 0.60│
│ ✓ delight    │                                                  │                           │
│ ● derive     │                                                  │  ▸ Advanced (38)          │
│ ○ qc         │                                                  │                           │
│ ○ export     │                                                  │                           │
├──────────────┴──────────────────────────────────────────────────┴──────────────────────────┤
│ ⚠ Repetition visible at 8× tiling · texel density 1.9 px/mm (low) · 3 warnings            │  ← Status bar 28px
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

**Panel behaviour**

- Left column: 240 px default, collapsible to 48 px icon rail.
- Right column: 300 px default, collapsible to 48 px.
- Both resizable by dragging the gutters. Double-click a gutter to reset.
- Split positions persist per project.
- The viewport toolbar is centered, pill-shaped, and always visible.

---

## 3. Design tokens

Defined once, referenced everywhere. In the prototype these are CSS custom properties; in the app
they become a theme object.

### 3.1 Colour

The palette is deliberately **desaturated and neutral**. Anything with chroma competes with the
texture you are judging. Only two chromatic accents exist, and each means exactly one thing.

```css
/* Surfaces — a 5-step ramp, no pure black anywhere */
--bg-base:      #0B0D10;   /* window background              */
--bg-panel:     #12151A;   /* panels, rails                  */
--bg-elevated:  #191D24;   /* cards, inputs, popovers        */
--bg-hover:     #1F242C;   /* row hover                      */
--bg-active:    #262C36;   /* pressed / selected             */
--bg-viewport:  #08090B;   /* behind the 3D preview          */

/* Lines */
--border-subtle:#1C2129;
--border:       #242A33;
--border-strong:#333B47;

/* Text */
--text-primary:  #E6E9EF;
--text-secondary:#A6AEBC;
--text-muted:    #6E7787;
--text-disabled: #464D59;

/* Accent — AMBER. Means "this is the primary action / the live value".
   Chosen over blue because it reads as material/light, and because every
   other tool in this space uses blue. */
--accent:        #E0A458;
--accent-hover:  #F0B468;
--accent-active: #C88F45;
--accent-dim:    rgba(224,164,88,0.14);
--accent-border: rgba(224,164,88,0.35);

/* AI — VIOLET. Reserved exclusively for AI-assisted operations.
   If it is violet, a model produced it, and it is optional. */
--ai:            #A78BFA;
--ai-dim:        rgba(167,139,250,0.14);
--ai-border:     rgba(167,139,250,0.35);

/* Semantic */
--ok:            #5FD08A;
--ok-dim:        rgba(95,208,138,0.14);
--warn:          #E8B65C;
--warn-dim:      rgba(232,182,92,0.14);
--fail:          #F2707A;
--fail-dim:      rgba(242,112,122,0.14);
--info:          #6BB8E8;

/* Map-type identity colours — used for map badges and pipeline chips only.
   Never for text, never for buttons. */
--map-albedo:    #E8A0A0;
--map-height:    #B0B8C4;
--map-normal:    #8FB8E8;   /* blue-ish is normal-map convention */
--map-roughness: #D6C08A;
--map-metallic:  #C8C8D0;
--map-ao:        #9CBFA0;
--map-opacity:   #C0A8D8;
```

**Prohibited:** pure `#000` or `#FFF` for any surface or text. Gradients behind the viewport.
Any accent colour on a label that is not a state.

### 3.2 Type

```css
--font-ui:   "Inter", "Segoe UI Variable", system-ui, sans-serif;
--font-num:  "JetBrains Mono", "Cascadia Mono", ui-monospace, monospace;  /* numbers & paths */
```

| Token | Size / line | Weight | Use |
|---|---|---|---|
| `--t-display` | 20 / 26 | 600 | Screen titles |
| `--t-title` | 15 / 20 | 600 | Panel headings |
| `--t-body` | 13 / 18 | 400 | Default text |
| `--t-label` | 12 / 16 | 500 | Field labels, tabs |
| `--t-small` | 11 / 14 | 400 | Badges, metadata, paths |
| `--t-micro` | 10 / 12 | 600, +0.04em | Section caps (`SEAM`, `SURFACE`) |

Rules: numbers are **always** `--font-num` so digits do not shift while dragging a slider
(`font-variant-numeric: tabular-nums` is not enough — proportional UI fonts jitter). Numeric
values are right-aligned. Units are `--t-small` and `--text-muted`.

### 3.3 Space, radius, elevation

```css
--s-1: 4px;  --s-2: 8px;  --s-3: 12px;  --s-4: 16px;
--s-5: 24px; --s-6: 32px; --s-7: 48px;   /* 4px base grid, no exceptions */

--r-sm: 4px;   /* badges, inputs */
--r-md: 6px;   /* buttons, cards */
--r-lg: 10px;  /* panels, modals */
--r-full: 999px; /* pills, viewport toolbar */

--shadow-panel: 0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.45);
--shadow-pop:   0 12px 32px rgba(0,0,0,0.6), 0 0 0 1px var(--border);
--shadow-focus: 0 0 0 2px var(--bg-base), 0 0 0 4px var(--accent);
```

### 3.4 Motion

```css
--dur-instant: 80ms;    /* hover, press          */
--dur-fast:    140ms;   /* panel expand, tooltip */
--dur-base:    220ms;   /* modal, tab switch     */
--dur-slow:    340ms;   /* viewport mode change  */
--ease-out: cubic-bezier(0.22, 1, 0.36, 1);
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
```

Motion rules: never animate a value the user is dragging. Never animate texture data.
`prefers-reduced-motion: reduce` collapses all durations to 0ms except opacity.

---

## 4. The viewport

The only part of the UI that matters. Eight modes, switched by the pill toolbar.

| Mode | Key | What it answers |
|---|---|---|
| **Sphere** | `1` | "Does this read as a material?" Catches wrong roughness and bad normals instantly. |
| **Plane** | `2` | "Does the albedo look plausible and evenly lit?" The de-lighting check. |
| **Floor** | `3` | "Does it look like a real floor at eye level?" The archviz reality check. Grazing angle exposes tiling and anisotropic blur. |
| **Tiling 1× / 3× / 8×** | `[` `]` | "Where does it repeat?" The 8× view is the honest one; nobody ships a 1× view. |
| **Seam heat map** | `H` | "Where exactly is it broken?" Red overlay, computed from the wrapped gradient across tile boundaries — not a decorative image. |
| **A / B** | `D` | "Was the last operation an improvement?" Draggable split against the previous version. |
| **Map viewer** | `M` | 2D inspect one map with zoom to 1600%, pipette readout, colour picker showing sRGB + linear values. |
| **Channel inspector** | `C` | R/G/B/A of the current map as separate greyscale panes, with per-channel histogram. |

**Viewport controls**

- Orbit (`LMB`), pan (`MMB` / `Space+LMB`), zoom (`wheel`, `F` to fit).
- Lighting environments dropdown: **Studio**, **Overcast**, **Warm interior**, **Night interior**,
  **Grazing sun**. These are the five that matter for archviz — not a dozen generic HDRIs.
  8k environments only if VRAM allows; they are downsampled to 1k for irradiance by default.
- Exposure slider (`-3 … +3 EV`) — the honest way to check whether an albedo is in range.
- Toggles, always visible: **Flip normal green** (`G`), **Normal strength**, **Roughness bias**,
  **Show AO**, **Show displacement** (mesh tessellation slider — lets you see whether the height
  map is worth using as real displacement or only as bump).
- Texel-density readout in the corner: `1.9 px/mm · 12.4 px/cm`. Turns red when under 1 px/mm.
- A permanent **"so what?" strip** under the viewport: seam score, albedo mean, clip %, and the
  single worst warning, clickable to jump to the offending region.

**Performance contract**

| Interaction | Budget |
|---|---|
| Any slider drag | Preview updates at ≥ 40 fps. Full-res recompute is queued. |
| Viewport mode switch | < 120 ms (cached textures, no pipeline rerun) |
| Tiling change | Instant (uniform change only) |
| Any parameter change | 1/4-res preview < 250 ms; full res queued with a pending badge |
| Project open | < 800 ms to interactive |

---

## 5. Screens

### 5.1 Workbench (the main screen)

Layout above. Loaded by default. Everything the daily loop needs:
**drop → look → adjust → export**, never leaving this screen.

### 5.2 Drop zone / new material

Full-window drop target with three affordances:

- **Drop a single image** → auto-classify, auto-preset, straight into the Workbench.
- **Drop a PBR set (multiple files)** → auto-detect which file is which map (filename heuristics
  plus image statistics — a blue-dominant mean near `(0.5, 0.5, 1.0)` is a normal map; a flat
  greyscale near 0.5 is roughness) and skip derivation for the maps you already have.
- **Point at a folder** → batch mode, presets applied to every image found.

Shows a plain-language classification line: *"Looks like: **floor tile** · lattice detected
(4 × 8 repeat) · suggested preset **Enscape (tile)**"* with a "why?" link that shows the evidence.

### 5.3 Export

The only other full-screen surface. Left: file tree preview with real filenames.
Right: preset, resolution matrix, formats, and a **"Wire it into <renderer>"** panel.

That panel is the highest-value screen in the app. It shows, per renderer, the literal slot
assignments and numeric settings, with a copy button:

```
V-Ray 6 · VRayMtl
  Diffuse      ← brick_wall_red_albedo_4k.png      (Bitmap gamma: Override 1.0)
  Roughness    ← brick_wall_red_roughness_4k.png   (gamma 1.0 — DATA map)
  Metalness    ← brick_wall_red_metallic_4k.png
  Bump/Normal  ← brick_wall_red_normal_4k.png      (green: +Y / OpenGL)
  Displacement ← brick_wall_red_height_4k.exr      (2D, 3.0 mm, mid-level 0.5)
  IOR 1.52 · Glossiness mode: Roughness
  Tip: use VRayTriplanar on curved geometry
                                                        [ Copy as text ]  [ Write .ms script ]
```

### 5.4 Library

Grid of material cards with generated thumbnails (sphere + a 3×3 plane, rendered by the same
engine as the viewport). Filters: category, tags, QC verdict, presence of AI steps, resolution,
texel density. Multi-select → batch export, batch re-run, batch rename. `Ctrl+F` focuses search.

### 5.5 QC dashboard

One row per material. Columns are the metrics from [QC_METRICS.md](QC_METRICS.md). Sort by any
column; click a cell to jump to that material with the relevant overlay already on.
The top of the screen is a single sentence: *"14 of 22 materials are export-ready; 6 warn;
2 fail."*

### 5.6 Timeline / history

Bottom-left, collapsed by default to the stage list. Expanded, it is a vertical list of every
version with a diff summary:

```
v004  ● now      Roughness remapped 0.35–0.9 · AO 0.6 · export enscape-4.1
v003  ✓ 14:20    Seam method: lattice-snap (was poisson) · seam 71% → 94%
v002  ✓ 14:18    De-light 0.55 · AI upscale 2× (blueplant-v2, seed 1234)   [violet]
v001  ✓ 14:12    Import brick_wall_red.jpg (4032×3024, sRGB, 1.9 px/mm)
```

Every row has **Restore**, **Fork**, and **Diff vs current**. This list is the reason people trust
the tool — it answers "what changed and when" without asking.

### 5.7 Batch queue

Table of jobs: thumbnail, name, stage, elapsed, ETA, warnings. Failed jobs show the reason inline
and offer **Retry** / **Open in Workbench** / **Copy error**. Never a dead end.

### 5.8 Settings

Five tabs, all boring: Paths, Performance, Defaults, Integrations, AI. Every AI toggle explains
what leaves the machine in one sentence.

---

## 6. Components

| Component | Behaviour notes |
|---|---|
| **Slider** | Built once, correct everywhere: click-drag, click-to-type, `↑↓` steps, `Shift` ×10, `Alt` ×0.1, `Esc` cancels a drag, double-click resets to default. Drag **does not** rebuild the full-res pipeline. |
| **Number field** | Right-aligned mono digits, unit suffix as a separate muted span, invalid states never swallow input. |
| **Segmented control** | The viewport mode toolbar and resolution pickers. Keyboard: `←/→` moves selection, `Home/End` jump. |
| **Score pill** | `● 94 Seam`. Colour: ≥ 90 `--ok`, 75–89 `--warn`, < 75 `--fail`. Click → opens the heat map focused on the worst region. **Always shows the number.** |
| **Map row** | Thumbnail (live, updated at 1/4 res), name, badges for colour space and bit depth, R/G/B/A mini-histogram, visibility toggle, solo (`Alt+click`). |
| **Stage chip** | Pipeline status: pending / running / done / warn / failed. Running chips show elapsed time, never a fake percentage. |
| **AI badge** | Violet pill: `AI · upscale-v2 · seed 1234`. Hovering reveals what it did and a "compare without" action. |
| **Warning row** | Icon + one plain sentence + a "show me" action that moves the viewport. Never a tooltip-only warning. |
| **Command palette** | `Ctrl+K`. Fuzzy over commands, materials, parameters, presets. Also the answer to "where is that one setting". |
| **Divergence bar** | Thin strip above the viewport in A/B mode: shows where A and B differ most, clickable to jump there. |
| **Toast** | Bottom-centre, max 2 stacked, auto-dismiss 4 s, always with an Undo action where undo exists. |

---

## 7. Keyboard map

```
Global
  Ctrl+K          Command palette           Ctrl+S        Save version
  Ctrl+Z / Y      Undo / redo               Ctrl+E        Export
  Ctrl+O          Open material             Ctrl+F        Focus search
  Ctrl+,          Settings                  ?             Shortcut sheet
  Ctrl+Shift+P    Cycle preset              Ctrl+Enter    Run pipeline (from first stale stage)

Viewport
  1 2 3           Sphere / plane / floor    4 5 6         Map / tiling / heat
  [ ]             Tiling down / up          G             Flip normal green
  H               Seam heat map             D             A/B compare
  F               Fit to view              , .           Previous / next map
  M               Map viewer                C             Channel inspector
  Space+drag      Pan                       L             Cycle lighting environment
  Shift+S         Detach viewport to 2nd monitor

Adjust
  ↑ ↓             Nudge focused slider        Shift+↑↓      ×10
  Alt+↑↓          ×0.1                      Backspace     Reset slider
  Tab / Shift+Tab Next / previous control
```

---

## 8. States you must design (and that most tools forget)

| State | Design |
|---|---|
| **Empty** | Not a sad grey box. A 5-image sample gallery that produces a beautiful result in one click, so the first 30 seconds teach the tool. |
| **First run** | One-screen overlay: drop a photo, watch it work, done. Dismissed forever after. |
| **Slow job** | Stage name + elapsed + "you can keep working". Never a modal spinner. |
| **Partial failure** | "Seamless OK · de-light OK · **derive failed** (VRAM). Maps below are from v003." Continue with what exists. |
| **Bad result** | Explicitly flagged, with the alternative method offered inline: *"Lattice snap scored 61% — try **spectral matching** instead?"* |
| **Non-tileable source** | Honest refusal: *"This looks like a marble slab with no repeat unit. Tiling will always show a seam. Use **random offset** or a 2×2 variation atlas instead."* |
| **Offline / no model** | AI features greyed with the reason stated, not hidden. |
| **Corrupt input** | Which file, which check failed, what to do. Never "unknown error". |

---

## 9. Accessibility

Non-negotiable even for personal use — it costs nothing to do now and everything to retrofit.

- Contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text and UI borders. The palette above is
  checked; `--text-muted` is for non-essential metadata only.
- **Never colour alone**: score pills carry the number, status chips carry an icon + word.
- Full keyboard operability. Visible focus ring via `--shadow-focus`, never `outline: none`.
- `prefers-reduced-motion` respected.
- Viewport aspect and content are described in a live region for screen readers
  ("Seam score 94, 1 warning: repetition at 8× tiling").
- Zoom to 200% without layout breakage (panels collapse, viewport holds).
- No text smaller than 10px, ever.

---

## 10. Do / Don't

**Do**

- Put the number next to the claim.
- Let the user keep working while a job runs.
- Make the tiling view the default, not a hidden menu item.
- Show the export file tree *before* the export runs.
- Keep the lighting environments to the five that matter for architecture.
- Remember split positions, last preset, last resolution, last export folder.

**Don't**

- Don't animate texture data or add loading shimmers over the viewport.
- Don't use the accent colour for anything that is not a primary action or live value.
- Don't use violet for anything that is not AI.
- Don't hide the seam score behind a tab.
- Don't put a settings drawer inside a panel inside a tab.
- Don't ship a progress bar you cannot compute. Use elapsed time instead.
- Don't let a slider rebuild the full pipeline on every pointer move.

---

## 11. Responsive / multi-monitor

| Width | Behaviour |
|---|---|
| ≥ 1920 | Full three-column, timeline expanded |
| 1440–1919 | Target. Three-column, timeline collapsed |
| 1280–1439 | Right panel collapses to rail; opens as an overlay |
| < 1280 | Single column: viewport + bottom sheet. Usable, not primary. |
| Second monitor | **Detach viewport** (`Shift+S`) opens the preview in a borderless always-on-top window. Archviz artists work with a viewport on the side monitor while tweaking — this is the single most-requested feature in every tool of this kind. |

---

## 12. Implementation notes

- The prototype uses plain CSS custom properties and no framework, so the tokens above can be
  lifted verbatim into whatever front end gets used.
- The 2.0.0 viewport is **WebGL**. A WebGPU renderer was a design proposal for 4k tiling previews
  with mip-mapping; it was not implemented, and the shipped build makes no WebGPU claim.
- The preview engine is the *same* PNG-math as the pipeline: normals are derived from height,
  roughness is modulated, colour spaces are converted in the shader with an explicit
  `pow(2.2)` on the albedo path. If they diverge, the preview stops being a QC tool.
- Never re-implement the preview's PBR model in JS. One shader, used for the viewport, the library
  thumbnails, and the export preview cards, so a thumbnail can never disagree with the viewport.
