# Pipeline

Every stage, every parameter, every default, every failure mode. This is the reference for anyone
touching the algorithms.

Read [ARCHITECTURE.md](ARCHITECTURE.md) §4 first for the DAG and cache rules.

---

## 0. The one rule that matters

> **Fix the seams once, on one authoritative representation, then derive everything from it.**

Concretely: the pipeline computes a single **height field** (and its de-lit albedo sibling) across
all maps, makes *that* seamless, then derives normal, AO, cavity and roughness from it.

Why this is not optional:

- If you seam-fix the albedo and the normal map separately, the two solutions disagree at the
  boundary. You get a visible line where relief does not match colour. This is the most common
  bug in every auto-PBR tool.
- Derived maps are aligned *by construction*. There is no alignment step to get wrong.
- It halves the compute: one solve instead of six.
- It makes the "derived maps are consistent" property testable rather than hopeful.

**Corollary:** any operation that must touch a map individually (a levels adjustment on the
albedo, say) is applied as a *narrowly-scoped transform on that map only*, never as a spatial
warp. Spatial warps are shared.

---

## 1. Stage reference

### 1.1 `ingest`

**Goal:** get pixels in, know exactly what they are.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `max_pixels` | int | 178 956 970 | 16k × 16k safety limit. Exceeding it is a clean refusal, not an OOM. |
| `detect_maps` | bool | `true` | Classify multi-file drops into a PBR set |
| `strip_metadata` | bool | `true` | EXIF/GPS out of the pipeline copies; the original in `source/` is never modified |

Detection heuristics for a multi-file drop, in priority order:

1. **Filename**: `_normal/_nrm/_norm/_n` → normal; `_rough/_rgh/_r` → roughness; `_ao/_occ/_ambient` → AO;
   `_metal/_mtl/_m` → metallic; `_height/_disp/_h/_bump` → height; `_albedo/_diffuse/_basecolor/_col/_d` → albedo.
2. **Statistics**: mean in `(0.35, 0.65)` for R,G with B > 0.75 and low saturation → tangent-space
   normal map. Near-flat greyscale with σ < 0.15 → roughness or metallic. R≈G≈B with high σ → height or AO.
3. **Cross-correlation**: if two candidate heights exist, the one whose Sobel matches the *other*
   file's blue channel is the height and the other is the normal. This resolves the ambiguous case.

**Failure modes:** colour space unknown → default to sRGB and warn loudly (a wrongly-assumed
colour space silently poisons every downstream stage). 16-bit data map dropped to 8-bit → warn.
Alpha channel present with all-opaque → discard silently.

---

### 1.2 `preprocess`

**Goal:** remove everything that is not the material.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `auto_levels` | bool | `true` | With clipping protection: never clip more than 0.1% of pixels |
| `white_balance` | enum | `auto` | `auto` (grey-world) / `none` / `manual` (eyedropper picks a neutral) |
| `vignette_correct` | float | `0.5` | 0 = off. Estimated from the radial luminance falloff. |
| `denoise` | float | `0.2` | 0 = off. Detail-preserving (guided). Above 0.5 it starts eating texture. |
| `dejpeg` | float | `0.3` | Block + ringing suppression, masked to low-detail regions only |
| `rectify` | `[[x,y]×4] \| null` | `null` | 4-point perspective warp, hand-placed in the UI |
| `crop` | enum | `content` | `content` / `none` / `manual` |
| `aspect` | enum | `square` | `square` / `preserve` |
| `size` | int | `4096` | Target longest edge. Never upscales. |

**Order matters:** rectify → WB → levels → vignette → denoise → dejpeg → crop → size. Vignette
correction before cropping, because the vignette is a property of the lens, not the crop.

**Failure modes:** aggressive `auto_levels` on a properly exposed image darkens it — hence the
clipping guard and a preview that shows the histogram before/after. `rectify` after seam-fixing
would break tiling; the pipeline forbids it by construction (rectify is only permitted here).

---

### 1.3 `lattice` — period detection

**Goal:** find the material's true repeating unit, if it has one. **The highest-value stage in the
pipeline**: for anything man-made this produces a *perfect* result with zero distortion, zero
blurring, and no AI.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | |
| `sensitivity` | float | `0.7` | 0 = only accept very strong periods, 1 = accept weak ones |
| `max_periods` | [int,int] | `[1, 24]` | Search range in repeats across the image |
| `axis_lock` | enum | `auto` | `auto` / `x` / `y` / `both` / `none` |
| `snap` | bool | `true` | Crop to an integer multiple of the detected period |

