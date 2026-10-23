# AI Features

> **Status: not implemented in PBRForge 2.0.** This is a design document for a possible future layer,
> not a description of shipped behaviour. There is no model download, no inference runtime and no
> prompt-to-texture feature in the product. The README and `pbrforge doctor` say so plainly, and the
> classical pipeline never depends on AI.

AI is a **refinement layer**, never the pipeline. Everything it touches is off by default, labelled
in the UI, pinned to a seed and a model version, reversible in one click, and has a deterministic
classical alternative sitting right next to it.

Read [PIPELINE.md](PIPELINE.md) §0 first. The single-height-field rule applies to AI too: an AI
step never processes individual maps, it produces one improved representation from which the maps
are derived.

---

## 1. Why AI is not the default

| Problem | Consequence |
|---|---|
| Diffusion is non-deterministic | A render stops being reproducible, which is unacceptable in archviz where a client may ask for a re-render six months later. Mitigation: pin seed + model hash, and bake the result to static maps so AI never runs at render time. |
| It hallucinates structure | Wrong brick bond, impossible grout, smeared detail. Worst on exactly the man-made patterns where lattice detection already solves the problem *exactly*. |
| It shifts colour and temperature locally | The seam region becomes a visible patch in the albedo. Mitigation: histogram matching in Lab space against the surrounding region, low-frequency-only generation, detail re-injection. |
| It costs VRAM and seconds | Cold starts and OOM kills make the tool feel broken. Mitigation: warm pools, VRAM guards, graceful fallback. |
| Licences vary and provenance is often unclear | You are using outputs in paid work. Mitigation: a per-model licence register, and never training on your own captures without checking terms. |
| It is seductive | Once AI is in the default path, every bug becomes "the model did something weird" and the tool stops being predictable. Keep it opt-in and the classical path stays the product. |

**The rule in one line:** AI may improve a result that is already acceptable. It may never be the
reason a result is acceptable.

---

## 2. Model catalogue

All optional, all local, all checksum-verified into `models/`.

| Key | Task | Typical model class | VRAM | Licence | Deterministic |
|---|---|---|---|---|---|
| `classify-material` | Material type → preset suggestion | Linear probe over CLIP/SigLIP embeddings | < 1 GB | per backbone | Yes |
| `upscale-tile` | Tiling-aware 2×/4× super-resolution | Real-ESRGAN-class / PLKSR-class, ONNX | 2–4 GB | per weights | Yes (with seed) |
| `matte-foliage` | Semantic matting for foliage, decals, railings | U²-Net / RMBG-class | 1–2 GB | per weights | Yes |
| `seam-v3` | Seam-band inpainting | SDXL/FLUX-class inpainting, or a fine-tune | 6–10 GB | **check carefully** | Seeds yes; hardware no |
| `normals-macro` | Monocular normals for macro form (marble slabs, sculpted relief) | Marigold-class / DSINE-class | 4–6 GB | per weights | Yes (with seed) |
| `intrinsic-v2` | Photo → albedo/shading (full PBR from one photo) | Intrinsic decomposition net | 4–6 GB | per weights | Yes (with seed) |
| `dedefect` | Scratch, dust, watermark, text removal | LaMa-class inpainting | 2–4 GB | per weights | Yes |

**`models/manifest.json` is the source of truth** and every entry carries: key, task, source URL,
sha256, licence, VRAM requirement, whether it is deterministic, and what leaves the machine
(nothing, for all of them — inference is local).

### Licence policy

- A model may only be enabled if its licence is recorded and permits the intended use.
- Diffusion weights derived from web-scraped training data are marked `provenance: unclear` and
  **cannot be used for marketplace/publishing workflows**, enforced in the export gate.
- No checkpoint is ever loaded from a pickle format. `safetensors` only.
- Downloads are opt-in and checksum-verified. Nothing downloads silently.

---

## 3. Feature catalogue

Ranked by value ÷ risk.

### 3.1 Material classification → auto preset ★★★

Small, fast, safe, and it removes every setup decision. Runs on a 512 px thumbnail.

Implementation: a linear probe over CLIP/SigLIP embeddings, trained or hand-fitted on ~600 labelled
texture crops across the classes in [PIPELINE.md](PIPELINE.md) §2. Output is a class + confidence,
which selects the parameter block.

**Always show the answer in plain language** and always allow a one-click override:

> *Looks like: **floor tile** (94%) · lattice detected 4 × 8 · preset **Enscape (tile)***
> *[ change ]*

