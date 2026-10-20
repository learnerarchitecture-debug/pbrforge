# Renderer Presets & Export

This is where correctness actually lives. A perfect material that exports with a gamma'd
roughness map has failed. Read this before touching anything under `export/`.

> **Verify against the version you actually run.** Renderer conventions change between majors
> (V-Ray 5 → 6 → 7, Corona 7 → 9, Enscape 3.x → 4.x). Every preset carries
> `"renderer_versions_tested"` and a `"verified"` flag. Anything unverified is labelled
> **community** in the UI, never **verified**.

---

## 1. Preset schema

`export/presets/<name>.json`

```json
{
  "name": "vray-6",
  "label": "V-Ray 6 (3ds Max)",
  "target_renderer": "vray",
  "renderer_versions_tested": ["6.2", "7.0"],
  "verified": true,
  "default": false,

  "maps": [
    { "key": "albedo",   "file": "{material}_albedo_{res}.png",
      "space": "srgb",  "bit": 8,  "format": "png",  "slot": "Diffuse",
      "notes": "Bitmap gamma must be Override 1.0 — the file is already sRGB-tagged." },

    { "key": "normal",   "file": "{material}_normal_{res}.png",
      "space": "data",  "bit": 16, "format": "png",  "slot": "Bump / Normal",
      "orientation": "opengl", "strength_default": 1.0 },

    { "key": "roughness","file": "{material}_roughness_{res}.png",
      "space": "data",  "bit": 16, "format": "png",  "slot": "Roughness",
      "mode": "roughness", "clamp": [0.05, 0.95] },

    { "key": "metallic", "file": "{material}_metallic_{res}.png",
      "space": "data",  "bit": 8,  "format": "png",  "slot": "Metalness" },

    { "key": "ao",       "file": "{material}_ao_{res}.png",
      "space": "data",  "bit": 8,  "format": "png",  "slot": "—",
      "notes": "Not wired by default; use as a mask. V-Ray has its own AO." },

    { "key": "height",   "file": "{material}_height_{res}.exr",
      "space": "data",  "bit": 16, "format": "exr",  "slot": "Displacement",
      "unit": "mm", "default_amplitude_mm": 3.0, "notes": "2D displacement, mid-level 0.5." }
  ],

  "material_defaults": {
    "ior": 1.52,
    "reflection_glossiness_mode": "roughness",
    "displacement_mode": "2d"
  },

  "handoff": {
    "snippet": "maxscript",
    "notes": [
      "Use VRayTriplanar on curved or inconsistently-UV'd geometry.",
      "Set Bitmap gamma to Override 1.0 on every data map.",
      "16-bit normals prevent banding on large flat surfaces."
    ]
  },

  "validator": "blender-headless-plane-v1"
}
```

**Rules**

- `space: "data"` is a hard promise. `pack` refuses to write a data map through any path that
  could apply gamma, and refuses to write a JPEG for any data map.
- `file` templates are resolved by `export/naming.py`. Unknown tokens are an error at load time,
  not a silent literal.
- A preset with `verified: false` cannot be selected as the default export.

---

## 2. Filename templates

```
Tokens:  {material} {map} {res} {version} {renderer} {date} {category} {scale} {project}

Default          {material}_{map}_{res}
With version     {material}_{map}_{res}_v{version}
Unreal           T_{material}_{map}
Unity            {material}_{map}
glTF / USD       {material}_{map}          (lowercase, spec-conventional)
Marketplace      {category}_{material}_{map}_{res}
```

`res` renders as `2k` / `4k` / `8k`. The export dialog previews real filenames before running —
seeing `brick_wall_red_normal_4k_v003.png` in a tree is how you catch a template mistake.

---

## 3. Colour space and bit depth matrix

| Map | Space | 8-bit OK? | Default bit | Format | Notes |
|---|---|---|---|---|---|
| Albedo | sRGB | Yes | 8 | PNG | 16 only for HDR-source work |
| Height / displacement | data | **No** | 16 | TIFF / EXR | 8-bit bands visibly on large flats |
| Normal | data | Realtime only | 16 offline, 8 realtime | PNG | 16 and renormalised after any resize |
| Roughness | data | Usually | 8 (16 if banding) | PNG | Clamp away from 0 and 1 |
| Glossiness | data | Yes | 8 | PNG | Legacy renderers; not a plain inversion |
| Metallic | data | Yes | 8 | PNG | Near-binary |
| AO / cavity | data | Yes | 8 | PNG | |
| Opacity | data | Yes | 8 (16 for mattes) | PNG + alpha | Straight vs premultiplied per renderer |
| Emissive | sRGB | Yes | 8–16 | PNG | HDR value usually lives on the material |
| Combined ORM / Mask Map | data | Yes | 8 | PNG | Never let an exporter "optimise" this to JPEG |

