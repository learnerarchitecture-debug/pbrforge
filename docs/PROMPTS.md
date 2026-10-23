# Prompts

> **Historical design record, not shipped behaviour.** The 2.0.0 build has no
> local-AI features and sends no prompts to any model. These texts are kept as
> development history and as a specification for later work, not as a claim that
> the features exist. See the status table in [README.md](../README.md).

Two unrelated things share this file name, so both are here:

- **§1 Build prompts** — copy-paste prompts used while developing the project.
- **§2 Runtime prompts** — proposed prompt strings for a future AI feature; they
  are not used by 2.0.0.

---

# §1 Build prompts

## How to use these

1. Feed one prompt per session. Do not merge them — the point is bounded scope with a checkable
   result.
2. Always include the **context block** below first. Agents remember nothing between sessions.
3. Ask the agent to run the verification step before you accept the work.
4. If the agent starts "improving" things outside the prompt, stop it. Scope creep from an agent is
   the same problem as scope creep from a person, but faster.

### Context block (prepend to every prompt)

```
PROJECT CONTEXT — read before doing anything.

Project: PBRForge. A personal, local-only tool that turns a photographed texture into a
tileable, render-ready PBR material set (albedo, normal, roughness, metallic, AO, height)
with correct colour spaces, bit depths and filenames for Enscape, V-Ray, Corona and Blender.

Read these files first, in this order:
  README.md
  docs/ARCHITECTURE.md       (module map, stage interface, cache rules)
  docs/PIPELINE.md           (the algorithms, parameters, defaults)
  docs/PRESETS.md            (colour space and packing rules)
  docs/DEVELOPMENT.md        (conventions, testing, how to add things)

Hard constraints — violating any of these is a failed task:
  1. Python 3.12. Local only. No network calls, no accounts, no telemetry, no cloud services.
  2. One authoritative height field: seam-fix the geometry once, derive normal/AO/cavity/
     roughness from it. NEVER process PBR maps independently — alignment is structural.
  3. Colour-space discipline: normal/roughness/metallic/AO/height/height are DATA maps. They
     must never receive a gamma transfer. Albedo is sRGB. Enforce this in code with typed
     Image objects (see ARCHITECTURE.md §6), not by convention.
  4. Tiling-safe resize: any resample of a tileable image uses wrap-around kernel padding.
     A naive resize reintroduces a seam. There is a property test for this; keep it passing.
  5. Deterministic by default: same input + params + version = identical bytes. Record a
     pipeline_hash in material.json.
  6. AI is optional and never in the default path. Do not add AI unless the prompt says to.
  7. Every stage implements the Stage protocol from ARCHITECTURE.md §3 and reports metrics,
     warnings and elapsed_s. No stage silently succeeds.
  8. Failures are typed and reported (NonTileableError, ColourSpaceError, VRAMError...), never
     a bare exception. Every error names the stage, the reason, and what to do.

Style:
  - Type hints everywhere. ruff + mypy clean.
  - numpy-vectorised or numba-jitted in hot loops. No Python loops over pixels, ever.
  - Docstrings explain WHY, not what. The code says what.
  - Tests alongside: a golden-image test, a property test if an invariant exists, and a
    failure test with a deliberately bad input.
  - Small functions. If a function needs a section header comment, split it.

When you finish, report: what you built, what you did NOT build, what you assumed, and the
exact command to verify it.
```

---

## Prompt 1 — Foundation

```
TASK: Build the foundation. No algorithms yet.

Create:
  pbrforge/__main__.py        dispatch CLI or studio
  pbrforge/config.py          config.toml + env var override, TOML via tomllib/tomli-w
  pbrforge/doctor.py          environment probe -> the readiness report in INSTALLATION.md §7
  pbrforge/core/image.py      the Image wrapper
  pbrforge/core/colour.py     sRGB <-> linear, data-vs-colour enforcement
  pbrforge/core/cache.py      content-addressed artifact cache with stage memoisation
  pbrforge/core/params.py     pydantic models for every parameter in PIPELINE.md §1
  pbrforge/core/errors.py     typed exception hierarchy
  pbrforge/core/context.py    PipelineContext
  pbrforge/cli.py             click commands: doctor, selftest, cache

Image wrapper requirements (this is the important part):
  - fields: data (np.ndarray), space ("srgb"|"linear"|"data"), bit_depth, path, sha256
  - from_file(path) with colour-space detection: ICC profile if present, else filename
    heuristics, else sRGB with a loud warning
  - to_file(path, format, space, bit_depth) that REFUSES to write a data map as sRGB
    and refuses JPEG for any data map
  - resample(w, h, method) using WRAP-AROUND kernel padding when the image is tileable
  - assert_space(required) used by every operation that cares
  - memory is measured and reported; no operation loads more than one full-resolution
    float32 copy of the image at a time

Cache requirements:
  - key = sha256(stage_name + params_hash + upstream_artifact_hashes)
  - atomic writes (temp + rename), file locking so two processes cannot corrupt it
  - a hit is a path copy, not a recompute

Verification: `python -m pbrforge doctor` runs and prints the report shape from
INSTALLATION.md §7. `python -m pbrforge selftest` generates a synthetic texture, round-trips
it through the cache, and asserts byte-identical output on the second run.

Do NOT implement any imaging algorithms beyond resample and colour conversion.
```