**Method:**

1. Convert to a single-channel luminance proxy (the height estimate, not the raw albedo).
2. Apply a windowing function (Hann) to suppress edge effects.
3. Compute the 2D autocorrelation via FFT. Also compute the 1D marginal autocorrelations along
   each axis.
4. Find the first strong peak beyond a minimum distance on each axis. Strength = peak / local
   median. Accept if strength > `f(sensitivity)`.
5. Refine the period to sub-pixel by parabolic interpolation around the peak.
6. Snap to the **nearest integer number of periods** that fits the image, and crop. Cropping
   *toward* the period is what makes the result exact.

**Beyond the FFT — the part most implementations get wrong:** an FFT peak tells you *a* period
exists, not that it is the *right* one. Three extra checks:

- **Structural confirmation.** Verify that the candidate period aligns with detected mortar
  lines / grout / plank edges (via a line-orientation histogram). A period that does not land
  on the structure is a false positive, usually from an accidental colour rhythm.
- **Sub-period rejection.** If period `p` is found, reject `p/2` and `p/3` unless their peak is
  *stronger*, because a brick wall's fundamental period is one full brick + mortar, not half.
- **Consistency across axes.** For a brick running bond, the y period is the course height and
  the x period is one brick pitch; they must satisfy the bond's aspect. A single-axis result on
  a textured wall is usually a false positive.

**Output:** `(period_x, period_y, confidence, snapped_rect)`. Confidence ≥ 0.75 skips the rest of
the cascade entirely.

**Failure modes:** perspective-distorted input (vertical convergence makes the period drift
across the image) → detect and warn, offer rectify. Non-periodic content (marble, murals) →
correctly returns low confidence. Heavy JPEG noise → lower `sensitivity`.

> **UI requirement:** the detected lattice is drawn as an overlay grid on the image and is
> **draggable**. When the artist disagrees with the algorithm, dragging the grid to the true
> period takes two seconds and the algorithmic work is preserved. Never let detection be a
> silent, unchallengeable decision.

---

### 1.4 `seamless` — the cascade

**Goal:** make it tile. Cheapest effective method wins.

```
0. Lattice snap available (confidence ≥ 0.75)?  → use it, stop.        EXACT · ~0.4 s
1. Crop + best-offset search
     Score every candidate offset in a coarse grid by wrap-boundary
     gradient error + structure preservation. Pick the best window.     ~1 s
   → score acceptable? stop.
2. Heal (offset + Poisson/Laplacian on the seam band only)
     Telea/NS inpaint or a multigrid Poisson solve, band-limited,
     then multi-band (Laplacian pyramid) blend with detail re-injection. ~2 s
   → score acceptable? stop.
3. Spectral (phase randomisation + radial spectrum + histogram match)
     For noise-like classes: sand, gravel, concrete, asphalt, grass, fabric. ~1.5 s
4. Quilting (graph-cut patch synthesis, PatchMatch accelerated)
     For structured non-lattice textures. Constrained to keep the
     global luminance profile so material identity survives.              ~6 s
5. AI seam refinement (optional, off by default)                        violet badge
     Diffusion inpainting, circular-padded, seam-band masked, low
     frequency only, colour-matched, fine detail re-injected.
   → always re-scored, always reversible, never the default.
```

**Automatic escalation** stops at the first method whose seam score clears the threshold, unless
the user pins a method. The method actually used is always reported in plain language:

> *"Lattice detected: 4 × 8 brick courses. Cropped to an exact multiple — no blending needed.
> Seam score 98."*

**Parameters**

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `method` | enum | `auto` | `auto`/`lattice`/`mirror`/`offset`/`heal`/`spectral`/`quilt`/`ai` |
| `seam_band_px` | int | `96` | Scales with resolution: 4% of the longest edge, clamped 48–256 |
| `offset_search` | enum | `coarse` | `off` / `coarse` (32×32 grid) / `fine` |
| `detect_repeats` | bool | `true` | Search for a *better* offset that minimises visible repetition |
| `mirror` | bool | `false` | Only for noise. Creates visible symmetry lines on structure. Warned in UI. |
| `ai.enabled` | bool | `false` | |
| `ai.model` | str | `seam-v3` | Registry key |
| `ai.seed` | int | `1234` | Pinned for determinism |
| `ai.strength` | float | `0.55` | Blend with the classical result. Never above 0.8 by default. |