**Banned by the packer:** JPEG for normals, height, roughness, metallic, AO, or any packed map.
PNG's compression is fine; the artefacts are not. Albedo may be JPEG only if the preset asks for
it and no normal map is in the batch.

---

## 4. Renderer cookbook

### 4.1 Enscape

The most common target for archviz, and the one with the most traps, because it is a realtime
engine with a simplified material model.

| Map | Enscape slot | Notes |
|---|---|---|
| Albedo | Albedo (Color) | sRGB. Keep ≤ 4k. |
| Height | Height | Bump by default; "use as displacement" only where the host geometry supports it. Modest relief — heavy height reads as noise at realtime framerates. |
| Roughness | Roughness | Linear, non-colour. Keep it in the mid range: near-zero roughness in Enscape produces smeared reflections that look like a bug. |
| Metallic | Metallic | **Near-binary.** Enscape interpolates metallic smoothly and mid values look like wet plastic. |
| Normal | Height slot or normal | Enscape's material model prefers a height/bump map. Supply the height and note that the normal map is available for other targets. |
| **AO** | **Do not wire** | Enscape computes its own screen-space occlusion. Adding an AO map produces dark-in-the-wrong-places. The AO map is still exported for other renderers and for masking. |
| Emissive | Emissive | Only where the material genuinely emits. |

Enscape preset defaults:

```
ao.strength        0.0          (map exported, not wired)
height.amplitude   small        (2 mm for tile, 4 mm for brick)
roughness.clamp    [0.08, 0.95] (avoid mirror-like 0)
metallic           near-binary
resolution         4096 max     (2048 if the project is large)
```

Native package: Enscape reads material packages (`.matpkg`). Exporting one is a
**nice-to-have**, and the format is undocumented — ship a `README.txt` plus a screenshot of the
slot assignments first, and only attempt `.matpkg` if you reverse-engineer it on a version you
control. Do not promise it.

Handoff note shipped with every Enscape export:

> *Enscape: albedo → Albedo, height → Height, roughness → Roughness, metallic → Metallic.
> Leave every data map's colour space set to linear/non-colour. Do not add the AO map — Enscape
> generates its own ambient occlusion.*

---

### 4.2 V-Ray 5/6/7 (3ds Max, SketchUp, Rhino, Blender)

| Map | Slot | Settings |
|---|---|---|
| Albedo | Diffuse → Bitmap | Bitmap gamma **Override 1.0** (the file is already sRGB). |
| Roughness | Reflection → Roughness | Gamma 1.0. Modern V-Ray. |
| Glossiness (legacy) | Reflection → Glossiness | Gamma 1.0. `gloss = 1 − rough` via the preset's curve, **not** a linear inverse. |
| Metallic | Metalness | Gamma 1.0. Non-metals stay at 0. |
| Normal | Bump → Normal Map | Green: **+Y (OpenGL)** in Max. Verify per host. |
| Height | Displacement | 2D displacement, amount in **mm**, mid-level 0.5. Or V-Ray's VDisplacement for true 3D. |
| IOR | Material | 1.52 most dielectrics, 1.55–1.6 polished marble/granite, 1.76 glass. |

Extras the handoff writes:

- **VRayTriplanar** recommendation — the fix for tiled materials on curved geometry and for
  geometry with inconsistent UVs. This single note prevents the most visible class of wall
  artefacts.
- **VRayDisplacementMod** configuration lines for the generated MaxScript.
- **VRayMultiSubTex** suggestion for packing multiple maps into one bitmap to reduce RAM.

```maxscript
-- generated: import/vray/brick_wall_red_01.ms
m = VRayMtl name:"brick_wall_red_01" diffuse:(bitmaptexture filename:@"...albedo_4k.png") \
    roughnessMap:(bitmaptexture filename:@"...roughness_4k.png") \
    metalnessMap:(bitmaptexture filename:@"...metallic_4k.png") \
    bumpMap:(Normal_Bump normal_map:(bitmaptexture filename:@"...normal_4k.png"))
-- set Bitmap gamma to Override 1.0 for every data map
-- add VRayDisplacementMod: 2D, amount 3.0mm, midlevel 0.5
```