Confidence below 0.6 → do not auto-apply, just suggest.

### 3.2 Tiling-aware super-resolution ★★★

The best value/risk ratio of any AI feature. Users photograph with a phone and want 4k.

**Non-negotiable constraint:** circular padding at all four borders. Upscale a tileable image
without wrap-around padding and the borders stop tiling — which turns the best feature in the tool
into a seam generator. The classical half of that constraint is already enforced and measured
(`core/image.py::resample`, `selftest :: resample tiling`); the model path must not route around it.
Any AI upscaler has to go through the same resample for its final scale step, and a property test on
the model path specifically is required before this feature ships.

```
detail_guard: 0.5     blend of the model output with a Lanczos upscale
                      prevents hallucinated micro-detail from dominating
run_after:    seamless   (never before — fix seams at native resolution first)
```

### 3.3 Seam refinement (constrained diffusion) ★★

**Only** when lattice detection failed and the classical cascade scored below threshold.

Constraints, all mandatory:

1. **Seam band only.** A 96 px band on a 4k image is 3.4% of the pixels. Never the whole image.
2. **Circular padding.** The model must see the wrap-around context, so continuity is enforced by
   construction rather than hoped for.
3. **Low frequency only.** Generate the coarse structure; re-inject the original's high-frequency
   detail so the tissue-paper look never appears.
4. **Colour matching.** Mean/std match in Lab space against the surrounding region, per channel,
   before compositing.
5. **Mask-limited.** Never regenerate anything the user has not been shown and approved.
6. **Structure gate.** Measure SSIM against the original outside the band; if it drops below
   threshold, the AI result is **rejected automatically** and the classical result is kept.
7. **Fixed seed.** Recorded in `material.json`.
8. **Always re-scored** by the same seam metric used for the classical methods. No exceptions, no
   "the model says it's good".

Prompt template in [PROMPTS.md](PROMPTS.md) §2.1.

### 3.4 Semantic matting ★★

Foliage cutouts, railings, lace, decals. Well-solved, and archviz needs it constantly.

Ship with the **edge decontamination** pass (see `alphamatte` in [PIPELINE.md](PIPELINE.md) §1.12) —
a raw matte produces fringes on every leaf and looks worse than a chroma key. The AI gives you the
mask; the classical pass makes it usable.

### 3.5 Intrinsic decomposition (photo → full PBR) ★★

The headline feature, and the most dangerous.

- Output: albedo, shading, roughness estimate — from a single photo.
- **Blend slider between classical and ML**, defaulting to a 50/50 mix. Pure ML output is usually
  too flat.
- **Show the removed shading map.** It is informative and it often makes a good dirt/AO mask by
  itself.
- **Never claim physical correctness.** The UI says: *"Estimated albedo. Real lighting and albedo
  cannot be separated from a single photo; this is a plausible separation, not the true one."*
- Confidence per map; low-confidence regions get the classical value, not a fabricated one.

### 3.6 Monocular normals for macro form ★

For slabs, sculpted relief and anything where the real geometry is macroscopic and the photo's
shading is the only evidence of it. Useful, but it is genuinely inventing geometry.

- Scale-ambiguous: needs a user-provided real-world scale or it produces wrong relief.
- Labelled **creative, not photogrammetric**.
- Cap the amplitude contribution (`ml_depth_scale`, default 1.0) and blend with the classical
  height so micro-detail stays photographic.

### 3.7 Defect and watermark removal ★

Scratches, dust, specks, stains, signage, text. Well-solved by inpainting.

**Ethical constraint:** if a watermark is detected, the tool **warns and refuses to remove it by
default** (with an explicit override that records the decision in `material.json`). Silently
stripping watermarks from other people's textures is not a feature. Text/logos on a building
photograph you own is a legitimate use; a stock-photo watermark is not.

### 3.8 Prompt-to-texture and prompt-to-variation ★ marketing, weak retention

Generate a tileable material from text, or generate variants of an existing one.

- Separate screen, clearly labelled as generative, never in the normal pipeline.
- Requires lattice-aware generation with circular padding, or it produces beautiful textures that
  cannot tile.
- Always records `provenance: generated` in `material.json`, with the prompt and model hash.
- Honestly: this is a fun feature. It will not save you more time than photographing the thing
  you actually have.

### 3.9 Natural-language parameter editing ★

*"Make it wetter."* *"Less relief, more variation."* *"Warmer."*