---

## Prompt 2 — Ingest, preprocess, and the PBR-set detector

```
TASK: Implement stages/ingest.py and stages/preprocess.py.

ingest:
  - accept PNG/JPG/TIFF/WEBP/EXR/BMP
  - enforce max_pixels (clean refusal, not an OOM)
  - multi-file drop: classify files into PBR map roles by (a) filename heuristics,
    (b) image statistics, (c) cross-correlation tie-break — all three tiers specified
    in PIPELINE.md §1.1. Emit a confidence per assignment.
  - preserve the original untouched in source/; strip EXIF from pipeline copies only

preprocess, applied in this exact order:
  rectify (optional 4-point) -> white balance -> auto levels (with a 0.1% clip guard) ->
  vignette correction -> denoise (guided, detail-preserving) -> dejpeg (block + ringing,
  masked to low-detail regions) -> crop -> size (never upscale)

Also build:
  - a radial vignette estimator (fit luminance falloff against radius)
  - a JPEG-block detector (8x8 DCT grid energy) that decides where dejpeg is safe

Tests:
  - golden images for each preprocess step
  - property: applying white balance to an already-neutral image changes the mean by < 0.5%
  - property: auto levels never clips more than 0.1% of pixels
  - failure: a 40k x 40k image is refused with a clean message, not an OOM
  - failure: a truncated JPEG reports "corrupt at byte N", not a decoder traceback

Report per stage: the parameters used, the measured effect (mean/std/σ change), and any
warnings.
```

---

## Prompt 3 — Lattice detection (the flagship)

```
TASK: Implement stages/lattice.py. This is the highest-value stage in the pipeline — for
man-made materials it produces an EXACT result with zero distortion and no AI. Read
PIPELINE.md §1.3 carefully; the three extra checks beyond the FFT are the whole point.

Requirements:
  - luminance proxy input (the height estimate, not the raw albedo)
  - Hann windowing, 2D autocorrelation via FFT, plus 1D marginals per axis
  - first strong peak beyond a minimum distance; strength = peak / local median
  - sub-pixel refinement by parabolic interpolation around the peak
  - structural confirmation: verify the period lands on detected structure (line-orientation
    histogram for mortar/grout/plank edges). Reject periods that do not.
  - sub-period rejection: reject p/2 and p/3 unless their peak is strictly stronger
  - cross-axis consistency check for running-bond patterns
  - snap-crop to the nearest INTEGER number of periods
  - output: (period_x, period_y, confidence, snapped_rect, evidence)

Evidence is required. For every decision the stage must be able to say why: which peak,
what strength, whether structure confirmed it. The UI draws this as an overlay grid, and the
artist can drag the grid to correct it — so return enough to draw and to re-snap from a
dragged period.

Tests:
  - synthetic brick, running bond, tile grid, plank floor: exact period recovered, conf > 0.9
  - synthetic non-periodic noise: correctly returns low confidence (must NOT claim a period)
  - a texture with an accidental colour rhythm: the structural check must reject the
    false positive
  - a half-period alias: p/2 must be rejected when the fundamental is present
  - perspective-warped input: detects drift across the image and warns
  - accuracy across the benchmark set, reported as a table

Performance: < 2 s at 4k on CPU. Subsample to 512 px for the detection pass, then verify
the candidate at full resolution before committing.
```

---

## Prompt 4 — Seamless cascade