---

### 4.3 Corona 9–12

| Map | Slot | Settings |
|---|---|---|
| Albedo | Base → Color | sRGB |
| Roughness | Roughness (Corona 7+) | Linear. Older versions: Glossiness = `1 − rough` via the legacy curve. |
| Metallic | Metalness (Corona 7+) | Near-binary |
| Normal | Geometry → Normal | +Y |
| Height | Displacement | **CoronaDisplacementMod uses screen size in pixels, not millimetres.** This is the single most-missed setting in Corona exports. |
| Base IOR | Material | ~1.52 |

Handoff note:

> *Corona: displacement is controlled by **screen size (px)**, not a physical amount. Start at
> 2 px and lower it until the mortar depth reads correctly. Higher resolution height maps need a
> smaller screen size, not a larger one.*

---

### 4.4 Blender 4.x

| Map | Slot | Settings |
|---|---|---|
| Albedo | Principled → Base Color | Colour space **sRGB** |
| Roughness | Principled → Roughness | Colour space **Non-Color** |
| Metallic | Principled → Metallic | **Non-Color** |
| Normal | Normal Map node → Normal | **Non-Color**, +Y (OpenGL) |
| Height | Displacement node → Material Output | **Non-Color**; needs subdivision or Adaptive Subdivision (Cycles) |
| IOR | Principled → IOR | 1.52 default |
| Coat / Sheen / Anisotropic | per material | For car paint, velvet, brushed metal |

**The classic mistake:** forgetting `Non-Color` on the data maps. Blender then applies the sRGB
transfer curve to your roughness map and the whole surface is wrong in a way that looks
*plausible* — which is exactly why it survives review. The exported `.blend` template sets the
colour spaces for you, and the handoff snippet lists them.

**Pack a `.blend` template.** A pre-built node group with six labelled image nodes, ready to
relink, saves an archviz artist two minutes per material and costs nothing to ship.

---

### 4.5 Unreal Engine 5

| Map | Slot | Settings |
|---|---|---|
| ORM (packed) | Base Color → ORM | **sRGB off.** R = AO, G = Roughness, B = Metallic. |
| Albedo | Base Color | sRGB on. |
| Normal | Normal | **DirectX (−Y).** BC5 compression, recompute Z. |
| Height | — | Nanite/WorldHeight if genuinely needed; otherwise skip. |
| Emissive | Emissive Color | sRGB on, and the material value can exceed 1. |

Export specifics:

- Texture Group per map, plus `Compression Settings` per texture.
- Mip generation settings; Virtual Textures if the material will be tiled heavily at 4k+.
- A Python editor script that imports the folder and builds a material instance with the right
  flags — the difference between a 30-second import and a 10-minute one.

---

### 4.6 Unity HDRP / URP

| Map | Slot | Settings |
|---|---|---|
| Mask Map | R = Metallic, G = AO, B = Detail, A = **Smoothness** | Smoothness is `1 − roughness` |
| Normal | Normal Map | **OpenGL (+Y)**, set texture type to Normal Map |
| Albedo | Base Map | sRGB |

**The smoothness trap:** the alpha channel carries smoothness, and Unity's default alpha
compression is lossy enough to band it. Use BC7 for the Mask Map, and check that the alpha
channel survives whatever pipeline step touches the file next.

---

### 4.7 glTF 2.0 / USD / MaterialX

| Target | What to write |
|---|---|
| glTF 2.0 | `metallicRoughness` (G = roughness, B = metallic), separate `occlusion`, `normalTexture` with `scale` = height amplitude. Correct `KHR_texture_transform` for tiling. |
| USD | `UsdPreviewSurface` with `UsdUVTexture`, correct `sourceColorSpace` per map, `scale`/`bias` on the normal for green-channel handling. |
| MaterialX | An **OpenPBR Surface** graph. The forward-looking target; supports coat, sheen, anisotropy, subsurface in one node rather than a pile of renderer-specific hacks. |

---

### 4.8 Lumion / Twinmotion / D5

Flatten everything. These engines want albedo + normal + roughness and not much else.

- 2048 maximum, 1024 if the scene is heavy.
- No packed maps, no 16-bit, no AO.
- Height used as bump only, small amplitude.
- Ship a one-page HTML sheet per export showing a screenshot of each slot with the file name on it.