A local LLM (or a hosted one, if you opt in) that maps a sentence onto the existing parameter
schema, shows the proposed diff, and waits for confirmation. It is a nice interface on top of
knobs you already have, it costs nothing when absent, and it makes the forty advanced parameters
accessible without reading documentation.

**Requirement:** it must show the parameter diff before applying. An LLM that silently changes
eight numbers is worse than no LLM.

### 3.10 Natural-language library search ★

*"rough grey concrete, 4k, no AI"* → filter the library. CLIP embeddings + SQLite. Cheap, pleasant,
and it makes the library usable once it passes a few hundred materials.

### 3.11 A "AI seam judge" ★

An alternative seam metric learned from human ratings, instead of the gradient-based one.

Attractive, and probably better correlated with what people actually see. But: you need labelled
data, it can disagree with the deterministic metric, and two metrics that disagree is worse than
one imperfect metric. **Only worth it after two years of your own ratings exist.** Until then, keep
the deterministic metric and let the human eye be the tiebreaker.

---

## 4. Runtime architecture

```
┌─────────────────────────────────────────────────────────────┐
│ ai/registry.py     manifest: key, task, licence, vram, sha  │
│ ai/runtime.py      session pool · VRAM guard · warm-up      │
│ ai/<task>.py       one AITask per file                      │
└─────────────────────────────────────────────────────────────┘

class AITask(Protocol):
    key: str
    available: bool          # model present AND VRAM sufficient right now
    licence: str
    provenance: str          # "clear" | "unclear"
    vram_mb: int
    deterministic: bool
    def run(self, img: Image, **kw) -> tuple[Image, AIProvenance]: ...
```

**Rules**

- `available` is checked *before* the UI offers the feature. Unavailable features are shown greyed
  with the reason ("needs 8 GB VRAM; you have 6 GB free") — never hidden, because a hidden feature
  is indistinguishable from a missing one.
- Inference is local, always. Any hosted option is per-run, explicitly consented, and shown in a
  modal that names what leaves the machine.
- Every AI artifact carries `AIProvenance`: model key, model sha256, seed, params, timestamp,
  and `provenance: clear|unclear`.
- AI stages are marked `deterministic: False` in the pipeline unless a seed is pinned *and* the
  model is on the deterministic list. The QC report states this explicitly.
- **VRAM guard:** before running, the runtime checks free VRAM; on insufficient memory it either
  tiles the work or declines with a clear message. Never an OOM crash mid-run.
- **Warm pool:** keep the two most-used models resident and unload others on a timer. A 20-second
  cold start makes a 3-second operation feel broken.

---

## 5. Evaluation

You cannot tell whether an AI feature is good without a number.

**Per-feature measures:**

| Feature | Measure | Gate |
|---|---|---|
| Classification | Accuracy vs the labelled benchmark set | ≥ 92% |
| Super-resolution | Tileability preserved (property test) + LPIPS vs ground truth | tileable always; LPIPS better than Lanczos |
| Seam refinement | Seam score improvement **and** structure SSIM retention | score up, SSIM ≥ 0.95 |
| Matting | IoU vs hand-made mattes on 30 test cutouts | ≥ 0.90 |
| Intrinsic | Blind human A/B vs the classical result | ≥ 60% preference |
| Any AI step | Determinism check: same seed twice = identical bytes | must pass |

**The structure gate is the important one.** Any AI step that changes the image outside its
declared region, or drops SSIM against the original below threshold, is rejected automatically and
the classical result kept. The model proposes; the metric disposes.

---

## 6. What not to do

| Don't | Why |
|---|---|
| Run AI in the default export path | Determinism and cost, for a marginal quality gain. |
| Let AI touch a single map on its own | Breaks the map alignment guarantee. |
| Fine-tune a diffusion model on your own captures | You will need 5–50× more labelled data than you think, and produce something worse than the off-the-shelf model. Revisit only if you have a genuinely unusual material class. |
| Add a hosted "cloud AI" tier | Privacy, latency, cost, and it makes the tool useless offline. |
| Let the AI decide the seam method for man-made patterns | Lattice detection is exact. AI is a guess. Prefer the exact answer. |
| Ship a feature whose licence you have not read | It will surface at the worst possible moment. |
| Hide the AI badge | Trust is the product. |
| Use two contradicting quality metrics | One metric with an honest name beats a committee. |
| Auto-remove watermarks by default | Not your material. |
| Train on the user's library | There is one user, and it is you. The moment this becomes a product, this becomes a lawsuit. |
