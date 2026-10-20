# QC Metrics

The numbers that let you trust a material a week later. Every claim the UI makes resolves to one
of these formulas.

Design rules:

1. **One metric per concern.** No two metrics answer the same question in different ways.
2. **Every metric is computed on the actual output**, not on the parameters that produced it.
3. **Thresholds are explicit**, versioned with the pipeline, and changeable in `qc_thresholds.toml`.
4. **A metric that cannot fail is not a metric.** If it never goes red, it is decoration — remove it.
5. **Failures name a location.** A score without a bounding box is not actionable.

---

## 1. Seam score (the headline metric)

**Question:** "Does the image actually tile without a visible discontinuity?"

### 1.1 Definition

The metric answers one comparative question: **is the wrap join worse than the joins the texture
already contains?**

For each axis, build the join profile — the mean absolute difference across every adjacent pixel
column (or row), including the wrap:

```
horizontal axis:
    C(k)    = mean over rows y of | I(y, k+1) - I(y, k) |        for k = 0 .. N-2
    C_wrap  = mean over rows y of | I(y, 0)   - I(y, N-1) |

vertical axis: the same, transposed

    e = max( C_wrap_h / p95(C_h) , C_wrap_v / p95(C_v) )        floored at 1.0

    seam_score = round( 100 / e )                                clamped to 1..100
```

Computed on the **height field**, not the albedo — the height field is the authoritative
representation and it is where a discontinuity is most visible in a render.

**The 95th percentile rather than the maximum**, so that one stray aliased pixel in the interior
cannot inflate the reference and hide a real seam. **The floor at 1.0**, because a join cannot be
better than seamless. **The max over both axes**, because a texture with a seam on one axis does
not tile.

### 1.2 Why it is defined this way

`e` is dimensionless and has a physical reading:

| `e` | What it means | Score |
|---|---|---|
| 1.00 | the wrap is no worse than the texture's own sharpest join — there is nothing to see | 100 |
| 1.18 | the wrap is 18% stronger than the sharpest legitimate join — findable if you look | 85 |
| 2.00 | twice as strong: the wrap is now *the* most prominent line in the tile | 50 |
| 4.00 | four times as strong — glaring | 25 |
| ≥ 100 | a hard step | 1 |

There are **no magic constants** in the mapping: `100 / e` is the whole of it, and it is
explainable in one sentence, which matters because this is the number the whole UI is built around
and users will rightly ask what it means.

### 1.3 Why not compare against the *mean* interior join

The first version of this metric did, and it was wrong in a way worth recording so it is not
reintroduced. It computed:

```
e = mean(boundary_grad) / mean(interior_grad)     <-- WRONG
```

That is a units mismatch. The numerator is one join; the denominator is an average over ~N joins,
most of which are interior pixels of a smooth surface. A brick wall has a dozen sharp mortar
joints and hundreds of smooth pixels between them, so the mean is diluted to almost nothing and
**a perfectly tileable brick field scored 30/100** — the tool crying wolf about a seam that does
not exist. Measured, on the standard brick fixture at 192²:

| Fixture | Old metric | Old score | New metric | New score |
|---|---|---|---|---|
| **perfectly tileable brick** | 3.79 | **30 (false positive)** | 0.98 → e=1.00 | **100** |
| white noise (tiles perfectly) | 1.01 | 100 | 0.93 → e=1.00 | 100 |
| flat field | 0.00 | 100 | e=1.00 | 100 |
| mild lighting ramp | 8.70 | 0 | e=1.66 | 60 |
| strong lighting ramp | 18.26 | 0 | e=2.26 | 44 |
| full-range ramp | 191.00 | 0 | e=191 | 1 |

A false positive on a flawless texture is the single worst failure mode available to this metric,
because it sends the user hunting for a seam that is not there. `pbrforge selftest` now asserts
against it explicitly (`test_tileability`), and `pbrforge/core/synth.py` and `ui/prototype.html`
implement the same definition and must agree.

### 1.4 Thresholds