```
TASK: Implement stages/seamless.py: the cascade in PIPELINE.md §1.4.

Methods, in escalation order: lattice-snap -> offset search -> heal -> spectral -> quilt.

The orchestrator must:
  - score after each method and stop at the first that clears the threshold
  - honour a pinned method (no escalation)
  - always report the method actually used in plain language

heal:
  - band-limited Poisson/Laplacian solve on the seam band only (96 px default, scaled 4% of
    the longest edge, clamped 48-256)
  - multigrid for the solve, numba-jitted
  - multi-band (Laplacian pyramid) blend, NOT a linear cross-fade
  - DETAIL RE-INJECTION afterwards: out = lowpass_healed + (orig - lowpass_orig) *
    mask_far_from_seam. Without this the healed area looks like tissue paper.

offset search:
  - score candidate offsets on a coarse grid by wrap-boundary gradient error plus a
    structure-preservation term
  - prefer offsets that also minimise visible repetition

spectral:
  - phase randomisation with radial spectrum and histogram matching
  - for noise-like classes only

seam scoring (define it once, use it everywhere):
  - wrapped central difference across the tile boundary
  - compared against the interior gradient distribution
  - reported as 0-100, with the worst region's bounding box so the UI can jump there

Tests:
  - property: the output tiles pixel-exactly, asserted by construction for every method
  - property: running seamless twice does not degrade the image further (idempotence)
  - golden images per method
  - failure: marble slab input raises NonTileableError with the honest recommendation
  - the three-in-a-row regression: albedo, normal and height all remain aligned within 1 px
    after the seam fix (asserted, not assumed)

Do NOT implement the AI method. Leave `method="ai"` raising NotImplementedError with a
message pointing at AI_FEATURES.md.
```

---

## Prompt 5 — De-light and derive

```
TASK: Implement stages/delight.py, derive_height.py, derive_normal.py, derive_ao.py,
derive_rough.py, derive_metal.py.

delight (PIPELINE.md §1.5):
  - illumination estimate via guided filter (default), with l0 and retinex as alternatives
  - dichromatic specular suppression, mask-gated
  - soft shadow reduction for dark regions not explained by local structure
  - MEAN-LUMINANCE RESTORE after flattening (this is mandatory — without it every material
    comes out dark and the artist compensates by breaking the scene exposure)
  - export the removed illumination as a separate map
  - report the mean/std change in the QC metrics

derive_height:
  - source from the DE-LIT luminance by default. Never from the raw photo (it bakes the
    lighting gradient into fake geometry, visible as manufactured waves across a wall).
  - detail_boost local contrast equalisation
  - physical amplitude in mm and mid-level

derive_normal:
  - Sobel/Scharr central differences on the height, scaled by strength x amplitude_mm
  - orientation flag (opengl +Y / directx -Y)
  - RENORMALISE after any resize (bilinear resampling shortens the vectors)
  - clamp_z option for realtime sparkle suppression

derive_ao:
  - HBAO-style horizon ray-marching on the height field, multiscale (8/32/128 px)
  - cavity and curvature derived as by-products
  - default strength 0.6; the Enscape preset sets 0.0

derive_rough:
  - hybrid: base prior + cavity influence + specular influence + variation
  - DO NOT map luminance to roughness directly (bright != smooth). This is the anti-pattern
    that makes cheap auto-PBR output instantly recognisable.
  - remap curve, clamp [0.05, 0.95]
  - report a CONFIDENCE value; when low, use the prior + variation and say so
  - glossiness output mode with a per-renderer conversion curve (not a linear inversion)

derive_metal:
  - classify dielectric/conductor with a confidence. Fall back to 0.0 below confidence_min
    and warn in plain language ("painted and dirty metal are indistinguishable from a
    photograph...").

Tests, in priority order:
  - property: data maps never receive gamma through any code path
  - property: normals are unit length within 1e-3 after resize and after 8-bit quantisation
  - property: derived maps align to the albedo within 1 px (cross-correlation)
  - property: delight preserves mean luminance within 1%
  - golden images per stage
  - failure: an image with no reliable specular information returns low roughness
    confidence rather than fabricating a pattern
```

---

## Prompt 6 — Pack, QC, presets, CLI