---

## 5. Physical scale and texel density

The most under-used correctness tool in archviz, and nearly free to compute.

**Texel density** = texture pixels ÷ real-world size. Ask the user for one real dimension
("this brick is 190 mm wide", "this floor tile is 600 mm") and the pipeline can compute:

```
px_per_mm = texture_width_px / real_width_mm
```

Then:

- **Recommend a resolution.** A 2 m wall at 4 px/mm needs 8192 px. If the user picked 4k, say so:
  *"4k gives 1.05 px/mm on this wall — you'll see it go soft at close range."*
- **Warn on over-sampling.** 8k on a surface seen from 20 m wastes memory for nothing.
- **Feed the normal/height amplitude** with an actual scale rather than a slider that means
  nothing.
- **Show it in the status bar** for every material, and audit it across the library
  (`pbrforge audit`).

Reference densities:

| Context | Target px/mm (px/cm) |
|---|---|
| Close-up hero shot, furniture | 4–8 px/mm (40–80 px/cm) |
| Interior floor at 2–3 m viewing | 2–4 px/mm |
| General interior walls | 1–2 px/mm |
| Exterior facade, distant | 0.3–1 px/mm |
| Background/context surfaces | < 0.3 px/mm |

**Real-world size inputs** the UI should offer as one-tap presets, because artists often do not
know them:

| Material | Typical module |
|---|---|
| Standard brick (India/UK) | 190 × 90 × 90 mm |
| Standard brick (US modular) | 194 × 92 × 57 mm |
| Floor tile large format | 600 × 600, 800 × 800, 1200 × 600 mm |
| Subway tile | 75 × 150, 100 × 200 mm |
| Wood plank | 120–220 mm wide, 1200–2200 mm long |
| Engineered parquet | 190 × 1200 mm |
| Paving slab | 450 × 450, 600 × 600 mm |
| Asphalt/road | n/a (use texture density from lane markings) |
| Wallpaper | 530 mm roll width |
| Carpet tile | 500 × 500 mm |

---

## 6. Physical reference values

Ship this as a picker that seeds the defaults and as a range check on the user's numbers.
Implausible albedo is one of the two things that makes archviz renders read as "CG"
(the other is impossible lighting).

| Material | Albedo (approx sRGB) | Roughness | Metallic | IOR |
|---|---|---|---|---|
| Asphalt, new / worn | 0.12 / 0.20 | 0.85 / 0.95 | 0 | 1.52 |
| Concrete, clean / weathered | 0.35 / 0.45 | 0.75 / 0.90 | 0 | 1.50 |
| Plaster, white / painted | 0.75 / 0.50 | 0.80 / 0.50 | 0 | 1.50 |
| Brick, red / buff | 0.25 / 0.40 | 0.85 / 0.80 | 0 | 1.52 |
| Mortar | 0.55 | 0.90 | 0 | 1.50 |
| Marble, polished / honed | 0.75 / 0.70 | 0.10 / 0.50 | 0 | 1.55–1.60 |
| Granite, polished | 0.30 | 0.12 | 0 | 1.55 |
| Terrazzo | 0.55 | 0.30 | 0 | 1.55 |
| Ceramic tile, glazed | 0.60 | 0.08–0.25 | 0 | 1.55 |
| Terracotta | 0.35 | 0.70 | 0 | 1.50 |
| Oak, sealed / raw | 0.35 / 0.45 | 0.30 / 0.70 | 0 | 1.50 / 1.40 |
| Walnut, sealed | 0.20 | 0.35 | 0 | 1.50 |
| Plywood | 0.45 | 0.60 | 0 | 1.50 |
| Steel, polished / brushed | 0.55 / 0.50 | 0.05–0.15 / 0.30–0.50 | 1 | — |
| Aluminium, brushed / anodised | 0.90 / 0.75 | 0.30–0.50 / 0.40 | 1 / **0** | — |
| Copper / brass, clean | 0.95 (tinted) | 0.10–0.30 | 1 | — |
| Gold | 1.00 (tinted) | 0.05–0.30 | 1 | — |
| Chrome | 0.90 | 0.03 | 1 | — |
| Rubber | 0.05–0.10 | 0.85–0.95 | 0 | 1.50 |
| Leather, matte / polished | 0.15 / 0.20 | 0.60 / 0.35 | 0 | 1.50 |
| Cotton fabric | 0.60 | 0.90 | 0 | 1.50 |
| Velvet | 0.05–0.35 | 0.90 (+ sheen) | 0 | 1.50 |
| Wool carpet | 0.20 | 0.95 | 0 | 1.50 |
| Grass, living / dry | 0.20 / 0.40 | 0.90 | 0 | 1.50 |
| Snow, fresh | 0.85–0.95 | 0.50–0.80 | 0 | 1.30 |
| Water / ice | — | 0.02–0.10 | 0 | 1.33 |
| Window glass | — | 0.02 | 0 | 1.52 |
| Car paint | 0.30–0.60 | 0.10 (clearcoat) | 1 (flake layer) | 1.50 + coat 1.50 |