**Tiling-safe resize — a hard rule.** Any resample of a tileable image must use **wrap-around
(circular) kernel padding**. A naive `cv2.resize` reintroduces a seam at the opposite edge: the
kernel samples outside the image, the padding invents pixels that are not the ones on the far side,
and the tile stops matching itself across exactly the width of the kernel. Symptoms in the wild:
"it looks seamless in the preview but the exported 2k version has a line down it."

This is enforced in `core/image.py::resample` (`wrap=True` is the default) and asserted by
`selftest.py::test_resample_tiling`, and the assertions are measured rather than hopeful:

| Check | Measured |
|---|---|
| A periodic source, resampled 1.5x to 8x with wrap | ratio holds 1.143 → 1.136 (drift 0.007) |
| The same source resampled with edge clamping instead | 1.50 → 6.96 (diverges 6.1x by 8x) |
| A known-tileable fixture, 2x and 4x upscale | 1.000 → 1.000 (the `< 1.05` promise below) |
| A constant image, resampled to 17/33/100 px | exact to 2e-16 — the weights are normalised |

The clamped row is the reason the table exists: without it, "the ratio did not change" would not
show that wrap is doing anything at all. Two further rules fall out of the same test. Resampling to
the *same* size must be the identity, because a half-pixel offset would misalign every derived map
against its albedo. And the kernel weights must be normalised, because an un-normalised stretched
kernel changes the mean brightness — which would also break the delight stage's mean-luminance
guarantee, for a reason that has nothing to do with delight.

**Detail re-injection.** Every smoothing method (Poisson, spectral, diffusion) low-passes the
region it touches. After any of them, add back the high-frequency band from the original,
masked so the actual seam pixels stay smooth:

```
out = lowpass_healed + (original - lowpass_original) * mask_far_from_seam
```

Without this, healed textures look like tissue paper next to their unhealed neighbours.

**Failure modes:** non-tileable content (marble, murals) → return `NonTileableError` with the
honest recommendation rather than a bad tile; heavy structure across the seam → escalate to
quilting; AI halucinating grout → the structure-integrity gate in [QC_METRICS.md](QC_METRICS.md)
rejects it automatically and falls back.

---

### 1.5 `delight`

**Goal:** remove baked-in lighting so the albedo behaves under any scene lighting.

This is an **ill-posed problem** — infinitely many (albedo, light) pairs produce the same photo.
The stage's job is to be useful and honest, not correct.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `strength` | float | `0.7` | 0 = keep the photo's lighting, 1 = full flatten. **Default is not 1** because real materials have real shading. |
| `illumination_scale` | int | `0` | Kernel size for the illumination estimate; 0 = auto (⅓ of the shortest edge) |
| `method` | enum | `guided` | `guided` (fast, edge-preserving) / `l0` (piecewise flat, slower) / `retinex` (multi-scale) |
| `remove_specular` | bool | `true` | Dichromatic-reflection chromaticity suppression |
| `specular_strength` | float | `0.5` | Above 0.7 you start deleting real highlights such as wet patches and glazes |
| `remove_shadow` | float | `0.4` | Softening of dark regions not explained by local structure |
| `preserve_micro` | float | `0.3` | How much high-frequency shading survives. Keeps crevice depth alive. |
| `export_shading` | bool | `true` | Write the removed illumination as a separate map (useful as a dirt or AO mask) |
| `ai.enabled` | bool | `false` | Intrinsic decomposition |
| `ai.blend` | float | `0.5` | Blend between classical and ML albedo |

**Order:** illumination estimate → divide → specular suppression → shadow softening →
histogram restore (so the mean luminance does not drift) → micro-shading re-injection.

**The mean-luminance trap.** Flattening illumination lowers the average albedo. If you do not
restore it, every material comes out slightly dark and the artist compensates by raising the
scene's exposure, which then breaks every *other* material. The stage therefore restores the
original mean luminance after flattening, then reports the change in the QC report.

**What "good" looks like:** the texture should look slightly *flat and boring* on a plane, and
*correct* on a sphere under a different light. If it looks exciting on the plane, the lighting
is still in there.

**Failure modes:** over-flattening → plastic, washed-out albedo with lost contrast. Specular
removal on a wet/glazed material → deletes the story of the surface. Both are why the defaults
are 0.7 and 0.5 rather than 1.0, and why the UI shows a before/after split by default.