```
TASK: Implement export/ and stages/qc.py and stages/pack.py, plus the CLI verbs.

pack:
  - channel packing: orm (Unreal), maskmap (Unity), mra, separate
  - per-map colour space, bit depth and format from the preset
  - HARD GATES: refuse sRGB on a data map; refuse JPEG for any data map or packed map
  - filename templates with token validation at load time (unknown token = error, not a
    literal string)
  - resolution matrix (e.g. 4k offline + 2k realtime in one export)
  - write material.json and the handoff snippet

qc:
  - the metrics in docs/QC_METRICS.md with those exact formulas
  - verdict pass/warn/fail
  - worst-region bounding boxes so the UI can jump there
  - texel density from the real-world module

presets: implement enscape.json, vray.json, generic-pbr.json only. Follow the PRESETS.md
schema exactly, including renderer_versions_tested and verified. Unverified presets cannot
be the default export.

cli: make, batch, audit, convert, stress, explain, presets --check
  (see AUTOMATION.md §1 for the exact flags)
  batch needs: --jobs, --dry-run, --resume, --skip-existing, --manifest, and a progress file
  so an interrupted run continues after a reboot.

handoff: write the per-renderer sheet from PRESETS.md §8 (text + HTML), including the
texel-density tip and the UV-module tip.

Tests:
  - every gate above has a test that asserts it RAISES
  - a batch of 20 synthetic textures completes, resumes correctly after a simulated crash,
    and produces byte-identical output on a repeat run
  - the Enscape preset produces exactly the file set in PRESETS.md §4.1
```

---

## Prompt 7 — The studio UI

```
TASK: Build the local UI. ui/prototype.html is the design reference — match its layout,
tokens and behaviour exactly; it is not a mockup, it is the spec.

Serve from FastAPI, bind 127.0.0.1, one page, no build step, no framework.
Reuse the prototype's design tokens verbatim (UI_DESIGN.md §3).

Required screens: Workbench, Export, Library, QC dashboard, Settings
Required components: slider (with the full interaction spec in UI_DESIGN.md §6),
score pill, map row, stage chip, warning row, command palette, A/B divider.

Non-negotiables:
  - the 3D preview uses the SAME PBR maths as the pipeline (normals derived from height,
    explicit sRGB->linear on albedo, explicit linear->sRGB on output)
  - dragging a slider updates a 1/4-res preview instantly and queues the full-res recompute
    with a visible pending badge. NEVER block the UI.
  - the seam score is always visible with its number, never hidden behind a tab
  - the stage timeline shows version history with Restore / Fork / Diff
  - every AI surface is violet, labelled, and has a deterministic alternative beside it
  - progress shows elapsed time, never a fabricated percentage

WebSocket for live progress and thumbnails. WebGL preview (WebGPU was a
proposal, not part of 2.0.0).

Tests: a Playwright smoke test that drops a fixture image, waits for a completed run,
reads the seam score from the DOM, and exports a pack that passes `presets --check`.
```

---

## Prompt 8 — Guardrails and regression suite

```
TASK: Build the safety net before adding anything new.

  pbrforge selftest  the invariants that must hold in a frozen build too.
                     Three of the seven below are already enforced there:
                         test_resample_preserves_tileability   -> selftest "resample tiling"
                         test_data_maps_never_get_gamma        -> selftest "colour space"
                         test_normals_stay_unit_length_after_resize -> selftest "resample normals"
                     Add to selftest, not only to pytest: the packaged build has no pytest and is
                     the one run where a broken image path must not slip through. See
                     DEVELOPMENT.md 4.2.

  tests/property/   the same invariants, as hypothesis tests, exploring many more inputs:
      test_resample_preserves_tileability        (the classic regression)
      test_data_maps_never_get_gamma
      test_derived_maps_align_to_albedo
      test_normals_stay_unit_length_after_resize
      test_delight_preserves_mean_luminance
      test_deterministic_output_is_byte_identical
      test_seam_idempotence
      test_pack_gates_raise

  tests/benchmark/  30-50 of MY OWN textures in a fixtures folder, not synthetic.
      Each with a human-rated golden result and a per-class expectation
      (lattice confidence, expected seam method, acceptable seam score).
      `pytest -m benchmark` runs them and prints a scorecard table.
      This is the file that tells me whether a change made things better or worse.

  tests/failure/    hostile and broken inputs:
      truncated JPEG, 0-byte file, huge EXR, 16-bit data map declared sRGB, PNG with a
      bad CRC, a tileable-almost image that should be refused, a GPU-less run, a cache
      directory that is read-only.

  `pbrforge selftest` runs the end-to-end suite and prints one verdict line.
  `pbrforge doctor` is the separate question — can this machine run it at all —
  and the two are deliberately not the same command.

Requirement: every test must fail for a REAL reason, not an assertion I will delete later.
If a test is annoying, fix the code, not the test — unless the test encodes a wrong
assumption, in which case update PIPELINE.md in the same commit.
```

---

## Prompt 9 — The first AI feature (only when you get here)