| Score | Verdict | `e` | Meaning |
|---|---|---|---|
| 95–100 | pass | ≤ 1.05 | Tiles perfectly, including for lattice-snap (structurally exact). |
| 85–94 | pass | ≤ 1.18 | A seam exists but requires inspection to find. |
| 70–84 | warn | ≤ 1.43 | Visible on a flat plane under a studio light. Fix it. |
| 50–69 | fail | ≤ 2.00 | The wrap is the most prominent line in the tile. Fix it. |
| < 50 | fail | > 2.00 | Visible in a render. The method failed. |

### 1.5 Also reported

- `worst_region` — a bounding box around the highest-error boundary pixels, so the UI can jump there.
- `method` — which stage produced it (lattice-snap / heal / spectral / quilt / ai).
- `e` — the raw ratio, because the score alone hides how bad the bad cases are.
- `axis` — which axis carried the worse join. One axis with a seam is a common and confusing case.

### 1.6 Known limits, stated rather than hidden

**It measures the wrap only.** A texture spliced from two mismatched halves is broken in the
*interior* and scores 100 here. Measured: a brick field with a +0.12 step across its vertical
midline scores `e = 0.99`, score 100. That is not a bug in the metric; it is the question the
metric asks. An interior discontinuity is a different defect, caught by the per-map audit in §10 —
do not extend this metric to cover it, or it stops meaning one thing.

**It measures discontinuity, not quality.** A perfectly seamless but badly synthesised result
(wrong brick bond, smeared detail) scores high and is still bad. That is what `structure` (§2) is
for. Two metrics, two questions, no overlap.

**A high-gradient texture needs a genuinely smooth wrap to score well.** On a texture whose
interior contains a full-magnitude step, the reference `p95(C)` is large, so `e` is small and a
moderate seam can hide behind it. This is deliberate — if the texture already contains joins that
strong, one more at the boundary is not what the eye notices first — but it means the score is
relative to the texture, and a score of 100 is not a claim that the texture is smooth.

---

## 2. Structure preservation

**Question:** "Did making it tile destroy or invent content?"

```
structure = SSIM( output, source )   computed over the region OUTSIDE the seam band only
```

Only outside the band, because inside it the pixels were *supposed* to change. Measuring the whole
image punishes any method for working.

| Value | Verdict |
|---|---|
| ≥ 0.97 | pass |
| 0.93–0.97 | warn — the method altered content it should not have |
| < 0.93 | **fail — automatically reject the AI result and fall back** |

For methods that legitimately change more (quilting, spectral), the threshold shifts to 0.90/0.85
and the class is recorded so the comparison stays fair.

**This is the gate that keeps AI honest.** If a diffusion step changes the image outside its
declared band, or drifts structurally, it is rejected by maths rather than by review.

---

## 3. Albedo plausibility

**Question:** "Is this a physically plausible diffuse colour, and will it look right beside
everything else in the scene?"

```
mean_srgb      = mean of the albedo, converted to sRGB, luminance-weighted
p1, p99        = 1st and 99th percentiles of the albedo luminance
clip_black_pct = % of pixels with luminance < 0.01
clip_white_pct = % of pixels with luminance > 0.99
pure_black_pct = % of pixels with any channel exactly 0
```

| Metric | pass | warn | fail |
|---|---|---|---|
| `mean_srgb` | 0.18–0.70 | 0.15–0.18 or 0.70–0.75 | < 0.15 or > 0.75 |
| `clip_black_pct` | < 0.1% | 0.1–0.5% | > 0.5% |
| `clip_white_pct` | < 0.5% | 0.5–2% | > 2% |
| `pure_black_pct` | 0% | > 0% | — |

**Why pure black is a failure and not a warning:** pure black albedo causes fireflies in V-Ray and
Corona, black-hole artefacts in some realtime engines, and is never physically real (the darkest
real material, fresh asphalt, is around 0.04). The pipeline floors albedo at 0.04 by default.

**Why the mean matters so much:** a material whose albedo mean is 0.10 looks correct in isolation
and reads as a hole in the wall next to a 0.45 concrete. Most "why does this one look pasted on"
problems are an albedo mean that drifted during de-lighting. That is why `delight` restores the
mean and this metric checks that it did.

**Category range check:** if the user declared `category: concrete` and the albedo mean is 0.72,
warn once with the reference value from [PRESETS.md](PRESETS.md) §6. Never silently clamp.

---

## 4. Normal map sanity

**Question:** "Is this a valid tangent-space normal map that will render correctly?"