---

### 1.6 `derive_height`

**Goal:** produce the authoritative geometry field.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `source` | enum | `delit_luminance` | `delit_luminance` / `original` / `ml_depth` (violet) / `external` |
| `detail_boost` | float | `0.2` | Local contrast equalisation so relief survives a flat-lit source |
| `smooth` | float | `0.15` | Pre-filter. Above 0.4 relief becomes mushy. |
| `amplitude_mm` | float | `3.0` | **Physical.** Drives V-Ray/Corona displacement and the parallax in the preview. |
| `midlevel` | float | `0.5` | Displacement pivot |
| `bit_depth` | int | `16` | 8-bit height bands visibly on large flat surfaces |
| `ml_depth_scale` | float | `1.0` | Only for `ml_depth`; macro form only |

**Critical rule:** height comes from the **de-lit** luminance, never the raw photo. Raw luminance
contains the lighting gradient, so the derived normal map would bake that gradient in as fake
geometry — and it would then be duplicated once per tile. This is a real and visible bug
(manufactured "waves" across a wall), and it is why `delight` runs before `derive_height`.

**Physical amplitude matters more than people expect.** Displacement in millimetres is what makes
a brick wall look right in V-Ray. A height map with no scale is a bump map with extra steps, and
artists will notice the mortar is 3 cm deep.

---

### 1.7 `derive_normal`

**Goal:** tangent-space normal map from the height field, with correct orientation and enough
precision that the renderer does not band.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `strength` | float | `1.0` | Scales the XY components |
| `orientation` | enum | per preset | `opengl` (+Y) / `directx` (−Y) |
| `bit_depth` | int | per preset | 16 for V-Ray/Corona/Blender/Arnold, 8 for Unreal/Unity/realtime |
| `filter` | enum | `sobel` | `sobel` / `scharr` (sharper) / `central` (finer) |
| `smooth_before` | float | `0.0` | Applied to the height first; prefer smoothing the height over the normal |
| `clamp_z` | float | `0.0` | 0 = no clamp. Non-zero prevents the extreme-grazing sparkle that some realtime engines produce |
| `renormalise_after_resize` | bool | `true` | Always. See the two hard rules below — the cost of skipping it is measured at 0.2764 unit-length error |

**Derivation:** central differences on the (optionally pre-smoothed) height, scaled by
`strength × amplitude_mm` so that a physically deeper relief yields a correspondingly stronger
normal. Then normalise `(-dx, -dy, 1)`.

**The green channel.** The most common support issue in this entire product category. Conventions:

| Engine | Convention |
|---|---|
| Blender, Unity, glTF, USD (OpenGL) | **+Y** (green up) |
| Unreal, 3ds Max's DirectX-style, Source | **−Y** (green down) |
| V-Ray, Corona, Arnold, Redshift, Octane | Follow the host DCC convention — verify per host, do not assume |

The app never guesses silently: the preset sets it, the UI shows it, `G` flips it live, and the
sidecar records it. A wrong green channel makes convex things look concave — the test is a lit
sphere: bumps should catch light on the upper-left.

**Two hard rules:**

1. **Renormalise after every resize and every compression step.** A resample shortens the vectors,
   and an un-normalised normal map renders as a desaturated, weak surface. The effect is large
   rather than subtle: this project's own fixture normal map measures a unit-length error of
   **0.0017**, which becomes **0.2764** after a Lanczos resample across its own mortar edges — and
   0.0000 once renormalised. `core/image.py::renormalise()` is therefore **opt-in** rather than
   automatic, and refuses anything that is not a 3-channel `data` image: a packed
   AO/roughness/metallic map also has 3 channels, and normalising it destroys the data while still
   looking plausible. BC5 also requires Z reconstruction on read.
   `selftest.py::test_resample_normals` asserts both halves, the counter-example included.
2. **Never derive normals from the raw albedo.** It bakes lighting into geometry. If a user asks
   for "more detail", the answer is `detail_boost` on the height field.

---

### 1.8 `derive_ao`

**Goal:** a subtle ambient-occlusion / cavity map.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `strength` | float | `0.6` | |
| `radius_px` | int | `48` | Scales with resolution |
| `multiscale` | bool | `true` | Three radii (8 / 32 / 128) with decreasing weight; single-radius AO looks like a stencil |
| `export_cavity` | bool | `true` | Cavity = AO minus large-radius blur. Drives edge wear and dirt in the renderer |
| `export_curvature` | bool | `false` | Signed; useful as a mask |
| `invert` | bool | `false` | For renderers that want convexity |