```
TASK: Add tiling-aware super-resolution. This is the only AI feature with a good
value/risk ratio, so it goes first. Read AI_FEATURES.md §2 and §3.2.

  ai/registry.py    manifest entry: key, task, source, sha256, licence, provenance,
                    vram_mb, deterministic, notes
  ai/runtime.py     ONNX session pool, warm-up, VRAM guard, timeout, unload timer
  ai/upscale.py     AITask implementation

Hard requirements:
  - CIRCULAR PADDING at all four borders. Without it, upscaling a tileable image destroys
    tiling at the edges and turns the best feature into a seam generator.
  - deterministic: seeded, and the seed is recorded in material.json
  - detail_guard: blend with a classical Lanczos upscale, default 0.5, so hallucinated
    micro-detail cannot dominate
  - runs AFTER seamless, never before
  - if the model or VRAM is unavailable, the feature is greyed out in the UI WITH THE REASON.
    Never silently substituted, never hidden.
  - the output carries AIProvenance and is badged violet in the UI

Tests:
  - property: the upscaled image still tiles pixel-exactly (the whole point)
  - determinism: same seed twice = identical bytes
  - quality: LPIPS against a ground-truth downscale-then-upscale must beat plain Lanczos
  - fallback: with the model absent, the pipeline produces the Lanczos result and reports
    that AI was unavailable — not an error, not a silent substitution
```

---

## Prompt 10 — A change to an existing algorithm

```
TASK: <describe the change>

Before writing code:
  1. Read the relevant section of PIPELINE.md and state which sentence your change
     obeys or contradicts. If it contradicts, update the doc FIRST and explain why.
  2. Run the benchmark suite and paste the before-scorecard.
  3. State which property tests your change could plausibly break.

After:
  4. Paste the after-scorecard. Improvement or not, in numbers.
  5. State what got worse. Every algorithmic change makes something worse; if you cannot
     name it, you have not looked hard enough.
  6. Update PIPELINE.md, and material.json's schema if the parameters changed.
```

---

# §2 Runtime prompts

The strings the shipped AI features actually send. Kept here so they can be reviewed and edited
without reading code.

## 2.1 Seam refinement (diffusion inpainting)

**Mask:** a 96 px band centred on the wrap boundary, feathered.
**Context:** the image circular-padded so the model sees consistent wrap-around content.

```
Positive:
  seamless continuation of {material_description}, photographic texture, even diffuse
  lighting, no shadows, no highlights, flat albedo, natural grain matching the surrounding
  area, extreme close-up surface detail, no objects, no text, no watermarks

Negative:
  seams, borders, edges, visible tiling, grid, repeated pattern, mirror symmetry,
  lighting gradient, vignette, shadow, hotspot, specular highlight, blur, bokeh,
  watermark, signature, text, logo, object, frame, patch edges, colour banding

Settings:
  strength        0.55
  steps           24
  cfg             4.5
  sampler         dpmpp_2m with karras
  denoise         0.55        (low-frequency only; detail is re-injected afterwards)

Post (mandatory, in this order):
  1. Lab mean/std match against the surrounding region, per channel
  2. detail re-injection from the original high-frequency band
  3. seam re-score with the same metric used for the classical methods
  4. structure gate: SSIM vs original outside the band >= 0.95, else REJECT and keep classical
```

`{material_description}` comes from the classifier, kept to a short noun phrase: `red clay brick
wall with grey mortar`, `honed grey marble`, `woven wool carpet`. **Never** describe lighting,
style, or mood — every extra word is another way for the model to invent something.

## 2.2 Material classification (vision)

Not a generative prompt. Feed a 512 px thumbnail to the classifier; if a multimodal model is used
as a fallback, the prompt is:

```
You are classifying a photograph of a surface material for a 3D texture pipeline.

Answer with JSON only:
{ "class": "<one of: tile|brick|stone|wood|concrete|plaster|gravel|sand|grass|foliage|
              fabric|metal|brushed_metal|rust|marble|terrazzo|asphalt|carpet|leather|
              glass_block|wallpaper|other>",
  "is_periodic": true|false,
  "estimated_repeat_x": <int or null>,
  "estimated_repeat_y": <int or null>,
  "is_metal": true|false,
  "has_visible_lighting_gradient": true|false,
  "has_visible_specular": true|false,
  "has_alpha_subject": true|false,
  "note": "<under 12 words>" }

Rules:
- "is_periodic" is true only if you can see a genuine repeating unit, not merely similarity.
- Marble, murals, weathered plaster and natural stone are usually NOT periodic. Say false.
- Do not guess metal from brightness. Bright and smooth is not metal.
- If unsure, use "other" and set the flags false. A wrong confident answer is worse than "other".
```