```
blue_mean      = mean of the B channel                       (expect ≈ 0.85 for a flat surface)
xy_mean        = length of mean([R-0.5, G-0.5])              (expect ≈ 0)
unit_error     = mean( |length(2*rgb - 1) - 1| )
nan_count      = pixels that are not finite
green_asymmetry = mean(G) - 0.5                              (a large value hints at a flip)
```

| Metric | pass | fail |
|---|---|---|
| `blue_mean` | 0.6–1.0 | < 0.5 |
| `xy_mean` | < 0.15 | > 0.35 (means the surface is tilted globally — usually a bug) |
| `unit_error` | < 0.02 | > 0.05 (renormalise after every resize) |
| `nan_count` | 0 | > 0 |

**Orientation** is verified separately, and cannot be checked numerically with confidence:
render a lit sphere and confirm that bumps catch light from the upper-left. The UI's `G` key
flips it live so the human eye makes the call in one second. Record which convention was exported.

**Do not** attempt to auto-detect the correct green channel from statistics. A slightly convex
surface and a slightly concave one have identical statistics and opposite meanings.

---

## 5. Roughness sanity

**Question:** "Is there a plausible distribution, or did the estimator give up?"

```
r_mean, r_std, r_p05, r_p95, r_entropy
```

| Metric | pass | warn | fail |
|---|---|---|---|
| `r_std` | > 0.04 | 0.02–0.04 | < 0.02 (flat) |
| `r_mean` | 0.15–0.92 | 0.08–0.15 or 0.92–0.97 | < 0.08 or > 0.97 |
| `r_entropy` | > 4 bits | — | < 3 bits |
| pixels exactly 0.0 or 1.0 | 0 | — | > 0 |

A roughness map with `r_std < 0.02` is the signature of a tool that failed to estimate and wrote a
constant. It is the single most common tell of cheap auto-PBR output, and it looks like plastic.

Also reported: the **estimator confidence**, which is a separate thing from the distribution.
A uniform roughness map can be the *correct* answer for polished marble — but only if it was a
decision, not a failure. The confidence value distinguishes them.

---

## 6. Map alignment

**Question:** "Do the maps agree about where the features are?"

```
For each derived map D and the albedo A:
    shift = argmax over ±3 px of cross-correlation( ∇D, ∇A )
alignment_px = max |shift| across all derived maps
```

**Target: exactly 0.** Because normal, AO, cavity and roughness are derived from the same height
field, any non-zero value is a bug in the derivation, not a tuning problem.

| Value | Verdict |
|---|---|
| 0 | pass |
| 0.5 px | warn — investigate |
| ≥ 1 px | **fail** — the pipeline is broken |

This metric exists mainly as a *test* rather than a report. Its value is that it turns the
"maps do not line up" class of bug from a visual hunt into an assertion.

---

## 7. Repetition analysis

**Question:** "Will this read as a grid when tiled 12 times across a floor?"

```
Render the material at N×N tiling (N=8) under a grazing light.
Compute the 2D autocorrelation of the render.
Find peaks at multiples of the tile period.
repetition_score = peak_at_tile_period / mean(autocorrelation away from the origin)
```

| Value | Verdict |
|---|---|
| < 0.25 | pass — repetition is not perceptible |
| 0.25–0.45 | warn — visible at grazing angles or close range |
| > 0.45 | warn loudly — this needs macro variation |

**Note this is the only metric that measures the *real* perceptual problem.** Seam score measures
whether the edges line up; repetition measures whether tiling is visible at all. A texture can
score 99 on seams and still read as an obvious grid, and it is the second one that clients notice.

**The fix is always the same:** increase `variation.macro_strength`, or generate variants, or use
a Wang tile set. See [FEATURES.md](FEATURES.md) §2.6.

---

## 8. Texel density

**Question:** "Does this actually have enough pixels for how it will be seen?"

```
px_per_mm = texture_width_px / real_width_mm
```

| Context | Target px/mm |
|---|---|
| Close-up hero, furniture | 4–8 |
| Interior floor at 2–3 m | 2–4 |
| General interior walls | 1–2 |
| Exterior facade, distant | 0.3–1 |
| Background/context | < 0.3 |