**Anodised aluminium is the trap:** it *looks* metallic but is a dielectric oxide layer. Setting
metalness = 1 on anodised aluminium is a common and immediately visible mistake.

**Range check:** if the user's albedo mean falls outside the plausible band for the declared
category, warn once, with the numbers, and offer the reference value. Never silently clamp.

---

## 7. Memory and file-size budget

Uncompressed sizes — the number that decides whether a realtime project is viable:

| Resolution | RGBA8 | 16-bit | 6-map set (8-bit, 3ch) |
|---|---|---|---|
| 2048² | 16 MB | 32 MB | ~50 MB |
| 4096² | 64 MB | 128 MB | ~200 MB |
| 8192² | 256 MB | 512 MB | ~800 MB |

Ship a calculator in the export dialog:

> *5 maps at 4k, 8-bit = 320 MB uncompressed. KTX2/BC7 brings it to ~60 MB. Your GPU has 12 GB.
> This material fits comfortably.*

For realtime presets (Unreal, Unity, Enscape, Lumion) offer **KTX2 / BCn compression** as a
checkbox. BC5 for normals (two channels, Z reconstructed on read — halves the size and improves
quality), BC7 for albedo and packed maps, BC4 for single-channel maps.

---

## 8. The "wire it into" panel

Every export ships a text/HTML sheet. It is the highest-value artifact the tool produces, because
it is what makes the material usable six months later by someone who has forgotten the settings.

```
┌──────────────────────────────────────────────────────────────┐
│  brick_wall_red_01  ·  V-Ray 6  ·  verified 6.2 / 7.0        │
├──────────────────────────────────────────────────────────────┤
│  Diffuse       ← _albedo_4k.png        (gamma: Override 1.0) │
│  Roughness     ← _roughness_4k.png     (gamma: 1.0  DATA)    │
│  Metalness     ← _metallic_4k.png      (gamma: 1.0  DATA)    │
│  Bump/Normal   ← _normal_4k.png        (green: +Y OpenGL)    │
│  Displacement  ← _height_4k.exr        (2D · 3.0 mm · 0.5)   │
│                                                              │
│  IOR 1.52        Glossiness mode: Roughness                  │
│  Texel density   1.92 px/mm · brick module 190 mm            │
│  Seam score      94  (lattice-snap, 4 × 8 courses)           │
│                                                              │
│  Tips                                                        │
│   • Use VRayTriplanar on curved geometry                     │
│   • Keep your UVW tiling at an integer multiple of 190 mm    │
└──────────────────────────────────────────────────────────────┘
                            [ Copy as text ]  [ Write .ms script ]
```

That last tip is worth spelling out: **UV tiling on a real-world module**. If the brick module is
190 mm and the artist tiles the UVW at 2.3 repeats, the bricks will be the wrong size against a
door, and no amount of map quality fixes it. Telling the artist the module size in the export
sheet is how the material ends up correct in the scene.

---

## 9. Preset checklist for a new renderer

Before a preset is marked `verified`:

- [ ] Every map has the right colour space, and the packer enforces it.
- [ ] Normal orientation confirmed by rendering a lit sphere — bumps must catch light upper-left.
- [ ] Roughness convention confirmed (roughness vs glossiness, and the legacy curve).
- [ ] Bit depths chosen and justified.
- [ ] Displacement units documented in the renderer's own terms.
- [ ] Filenames match the renderer's conventions.
- [ ] A handoff snippet that a person can follow in 30 seconds.
- [ ] One automated headless render test (if the renderer can be automated) or one manual
      screenshot stored next to the preset.
- [ ] `renderer_versions_tested` populated with the versions actually checked.