## 2.3 Natural-language parameter editing

```
You translate a user's plain-language request into changes to a fixed parameter set.

Parameters you may change (name, type, range):
  seam.method            enum: auto|lattice|heal|spectral|quilt
  seam.seam_band_px      int 48-256
  delight.strength       float 0-1
  delight.remove_specular bool
  delight.preserve_micro float 0-1
  height.detail_boost    float 0-1
  height.amplitude_mm    float 0-50
  normal.strength        float 0-3
  roughness.base         float 0-1
  roughness.remap        [float,float] within 0-1
  roughness.variation    float 0-1
  metallic.value         float 0 or 1
  ao.strength            float 0-1
  variation.macro_strength float 0-1

Input: the current parameter values as JSON, and the user's request.
Output: JSON only, in this shape:

{ "changes": [ {"param": "...", "from": <old>, "to": <new>, "why": "<8 words>"} ],
  "unchanged_reason": "<if nothing changed, why>" }

Rules:
- Only change what the request implies. "Make it wetter" means lower roughness; it does not
  mean more relief.
- Never change more than 4 parameters for one sentence. If the request implies more, say so in
  unchanged_reason and propose the single most important change.
- Never invent parameters that are not in the list. A hallucinated parameter name is a failure.
- If the request is ambiguous or physical rather than parametric ("make it look expensive"),
  return an empty changes list and explain which knob is closest.

Nothing is applied until the user confirms the diff in the UI.
```

## 2.4 Library search

```
Convert this query into a filter. JSON only.

Available filters:
  class:string, tags:string[], min_res:int, max_res:int, resolution_exact:[w,h],
  has_normal:bool, has_roughness:bool, has_height:bool, has_alpha:bool,
  metal:bool, ai_used:bool, qc_verdict:pass|warn|fail,
  min_density:float, max_density:float, created_after:date, created_before:date,
  text_match:string

Query: "<user text>"
Current filter: <json>

Rules: merge with the current filter rather than replacing it, unless the query says
"clear" or "reset". "No AI" means ai_used:false. "Rough concrete" sets
text_match:"concrete" and tags:["rough"] — do not invent tags the library does not use.
```

## 2.5 Prompt-to-texture (generative)

Isolated, clearly labelled, never in the normal pipeline. Output is always recorded as
`provenance: generated` with the prompt and model hash in `material.json`.

```
Positive:
  seamless tileable material texture, {user_prompt}, flat even diffuse lighting,
  no shadows, no highlights, no perspective, orthographic top-down, even exposure,
  uniform density, photographic detail, 4k
Negative:
  seam, border, edge, tiling artefact, vignette, gradient lighting, shadow, highlight,
  reflection hotspot, perspective, depth of field, blur, text, watermark, logo, object,
  frame, repeated motif, mirror symmetry

Settings:
  512 -> 2048 with circular padding and tile-consistent sampling; strength 0.75, cfg 5.0,
  steps 32, fixed seed
Post: histogram match is NOT applied (there is no source to match); instead run the seam
  metric and report it honestly, and flag the material as generative.
```

**Mandatory disclosure:** exports of generated materials carry `provenance: generated` and the
export gate refuses them for any publishing/marketplace workflow, per [AI_FEATURES.md](AI_FEATURES.md) §2.

---

# §3 Prompt-writing rules for this project

Applies to both the build prompts and the runtime prompts above. Worth keeping because most
prompt damage is boring and predictable.

1. **State the invariant, not the implementation.** "Data maps never receive gamma" survives a
   refactor; "call convert_to_linear on line 42" does not.
2. **Give the failure mode.** A prompt that says what *not* to do catches more mistakes than one
   that only says what to do. The anti-patterns in PIPELINE.md are in there deliberately.
3. **Name the file.** Agents that have to guess where something lives produce parallel
   implementations of the same thing.
4. **Demand a verification command.** If you cannot run one command to check the work, you will
   not check it.
5. **Require a report of what was not done.** The most dangerous output is a confident summary
   that hides three skipped requirements.
6. **Negative prompts list objects, not adjectives.** "no watermark" works; "not ugly" does
   nothing.
7. **Never describe lighting in a texture prompt.** Every lighting word is an invitation for the
   model to bake it in, and baked lighting is the single biggest thing this tool exists to remove.
8. **Pin the seed and record it.** A prompt without a seed is an unreproducible result, which in
   archviz is a liability rather than a feature.