**Method:** horizon-based ray-marching on the height field (HBAO-style), vectorised in numba.
Cavity is derived as a by-product — it is free once you have the multiscale AO.

**Keep it subtle.** Enscape, Lumion, Twinmotion and Unreal all generate their own screen-space
occlusion. Feeding an AO map *and* letting the engine compute SSAO produces the classic dirty,
dark-in-the-wrong-places look. Default `strength` is 0.6; the Enscape preset defaults it to 0.0
for the AO map (it is still exported for other renderers and for masking).

---

### 1.9 `derive_rough`

**Goal:** an honest, plausible roughness map — and the honest admission that this is the weakest
signal in the whole pipeline.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `algorithm` | enum | `hybrid` | `luminance` / `cavity` / `specular` / `hybrid` / `ml` (violet) / `flat` |
| `base` | float | `0.65` | Category prior (see the reference table in PRESETS.md) |
| `cavity_influence` | float | `0.35` | Crevices are usually rougher |
| `specular_influence` | float | `0.45` | Where highlights were detected, lower roughness |
| `variation` | float | `0.12` | Adds plausible per-pixel spread |
| `macro_variation` | float | `0.0` | Large-scale patches (see `variation` stage) |
| `remap` | [float,float] | `[0,1]` | The slider the artist will actually use most |
| `clamp` | [float,float] | `[0.05, 0.95]` | Avoid 0 and 1 — both cause renderer artefacts |
| `output_mode` | enum | per preset | `roughness` / `glossiness` |

**Why this is the weak link:** a photograph only carries roughness information where specular
reflection actually occurred, which is a small, lighting-dependent subset of pixels. Everything
else is a prior. Be upfront: the UI shows a **confidence** value, and when confidence is low the
texture gets the base value plus variation rather than a fabricated pattern.

**Anti-pattern to reject:** do not simply map luminance to roughness. Bright ≠ smooth and dark ≠
rough, and doing this produces the "everything is chrome-adjacent" look that identifies cheap
auto-PBR output immediately.

**Glossiness conversion** (legacy renderers): `gloss = 1 − rough`, but with the renderer's own
curve. V-Ray and Corona legacy glossiness are not a linear inversion of their roughness. The
preset carries the conversion curve; do not hard-code a single formula.

---

### 1.10 `derive_metal`

**Goal:** almost always a single value with the honesty to say so.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `mode` | enum | `classify` | `classify` / `zero` / `one` / `masked` |
| `value` | float | `0.0` | Used by `zero`/`one` |
| `confidence_min` | float | `0.9` | Below this, fall back to `zero` and warn |
| `mask` | path | `null` | Artist-painted or AI mask for mixed materials |

**Method:** a small classifier over image statistics (chroma abscence, specular colour cohesion,
highlight tint, mean luminance) plus, if enabled, a CLIP probe. Output is dielectric (0) or
conductor (1) with a confidence, not a continuous guess.

**The honest UI, verbatim from the plan:** *"Metallic is indistinguishable from painted or dirty
metal in a photograph. We classified this as dielectric (0.0) with 82% confidence. If you know
better, set it manually."* That sentence prevents most of the bad metallic maps in the world.

**Painted metal, anodised aluminium, dirty brass, tarnished copper and metal in shadow are all
genuinely unrecoverable.** The mask mode exists for these, and the classifier should say so
rather than bluffing.

---

### 1.11 `variation`

**Goal:** break up repetition. Cheap, and the difference between "tileable" and "convincing".

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `macro_strength` | float | `0.18` | Large-scale luminance/hue variation blended into albedo |
| `macro_scale` | float | `0.15` | Feature size as a fraction of the material's real-world size |
| `macro_hue_shift` | float | `0.02` | Subtle colour drift so a floor does not read as one colour |
| `roughness_nudge` | float | `0.08` | Correlated roughness variation — big realism win |
| `detail_split` | bool | `false` | Emit a 1/8-scale micro normal+roughness pair for renderer detail nodes |
| `output_macro_map` | bool | `true` | Write the macro layer as its own non-tiling map for the renderer |

