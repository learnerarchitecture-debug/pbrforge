# Glossary

Terms you will forget, and the ones people use inconsistently. Definitions here are the ones this
project means, which sometimes differ from general usage.

---

## Material maps

**Albedo** — base colour with lighting removed. Also called base colour, diffuse, colour map.
The thing this tool spends most of its effort trying to recover.

**Ambient occlusion (AO)** — how much of the sky a point can "see". Darkens crevices. Widely
overused: engines generate their own screen-space AO, so adding an AO map as well produces the
dark-in-the-wrong-places look. See [PRESETS.md](PRESETS.md) §4.1.

**Cavity** — how concave a point is, independent of scale. Derived from height as a by-product of
multiscale AO. Drives edge wear and dirt accumulation.

**Curvature** — signed cavity. Positive on convex edges, negative in concave crevices. Useful as
a mask in the renderer.

**Displacement** — height as real geometry movement, not just shading. Needs a scale in
millimetres and enough vertices to show. Expensive, and the only way to get a genuinely correct
silhouette.

**Bump** — fake relief produced by perturbing the surface normal at shade time. Cheap, no
geometry needed, wrong at silhouettes.

**Height map** — greyscale elevation. The authoritative geometry representation in this pipeline.
Everything else is derived from it.

**Normal map** — tangent-space RGB encoding of a per-pixel surface direction. `(0.5, 0.5, 1.0)` is
flat. Red = X tilt, green = Y tilt, blue = Z (always mostly positive).

**Roughness** — how blurry a reflection is. 0 = mirror, 1 = fully diffuse. The weakest thing you
can recover from a photograph, and the thing people complain about most.

**Glossiness** — the legacy inverse of roughness. `gloss ≈ 1 − rough`, but not exactly: each
renderer has its own curve, so a conversion must use the renderer's curve rather than a plain
inversion.

**Metallic / metalness** — 0 for dielectrics, 1 for conductors. Essentially binary in reality.
Anything in between is a concession, not a material.

**Specular** — the older parameterisation, separate from roughness. Modern metal/rough workflows
derive it instead. Converting between the two is a lossy transformation.

**Opacity / alpha** — transparency. The convention question (straight vs premultiplied) matters
more than the map itself.

**Emissive** — self-illumination. In archviz: screens, lamps, neon, lit signage.

**IOD/height amplitude** — the real-world depth of the relief, in millimetres. Without it,
displacement is a bump map with extra steps.

---

## Colour management

**Colour space** — how numeric pixel values map to light intensity. The source of the single
largest class of bugs in this tool's domain.

**sRGB** — a gamma-encoded colour space. What an image file almost always contains, and what a
monitor expects. Sensible for albedo, **wrong** for every data map.

**Linear** — values proportional to light intensity. What shading maths requires.

**Data map** — a map that is not colour at all: normal, roughness, metallic, AO, height, opacity.
Applying a gamma curve to one is always a bug, and often an invisible one because the result
looks plausible.

**Gamma** — the exponent in the encode/decode transfer function. In tooling, usually shorthand
for "which of the two colour spaces is this in", which is why the phrase "gamma'd" is used
loosely and should be replaced with the specific space.

**Tone mapping** — compressing HDR values into a displayable range. Only relevant to the preview,
never to the maps.

**Dithering** — adding a sub-LSB noise pattern before quantising to 8-bit, to break up banding in
smooth gradients.

**Bit depth** — bits per channel. 8 for colour, 16 recommended for height and normals, 32-float
for HDR work.

**Clip** — values pinned at 0 or 1. Clipped highlights are unrecoverable, which is why
`preprocess` guards against clipping during auto-levels.

---

## Tiling

**Seamless / tileable** — the image wraps so that the right edge continues into the left. A
mathematical property, assertable in a test.

**Tiling (UV tiling)** — repeating a texture across a surface by scaling its UV coordinates.
Distinct from seamlessness: you can tile a non-seamless texture, and you often do not tile a
seamless one.

**Wrap-around / circular padding** — treating the image as a torus, so the border pixel's
neighbour across the edge is the opposite border pixel. Required by every resize and every
convolution applied to a tileable image. The most commonly missed detail in tools of this kind.

**Lattice / period** — a material's genuine repeating unit. Bricks, tiles, planks and weaves have
one; marble and murals do not. Detecting it makes tiling **exact** rather than approximate.

**Repeat unit / module** — see lattice. In architecture, "module" usually means the real-world
size of one unit: a 190 mm brick, a 600 mm tile.

**Offset (the Photoshop trick)** — shifting the image by 50% so the seam lands in the middle
where it is easy to paint out by hand. Automated here as one method in the cascade.

**Mirror tiling** — reflecting instead of repeating. Always seamless, but produces visible
symmetry lines and doubles the perceived repetition, so it is only acceptable for noise.