Reported as `ok` / `low` / `high`, with the concrete consequence spelled out:
*"1.9 px/mm. At a 2 m viewing distance a 4k texture spans 2.1 m — you will see it go soft up
close."* Numbers people can argue with are better than a red dot.

If no real-world module is known, the density is reported as `unknown` and the audit suggests how
to measure it (count brick courses, count planks, count tiles).

---

## 9. Energy and physical consistency (optional, high value)

Runs only when a module or reference is available.

```
albedo_vs_reference = |mean_srgb - reference_mean| / reference_mean
height_vs_amplitude = the measured relief in mm (from height p1-p99 × amplitude_mm)
                      compared against the expected relief for the category
roughness_vs_category = whether the roughness distribution overlaps the category's typical band
```

Catches the subtlest failures: a brick wall whose relief is 40 mm deep (should be 10–20), a
marble floor rougher than the concrete beside it, an albedo three times darker than the reference
for that material.

---

## 10. Per-map technical audit

Cheap checks, run on every export, reported as a list:

| Check | Fail condition |
|---|---|
| Colour space | A data map tagged or written as sRGB |
| Format | Any data map or packed map written as JPEG |
| Bit depth | Height or normal below 16-bit for an offline preset |
| Range | Height map not using at least 60% of the 0–1 range (flat, wasted precision) |
| Alpha | Alpha present but entirely opaque, or entirely transparent |
| Size | Non-power-of-two for a realtime preset |
| Orientation metadata | Normal orientation missing from the sidecar |
| Tiling | A map marked tileable whose boundary error exceeds the seam threshold |

Any failure here is an **export-blocking error**, not a warning. A gamma'd roughness map is not a
style choice.

---

## 11. Verdict logic

```
fail  if any metric is fail, or any export-blocking audit check fails
warn  if any metric is warn, or the repetition or density check is warn
pass  otherwise
```

The verdict is written into `material.json` and shown as a pill in the library, so a folder of 500
materials can be sorted by "what needs attention" instead of being opened one by one.

---

## 12. Configurable thresholds

`qc_thresholds.toml`, versioned with the pipeline so an old material's verdict can be reproduced:

```toml
[seam]
pass = 85
warn = 75

[structure]
pass = 0.97
warn = 0.93

[albedo]
mean_min = 0.18
mean_max = 0.70
clip_black_max = 0.005
clip_white_max = 0.02
pure_black_max = 0.0

[normal]
blue_mean_min = 0.6
unit_error_max = 0.02
xy_mean_max = 0.15

[roughness]
std_min = 0.04
entropy_min = 3.0

[repetition]
pass = 0.25
warn = 0.45

[density]
min_px_per_mm = 1.0
```

Changing a threshold changes every verdict produced after that change. Old verdicts are not
recomputed, and `material.json` records the threshold set version so a historic report stays
interpretable.

---

## 13. Human review checklist

Automated metrics cannot judge material identity. Five questions, thirty seconds, per material:

1. **Does it still look like the thing I photographed?** (structure gate)
2. **On the sphere, does the relief read as the right material?** (normal strength and amplitude)
3. **On the plane, does the albedo look slightly boring and flat?** (de-lighting worked)
4. **At 8× tiling, can I see the grid?** (repetition and macro variation)
5. **Would I ship this in the project I am working on today?** (the only one that matters)

Record the answer; after fifty materials, compare your answers against the metrics and fix the
thresholds where they disagree. That calibration is worth more than any new algorithm.

---

## 14. Metric → metric disagreements

When two metrics disagree, decide which one wins rather than averaging them.

| Situation | Winner | Why |
|---|---|---|
| Seam score high, repetition high | **Repetition** | The client notices the grid, not the seam. Add macro variation. |
| Seam score high, structure low | **Structure** | The method invented content. Reject and take the classical result. |
| Roughness distribution good, confidence low | **Confidence** | It looks plausible by accident. Say so honestly. |
| Albedo mean good, clipping high | **Clipping** | Clipped highlights are unrecoverable; the mean is an average over a broken image. |
| All metrics pass, human says no | **Human** | Then write a test that captures the human's reason, because it is a class of failure not yet measured. |
| Any metric fails, human says it looks fine | **Metric** | Fix it or relax the threshold deliberately. Never ignore it silently. |