**Why this matters more than seam quality.** A perfectly seamless 4k concrete texture repeated
12× across a hall reads as a grid because the eye detects the *period*, not the seam. Macro
variation attacks the actual perceptual problem. This is the feature that separates a tileable
texture from a usable one, and it is nearly free to compute.

---

### 1.12 `alphamatte`

**Goal:** get a usable alpha for foliage, railings, lace, decals.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | |
| `mode` | enum | `chroma` | `chroma` / `luminance` / `ml` (violet) / `manual` |
| `key_colour` | RGB | auto | Estimated from the border pixels |
| `erode_px` | int | `1` | Removes the background fringe |
| `decontaminate` | bool | `true` | Colours the semi-transparent edge with the *foreground* colour, not a mix |
| `premultiply` | enum | `straight` | Per-renderer; getting this wrong gives a dark halo |
| `preserve_rgb` | bool | `true` | Never let the matte rewrite the albedo inside the opaque region |

**Edge decontamination is the whole game.** A naive chroma key leaves green/white fringes on every
leaf. The fix is to solve for the foreground colour in partially-transparent pixels rather than
leaving the background blend in place. The renderer preset decides straight vs premultiplied —
this alone accounts for most "my foliage has a dark outline" reports.

---

### 1.13 `upscale`

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | |
| `scale` | int | `2` | 2 / 4 |
| `model` | str | `tiling-realplksr-v2` | Violet |
| `tiling_aware` | bool | `true` | **Circular padding at the borders.** Without this, upscaling a tileable texture destroys tiling at the edges. |
| `detail_guard` | float | `0.5` | Blend against a classical Lanczos upscale to prevent hallucinated detail from dominating |
| `seed` | int | `1234` | |

**Rule:** upscale *after* seam fixing, never before. Seam-fixing at native resolution and then
upscaling is both faster and more correct than the reverse.

---

### 1.14 `pack`

**Goal:** turn six maps into the exact file the renderer expects.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `preset` | str | `generic-pbr` | Selects the whole packing + naming + format profile |
| `resolutions` | [int] | `[4096]` | A matrix, not one value — you often want a 4k offline set and a 2k realtime set |
| `channel_order` | enum | per preset | `orm` (Unreal) / `maskmap` (Unity) / `mra` / `separate` |
| `normal_orientation` | enum | per preset | |
| `roughness_mode` | enum | per preset | |
| `filename_template` | str | `{material}_{map}_{res}` | Tokens in PRESETS.md |
| `bit_depth` | per map | per preset | |
| `formats` | per map | per preset | |
| `realtime_compression` | enum | `off` | `off` / `ktx2` / `dds` |
| `write_sidecar` | bool | `true` | `material.json` next to the maps |
| `write_handoff` | bool | `true` | The per-renderer snippet |

**Channel packing definitions:**

```
Unreal ORM      R = AO       G = Roughness  B = Metallic
Unity Mask Map  R = Metallic G = AO         B = Detail   A = Smoothness (1 − Roughness)
glTF MR         R = (unused) G = Roughness  B = Metallic   (+ separate occlusion texture)
V-Ray MRA       R = Metallic G = Roughness  B = AO         (user-definable)
```

**Colour-space hard gate.** `pack` refuses to write a `data` map into an sRGB-tagged PNG and
refuses to write any albedo as a JPEG when a normal map is present in the same batch (JPEG
destroys normal maps). These are errors, not warnings.

---

### 1.15 `qc`

**Goal:** produce a verdict you can trust a week later.

Full metrics, formulas and thresholds: [QC_METRICS.md](QC_METRICS.md).

Minimum viable verdict:

```
verdict: pass | warn | fail
seam_score:        94.1      (warn < 85, fail < 75)
structure:         0.981     SSIM vs source outside the seam band (fail < 0.93)
albedo_mean:       0.42      (warn <0.15 or >0.75)
clip_pct:          0.03%     (warn >0.5%, fail >2%)
normal_blue_mean:  0.87      (fail < 0.5)
roughness_std:     0.11      (warn < 0.02)
repetition_at_8x:  0.31      (warn > 0.45)
alignment_px:      0.0       derived maps, so structurally zero
texel_density:     1.92 px/mm  (warn < 1.0)
```

---

## 2. Parameter defaults by material class

`classify` picks one of these; the UI shows the choice and lets you override.