**Poisson / Laplacian blending** — gradient-domain image merging. Matches gradients rather than
values, so the seam disappears even when the two sides disagree on brightness. The workhorse of
seam healing.

**Quilting** — synthesising a new image from patches of the original, choosing seams that cut
through low-contrast paths (graph cut). Good for structured, non-lattice textures.

**Spectral / phase randomisation** — randomising the phase of the Fourier transform while keeping
the magnitude, preserving the power spectrum (grain statistics) but destroying the layout.
Excellent for noise-like textures.

**Detail re-injection** — adding back the high-frequency band that a smoothing method removed.
Without it, healed regions look like tissue paper.

**Wang tiles** — a finite set of edge-matched tiles that can be assembled without visible
repetition. 4 edge types = 256 tiles; 16 covers most practical cases.

**Macro variation** — large-scale, non-tiling luminance and hue variation blended over the
material. Attacks the perceived repetition, which is the actual perceptual problem.

**Detail map** — a small, high-frequency, separately-tiled map used to add close-up detail
without a huge base texture.

**Texel density** — texture pixels per real-world unit (px/mm or px/cm). The most under-used
correctness number in archviz.

---

## PBR and rendering

**PBR** — physically based rendering. Shading driven by measured material parameters rather than
artistic sliders. The point is that materials interact correctly under *any* lighting.

**Energy conservation** — a surface cannot reflect more light than it receives. Hence albedo
ceilings, and hence the ban on pure white albedo.

**IOR** — index of refraction. ~1.52 for most dielectrics, 1.55–1.6 for polished stone, 1.76 for
glass in V-Ray's convention, 1.33 for water.

**Fresnel** — reflectivity increasing at grazing angles. Every real surface does this; it is why
floors look reflective at eye level.

**GGX / Trowbridge-Reitz** — the microfacet distribution used by essentially every modern
renderer. Roughness controls its width.

**Tangent space** — the local coordinate frame of a surface, in which normal maps are expressed.
That is why normal maps are portable between meshes, and why they need a UV layout to work.

**DirectX vs OpenGL normals** — the green channel's sign. −Y (DirectX, Unreal) vs +Y (OpenGL,
Blender, Unity, glTF). The cause of more integration bugs than anything else in this list.

**ORM** — packed map: R = AO, G = Roughness, B = Metallic. Unreal's convention.

**Mask Map** — Unity HDRP/URP: R = Metallic, G = AO, B = Detail, A = **Smoothness** (inverted
roughness). The inverted alpha catches people out.

**metallicRoughness** — glTF's packing: G = roughness, B = metallic. AO lives in a separate
texture, which surprises people who expect ORM.

**Spec/gloss workflow** — the pre-2015 parameterisation. Still present in legacy scenes and
libraries, which is why `pbrforge convert` exists.

**OpenPBR** — the Academy Software Foundation's unified surface shading model, expressed in
MaterialX. Intended to replace the pile of renderer-specific parameter sets.

**MaterialX** — an open standard for describing materials and shader graphs, renderer-agnostic.

**UDIM** — a UV tiling convention for spreading a large texture across numbered tiles, so a
4 m mural can be eight 4k maps instead of one impossible 16k one.

**KTX2 / BCn / ASTC** — GPU texture compression formats. BC5 for normals (two channels, Z
reconstructed on read), BC7 for colour and packed maps, BC4 for single-channel.

---

## This tool

**Stage** — one step of the pipeline with a single responsibility and a cacheable artifact.

**Cascade** — the ordered list of seam methods, cheapest first, stopping when the score clears the
threshold.

**QC report** — the metrics, thresholds and verdict attached to every material.

**Seam score** — 0–100. How discontinuous the wrap boundary is compared with a typical interior
pixel. See [QC_METRICS.md](QC_METRICS.md) §1.

**Structure preservation** — SSIM against the source outside the seam band. The metric that keeps
synthesis honest.

**Pipeline hash** — a hash of the code version, every parameter, and every model. Makes any result
reproducible. The field that makes the tool trustworthy.

**Deterministic** — same input + parameters + version = identical bytes. True for the classical
pipeline; conditional for AI.

**De-light** — removing baked-in illumination from a photograph so the albedo behaves under any
scene lighting.

**Intrinsic decomposition** — the formal name for the albedo/lighting separation problem. Ill-posed
from a single image, which is why this tool is honest about it.

**Provenance** — where an asset came from and what touched it: source rights, AI model, seed,
prompt. Recorded in `material.json` and increasingly a contractual requirement.

**AI badge** — the violet label marking any artifact a model produced. Never hidden.

**Non-tileable** — a material with no repeat unit. A correct refusal, not a failure.

**Texel source** — where the real-world scale came from: `user`, `lattice` (counted from detected
periods), `reference` (matched against a known module), or `unknown`. Recorded, because a guessed
scale is not the same as a measured one.