| Class | Seam method | De-light | Height source | Rough base | Metal | AO |
|---|---|---|---|---|---|---|
| **Tile / ceramic** | lattice-snap | 0.6 | delit_luminance | 0.25 | 0 | 0.5 |
| **Brick** | lattice-snap | 0.7 | delit_luminance | 0.80 | 0 | 0.7 |
| **Stone / paving** | lattice-snap | 0.7 | delit_luminance | 0.85 | 0 | 0.6 |
| **Wood / plank** | lattice-snap (1-axis) | 0.6 | delit_luminance | 0.40 | 0 | 0.4 |
| **Concrete / plaster** | spectral | 0.75 | delit_luminance | 0.85 | 0 | 0.5 |
| **Gravel / sand** | spectral | 0.5 | delit_luminance | 0.90 | 0 | 0.4 |
| **Grass / foliage** | quilt | 0.4 | delit_luminance | 0.85 | 0 | 0.3 |
| **Fabric / knit** | spectral + lattice | 0.5 | delit_luminance | 0.90 | 0 | 0.3 |
| **Metal (clean)** | lattice-snap | 0.4 | delit_luminance | 0.25 | **1** | 0.4 |
| **Metal (brushed)** | lattice-snap (axis-locked) | 0.4 | delit_luminance | 0.40 | **1** | 0.4 |
| **Rust / weathered metal** | quilt | 0.6 | delit_luminance | 0.90 | 0.5 → mask | 0.6 |
| **Marble / slab / mural** | **none** | 0.5 | ml_depth | 0.20 | 0 | 0.3 |
| **Terrazzo / speckle** | spectral | 0.6 | delit_luminance | 0.55 | 0 | 0.4 |

The `marble/slab/mural` row is the honest one: these have no repeat unit, so the tool refuses to
tile them and says why.

---

## 3. Parameter exposure

Not everything belongs in the UI. Three tiers:

| Tier | Where | Contents |
|---|---|---|
| **Primary** | Always visible, right panel | `seam.heal`, `seam.method`, `delight.strength`, `height.amplitude_mm`, `normal.strength`, `roughness.base`, `roughness.remap`, `metal.value`, `ao.strength` |
| **Advanced** | Behind `▸ Advanced`, grouped, searchable | Everything else in this document |
| **Internal** | Config file only, never in the UI | `seam_band_px` auto-scaling rules, multigrid tolerances, patch size, cache policy, resample kernels |

Tier-1 has exactly nine controls because nine is the number a person can hold in their head.
If a tenth seems necessary, the abstraction is wrong.

---

## 4. Testing hooks

Each stage in this document has, at minimum, one of these — see [DEVELOPMENT.md](DEVELOPMENT.md):

- A golden-image test with a PSNR/SSIM threshold.
- A property test where a mathematical invariant exists (tileability survives resize; normals
  stay unit length; data maps survive every conversion without gamma; derived maps align within
  1 px).
- A failure test with a deliberately bad input confirming a clean, reported error instead of
  an exception or a silently wrong result.

The three property tests that catch the most real bugs, in order:

1. `test_resample_preserves_tileability` — **enforced**: `selftest.py::test_resample_tiling`, label
   "resample tiling". Includes the clamped-resample counter-example that makes the check mean
   something, and the identity and weight-normalisation rules.
2. `test_data_maps_never_get_gamma` — **enforced**: `selftest.py::test_colour_space`, label "colour
   space". Every registered data kind, both directions, plus the export gate, plus the reverse
   direction (colour written as data).
3. `test_derived_maps_align_to_albedo` — **enforced structurally**: `test_derived_maps` (shapes and
   unit length) and the map-set block in `test_resample_tiling` (the whole set resampled together
   stays in lockstep). The pixel-level misalignment *detector* lands with the `derive_*` stages;
   until then alignment is structural — every map is the same size because one height field
   produced them all.

Two more, added with the imaging core, because measuring them changed what the right assertion was:

4. `test_resample_normals` — a resample really does break unit length (0.0017 → 0.2764 on the
   fixture), and renormalising really does fix it (0.0000). The counter-example is asserted first,
   so the test cannot pass vacuously.
5. `test_image_io` — quantisation round trips exactly, overshoot clamps instead of wrapping, and the
   content hash is stable but sensitive to a single 16-bit step.

All five live in `pbrforge selftest` rather than in a pytest tier, deliberately: the frozen build
has no pytest, and the packaged build is the one run where a broken image path must not slip
through. See [DEVELOPMENT.md](DEVELOPMENT.md) §4.2.
