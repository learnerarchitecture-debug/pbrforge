# Architecture

Personal-use architecture: **one process, local files, no services you have to operate.**
The full commercial plan (queues, K8s, multi-tenancy, S3, Postgres) was deliberately deleted —
see §9 for what was cut and why.

---

## 1. Shape

```
                          ┌──────────────────────────── LAN ────────────────────────────┐
                          │                                                             │
   this PC ───────────────┼──────────────── laptop ────────────── tablet / other PC ──┤
   http://127.0.0.1:7788  │  https://192.168.1.100:7443   https://192.168.1.100:7443    │
                          │  (pair once, then a 90-day signed cookie)                   │
                          └─────────────────────────────┬───────────────────────────────┘
                                                        │
┌───────────────────────────────────────────────────────▼──────────────────────────────┐
│  pbrforge serve            (the ONLY process)                                        │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │  HTTP server — stdlib ThreadingHTTPServer                                     │    │
│  │  routing · gzip · ETag · security headers · pairing · /api/* · static UI      │    │
│  └───────────────────────────────┬──────────────────────────────────────────────┘    │
│                                  │                                                   │
│  ┌───────────────────────────────▼──────────────────────────────────────────────┐    │
│  │  Orchestrator (in-process, threads)                                           │    │
│  │  stage DAG · retries · progress · cache · cancel                              │    │
│  └───────┬──────────────────────────────────────────────┬──────────────────────┘    │
│          │                                              │                            │
│  ┌───────▼──────────────────┐              ┌────────────▼───────────────┐            │
│  │  Pipeline stages         │              │  AI service (opt., off)    │            │
│  │  numpy / libvips / numba │              │  onnx / diffusers          │            │
│  │  OpenCV                  │              │  warm model pool           │            │
│  └───────┬──────────────────┘              └────────────┬───────────────┘            │
│          │                                              │                            │
│  ┌───────▼──────────────────────────────────────────────▼───────────────────────┐    │
│  │  Content-addressed cache (disk, lock files, no OS byte-range locking)        │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           ▼
        ┌───────────────────────────────────────────────────────┐
        │  Library on disk        C:\textures\                  │
        │    <material>/                                        │
        │      source/           originals, untouched           │
        │      maps/             derived maps (the truth)       │
        │      export/<preset>/  packaged, renderer-ready       │
        │      material.json     params, hashes, scale, history │
        │      preview/          sphere + 3×3 + A/B cards       │
        └───────────────────────────────────────────────────────┘
```

CLI and UI drive the **same** orchestrator. Nothing is UI-only.

**One process, many clients.** This is a personal-use tool, so there is no broker, no queue and
no worker pool — but there is exactly one server, and it is the one on the machine that has the
library. Other PCs are browsers. That single decision removes an entire category of problem
(which copy of the cache is authoritative, which machine holds the job lock, whether two machines
can edit the same material) at the cost of one always-on machine. See `DEPLOYMENT.md` for the
three supported shapes and how to pick one.

**The server is stdlib-only.** Not "mostly" — the runtime has no third-party dependencies at all,
and that is load-bearing enough to be an architectural rule rather than an implementation detail.
See §2.1.


---

## 2. Why these technology choices

| Choice | Reason | What it would cost to change |
|---|---|---|
| **Python 3.12** | The imaging and ML ecosystems are Python. This is not a language-enthusiasm decision, it is an ecosystem fact. | Rewriting the pipeline in Rust/C++ is a 3–5× effort for maybe 2× speed on the 20% of stages that are CPU-bound. Not worth it for one user. |
| **libvips (via pyvips)** | Streams and tiles internally. Handles 8k and beyond in bounded memory. Pillow loads the whole image into RAM as a Python object and dies. | Set `PBRFORGE_IMAGE_BACKEND=pillow` for a working but memory-hungry fallback. |
| **NumPy + numba** | The hot loops (Poisson solve, patch matching, AO ray-marching) are 5–60× faster in numba than in vectorised NumPy alone, with no C toolchain. | Pure NumPy works, ~4× slower. Cython/C++ extension is more setup for little further gain. |
| **OpenCV (contrib)** | `inpaint`, `seamlessClone`, `ximgproc` are production-quality implementations of exactly the two operations this tool needs most. | Reimplementing them is weeks of work for a worse result. |
| **Local HTTP server, stdlib `ThreadingHTTPServer`** | A browser UI is the least-friction way to get a good preview in Python, and the same server is what lets a second PC use the tool without an install. `http.server` is genuinely sufficient for one user on a LAN: request routing is a dict lookup, there is one writer, and a thread per connection is fine at this concurrency. | See §2.1 — this replaced FastAPI and took ~85 MB off the executable. Reverting would mean giving up the single-file `.exe`, which is the deliverable. |
| **`argparse`, not `click`** | Stdlib, covers the entire command surface, and does not appear in the frozen executable's dependency graph. | `click` is nicer to write. It is not nicer than a dependency that must be audited in a build that runs on machines you cannot debug. |
| **Own PNG codec (`core/png.py`)** | The server must serve and round-trip map previews on a machine with no Pillow. `zlib` is stdlib. Deterministic byte-for-byte output also makes the cache's content addressing testable. | Pillow for anything but PNG, libvips for anything large. The codec is deliberately narrow and refuses what it cannot do correctly (interlaced, palette) rather than guessing. |
| **SQLite** | One file, no daemon, real SQL, `json1` for tags. Enough for 100k materials. | Postgres would add an install step and buy nothing at this scale. |
| **WebGPU / WebGL2 (three.js-free, hand-written shader)** | The preview must match the pipeline's math exactly. A generic engine gets in the way. | A framework would be quicker to start and harder to keep honest. |
| **No framework on the front end** | One page, one shader, ~1500 lines. React would triple the file count for no benefit at this scale. | If the UI grows past ~5 screens, revisit. |

---

## 2.1 The runtime has no dependencies

This is the most consequential decision in the file, so it gets its own section rather than a
table cell.

**The rule:** the server, the CLI, the config, the PNG codec, the cache, the certificate tooling,
the discovery beacon and `selftest` import nothing outside the standard library. Not "few"
dependencies — none. Heavy libraries belong to the pipeline stages, behind lazy imports, and they
are loaded only when an image is actually processed.

**Why.** Three reasons, in order of how much they cost if ignored:

| Reason | The failure it prevents |
|---|---|
| **The deliverable is a single-file executable** | Every dependency is another chance for the frozen build to fail on a machine you cannot debug. Optional imports of `cv2`, `torch` and `PIL` are the single most common cause of a PyInstaller build that works on the build machine and dies on the customer's. An `excludes` list is a mitigation; having nothing to exclude is a fix. |
| **The target is "any PC on the LAN"** | The third machine is someone else's laptop. It may have no Python, no pip, a Store-sandboxed Python, a proxy, or a corporate policy against installers. "Just `pip install` it" is not an answer that survives contact with a second household. |
| **Diagnosis has to work without tooling** | When something is wrong on a machine you are not sitting at, you have `python -m pbrforge doctor` and nothing else. A stack that can have version-skew problems between NumPy and OpenCV turns a one-line answer into a dependency-resolution session. |

**The measurement.** On the development machine — a Microsoft Store Python 3.12.10, with no `py`
launcher, and none of numpy, pyvips, fastapi, uvicorn, click or rich installed, and no openssl on
`PATH`, no docker and no git on `PATH` — a stdlib-only server starts, runs its full 14-test suite,
binds the real LAN address, generates and validates certificates, serves the UI and is reachable from
another machine. That is the whole product surface, on a machine with nothing.

| Build | Approximate size | Runs on a bare Windows PC |
|---|---|---|
| stdlib-only + bundled `cryptography`, PyInstaller `--onefile` | **12.4 MB** | yes |
| the same without `cryptography` on the build machine | **8.7 MB** | yes, and it finds the `openssl` CLI for certificate generation instead |
| + FastAPI / uvicorn / pydantic / click / rich | ~90 MB | yes, but a bigger download, a slower start, and more surface to be wrong |
| + the imaging stack | ~600 MB | not the point — this layer is optional and lazy |

**What it costs.** Being honest, because this is a trade and not a free win:

| Given up | Consequence | Mitigation |
|---|---|---|
| ASGI, and its middleware ecosystem | No ready-made CORS, sessions, rate limiting, static-file handling | ~200 lines in `server/simple.py`, `server/auth.py`. Written once, no version skew. |
| WebSocket | No server-push progress | The pipeline is in-process and single-user. Progress is polled; a chunked `text/event-stream` response is available if polling ever becomes visible. |
| An ORM and SQLAlchemy | None | `sqlite3` is stdlib. The library index has not needed more. |
| Pydantic validation | Config errors are caught by hand-written checks | `Config.__post_init__` is a dozen lines and produces better messages than a schema dump, because it knows what the value is *for*. |
| FastAPI's automatic OpenAPI docs | No `/docs` page | Every endpoint is in `docs/AUTOMATION.md`, and there are nine of them. |

**The one dependency with a real UX cost.** `cryptography`, for `pbrforge tls init` and
`tls trust` only. It is optional, and here is why it is worth installing anyway:

> Browsers grant a secure context — WebGPU, clipboard writes, service workers — only on `https://`
> or on `localhost`. `http://192.168.1.100:7788` is neither, so a LAN client **silently** loses
> WebGPU and falls back to WebGL2. There is no error message; the preview is just quietly worse.
> That is why TLS is designed in rather than bolted on, and why `pbrforge doctor` and the UI's
> server badge both report the current secure-context state unprompted.

Without `cryptography` the server still starts and serves over HTTP. `doctor` says exactly what is
missing and what it costs. That is the correct degradation.

Note the asymmetry, because it is deliberate: the server never needs `cryptography`, so the stdlib
rule in §2.1 is untouched — but **certificate generation** does, and generation has to work on an
installed copy. So the spec bundles `cryptography` when the build machine happens to have it (12.4 MB
instead of 8.7 MB, and it is the reason the installed build can create a certificate with nothing
else present), and `tls.py` falls back to the `openssl` CLI when it does not. `doctor` reports which
of the two is in play. Three layers, one of which is always available, and none of them needed to
serve a page.

**The boundary, enforceable in review:**

1. Does the *server* fail without it, on a machine that is only opening a URL? If no, it cannot go
   in the runtime layer.
2. Can the pipeline stage that needs it import it lazily, inside the function that uses it? If yes,
   that is where it goes.
3. Is it in the `excludes` list in `deploy/pbrforge.spec`? It should be, unless rule 1 says
   otherwise.

---

## 3. Module map

```
pbrforge/
├── __main__.py            entry point; UTF-8 console fix; import-crash reporting with a pause
├── cli.py                 argparse commands: serve stop doctor selftest url code discover
│                          tls cache config version
├── config.py              config.toml (tomllib, BOM-tolerant) + PBRFORGE_* env overrides, path resolution
├── doctor.py              environment probing and the readiness report
├── selftest.py            18 end-to-end tests; the deployment acceptance test
├── lifecycle.py           server.pid, live-server detection, and `stop` — with guards that
│                          refuse to terminate a PID they cannot positively identify
├── netutil.py             interface enumeration, LAN URL construction, port ownership,
│                          process identity (port_owner_pid, image_name, terminate)
├── resources.py           locates ui/ in the source tree and inside a frozen bundle
├── tls.py                 local CA + leaf generation on two backends, SAN coverage drift,
│                          client-trust folder, and the secure-context explanation
│
├── core/
│   ├── png.py             stdlib PNG read/write. Deterministic bytes, explicit errors for
│   │                      interlaced and palette images rather than a wrong answer
│   ├── cache.py           content-addressed artifact cache, atomic os.replace,
│   │                      O_CREAT|O_EXCL lock files (NOT msvcrt/fcntl — see §6)
│   ├── synth.py           procedural fixture generation + the seam metric in QC_METRICS.md §1
│   ├── image.py           colour-space-typed Image, tiling-safe resize with wrap-around
│   │                      kernel padding, content hashing (float pixels in 0..1)
│   ├── colour.py          the sRGB transfer functions (IEC 61966-2-1), the map-space
│   │                      registry, and the export gate that refuses gamma on a data map
│   ├── params.py          (planned) dataclasses: every knob, every default, validated
│   ├── context.py         (planned) PipelineContext: paths, params, timings, cancel token
│   └── errors.py          typed failures (ColourSpaceError, NonTileableError, LimitError)
│
├── stages/                one module per stage, identical interface
│   ├── base.py            Stage protocol: run(ctx) -> StageResult, artifacts, metrics
│   ├── ingest.py
│   ├── preprocess.py
│   ├── lattice.py         period detection
│   ├── seamless.py        the cascade (mirror / heal / spectral / quilt / ai)
│   ├── delight.py
│   ├── derive_height.py
│   ├── derive_normal.py
│   ├── derive_ao.py
│   ├── derive_rough.py
│   ├── derive_metal.py
│   ├── variation.py       macro variation, detail split
│   ├── alphamatte.py
│   ├── upscale.py
│   ├── pack.py            channel packing, gloss<->rough, green flip
│   └── qc.py              metrics and verdicts
│
├── export/
│   ├── base.py            Exporter protocol
│   ├── naming.py          filename templates and token resolution
│   ├── writers.py         PNG/TIFF/EXR/KTX2 writers with correct colour tags
│   ├── presets/           one JSON per renderer profile
│   │   ├── enscape.json  vray.json  corona.json  blender.json
│   │   ├── unreal.json   unity.json  gltf.json   generic-pbr.json
│   └── handoff.py         generates .ms / .py / README snippets per renderer
│
├── ai/
│   ├── registry.py        model manifest: name, licence, VRAM, sha256, task
│   ├── runtime.py         ONNX / torch session pool, VRAM guard, warm-up
│   ├── classify.py        material type → preset suggestion
│   ├── seams.py           diffusion seam refinement (constrained)
│   ├── upscale.py         tiling-aware super-resolution
│   ├── matte.py           semantic matting
│   └── depth.py           monocular normals for macro form
│
├── server/
│   ├── simple.py          ThreadingHTTPServer: routing, gzip, ETag, mandatory Content-Length,
│   │                      security headers + CSP, static UI, /api/*, path-traversal guard,
│   │                      port-walking bind, banner, /pair page
│   ├── auth.py            pairing-code derivation (stable, from secret.key), HMAC session
│   │                      cookies, 5-failure/30 s lockout, no session table
│   ├── discover.py        UDP beacon responder + probe client, port 7789
│   └── jobs.py            (planned) in-process job registry and progress
│
├── library/
│   ├── store.py           on-disk layout, SQLite index, tags, search
│   └── thumbs.py          shared preview renderer (used by UI and library cards)
│
└── ui/
    └── prototype.html     the whole SPA: one self-contained file, no build step, no CDN,
                           hand-written WebGL preview + the seam heat map + the QC readout
                           (splits into app.js / preview.js / style.css only if it outgrows
                            a single file, which it has not)
```

### 3.1 What exists today, versus what is mapped above

The module map is the target. Being precise about the gap, because "the architecture says X" and
"the code does X" diverging is how a design document becomes fiction:

| Layer | State | Notes |
|---|---|---|
| **Server, auth, TLS, discovery, CLI, config, doctor, selftest** | **built** | stdlib-only at runtime; 18/18 selftest green |
| **UI** | **prototype** | `ui/prototype.html` is a real, working single-page app with a real WebGL preview and a real seam metric. It is served by the server and reachable from any LAN client |
| **PNG codec, content-addressed cache, seam metric, procedural fixtures** | **built** | in `core/`; the metric is shared verbatim with the UI and asserted in `selftest` |
| **Imaging core** (`core/image.py`, `core/colour.py`, `core/errors.py`) | **built** | the colour-space-typed image, the wrap-around resample, the map-space registry and the export gate. Pure stdlib: the type rules hold in a build with nothing installed. Four selftests, all core |
| **TLS certificate generation** | **built** | `tls.py` dispatches between `cryptography` (bundled into the `.exe` when the build machine has it) and the `openssl` CLI, so an installed copy can generate certificates with nothing installed. Both paths are covered by a real handshake in `selftest` |
| **Packaging and deployment** | **built and verified** | `deploy/`: portable `.exe`, Inno Setup installer, firewall/autostart/client-shortcut scripts, Docker, systemd. Both artefacts have been compiled and an install → run → uninstall cycle run end to end |
| **Process lifecycle** (`lifecycle.py`) | **built** | `server.pid` + `pbrforge stop`, with refusal guards. See `DEPLOYMENT.md` §13.5 for why the process Task Manager shows is the wrong one to end |
| **Pipeline stages** (`stages/`) | **not started** | the DAG, the stage protocol and the parameters are specified in `PIPELINE.md` |
| **Orchestrator, job registry, library index** | **not started** | specified here and in `PIPELINE.md` |
| **Export writers and presets** | **not started** | the preset *format* is specified in `PRESETS.md`; the export dialog in the UI already renders the package layout |
| **AI service** | **not started** | deliberately last; see `AI_FEATURES.md` |

So: the transport, the identity, the deployment, the QC metric and the two rules that prevent the
majority of pipeline bugs are real and verified. The rules are enforced at the *type* level, which
is why `core/image.py` and `core/colour.py` exist before any stage that would call them: "data maps
never receive gamma" and "derived maps are aligned by construction" are cheapest to enforce before
there is code to retrofit them into. The resample's wrap-around padding is not taken on trust — the
selftest resamples a periodic signal and a known-tileable fixture, and also resamples the same
signal with edge clamping, which fails the same check by 6x.

The stages themselves are designed and not written, and so are the orchestrator, the export writers
and the AI layer. A deployment that has been exercised end to end on the real network stack is not
something to discover at the end.

**Stage interface** — every stage looks the same, which is what makes the pipeline
re-runnable and testable:

```python
class Stage(Protocol):
    name: str
    def run(self, ctx: PipelineContext) -> StageResult: ...

@dataclass
class StageResult:
    artifacts: dict[str, str]      # key -> path in the cache
    metrics:   dict[str, float]    # merged into the QC report
    warnings:  list[Warning]
    elapsed_s: float
    deterministic: bool            # False for any unseeded AI step
```

Adding a stage is one file plus one entry in the DAG. That is the whole extension story.

---

## 4. Pipeline DAG

Stages declare what they need, so the orchestrator can skip anything already cached and rerun
only what a parameter change invalidates.

```
ingest ─► preprocess ─┬─► lattice ──► seamless ──► delight ──┬─► derive_height ─┬─► derive_normal ─┐
                      │        (or seamless directly)         │                  ├─► derive_ao ─────┤
                      │                                       │                  ├─► derive_rough ──┤
                      │                                       │                  └─► derive_metal ──┤
                      │                                       │                                     │
                      └── (bypass if the user supplied maps) ─┴──► variation ──► alphamatte ──► pack ─┴─► qc ─► export
```

**Cache key** = `sha256(stage_name + params_hash + [upstream artifact hashes])`.
A cache hit is a file copy, so iterating on the roughness remap curve costs milliseconds while
the seamless solve never reruns.

**Invalidation rule:** change a parameter in `delight` and everything downstream is stale
(`derive_*`, `pack`, `qc`). Change `roughness.remap` and only `derive_rough` onward is stale.
The UI shows exactly this as the stage list with stale chips — that is what "Run pipeline"
(from first stale stage) means.

---

## 5. Determinism and provenance

Every material records `material.json`:

```json
{
  "schema": 3,
  "material": "brick_wall_red_01",
  "created": "2026-10-18T14:12:03+05:30",
  "pipeline_hash": "9f2c1ab4…",
  "pbrforge_version": "0.1.0",
  "deterministic": true,
  "seed": 1234,
  "source": { "file": "source/brick_wall_red.jpg", "sha256": "…", "rights": "own-photo" },
  "classification": { "type": "tile", "confidence": 0.94, "lattice": [4, 8, 0.91] },
  "physical": { "size_mm": [400, 800], "px_per_mm": 1.92, "texel_source": "user" },
  "stages": [
    { "name": "seamless", "method": "lattice-snap", "params": { "band_px": 96 }, "metrics": { "seam_score": 94.1 } },
    { "name": "derive_normal", "params": { "strength": 1.0, "orientation": "opengl", "bit_depth": 16 } }
  ],
  "ai": [ { "stage": "upscale", "model": "tiling-realplksr-v2", "seed": 1234, "scale": 2 } ],
  "outputs": { "albedo": "maps/…_albedo_4k.png", "normal": "maps/…_normal_4k.png" },
  "qc": { "verdict": "pass", "seam": 94.1, "albedo_mean": 0.42, "clip_pct": 0.03 },
  "history": [ { "version": 3, "at": "2026-10-18T14:20:11", "change": "seam method lattice-snap (was poisson)" } ]
}
```

`pipeline_hash` covers **the code version, every parameter, and every model hash**. If two runs
of the same material produce different pixels and the hash matches, that is a bug, and it is a
bug that can be reproduced exactly. This single field is why studios can trust the tool, and it
costs one hash function.

---

## 6. Colour space discipline

The single largest source of "correct on screen, wrong in the render" bugs. Enforced in code,
not by convention.

```python
class Image:
    width: int
    height: int
    channels: int
    space: Space          # Space.SRGB | Space.LINEAR | Space.DATA
    data: list[float]     # row-major, w*h*c, 0..1 nominal, not clamped until write
```

- `Space` is a `str` enum, so a sidecar or a JSON payload carries `"data"` unchanged while a typo
  cannot satisfy it.
- `to_linear()` and `to_srgb()` raise `ColourSpaceError` on a `data` image, in both directions.
  That refusal *is* the enforcement point: there is no code path on which a normal, roughness, AO,
  metallic or height map receives a transfer function.
- `resample(width, height, wrap=True)` pads the kernel circularly, so a resample cannot break
  tiling. `wrap=False` clamps the edges and exists for content that is not a tile. Lanczos-3 by
  default, and the output may overshoot 0..1 — clamping happens in `to_png()`, where the range
  actually ends, so the arithmetic never lies about what it computed.
- `renormalise()` is the normal-map rule from `PIPELINE.md` §1.7, and is deliberately **opt-in**
  rather than automatic: applied to a packed AO/roughness/metallic map it destroys the data and
  still looks plausible. It refuses anything that is not a 3-channel `data` image, and it is not
  cosmetic — resampling this project's own fixture normal map takes the unit-length error from
  0.0017 to 0.2764.
- Writing runs `assert_writable(map_kind, space)` against the registry below. A kind that would be
  written in the wrong space raises `ColourSpaceError` before the file exists.
- The **only** legitimate conversion is in and out of the shading model, at two places:
  albedo into linear at preview/pack time, and back to sRGB on write.
- A job too large for pure Python raises `LimitError` naming the fix (`pip install numpy`) rather
  than running for minutes or dying. `ingest.max_pixels` is the same idea at the other end of the
  pipeline: a clean refusal beats an OOM.

The table below **is** `core/colour.py::MAP_SPECS` — one source of truth, so a map cannot be
exported in a space the registry does not know:

| Map | Space | Bit depth (default) | Written as |
|---|---|---|---|
| Albedo | sRGB | 8 | sRGB PNG |
| Height | data | 16 | 16-bit linear PNG, TIFF or EXR |
| Normal | data | 16 (offline) / 8 (realtime) | linear PNG |
| Roughness | data | 8 (16 if banding) | linear PNG |
| Metallic | data | 8 | linear PNG |
| AO / cavity | data | 8 | linear PNG |
| Curvature | data | 8 | linear PNG |
| Opacity | data | 8 (16 for mattes) | linear PNG, usually with alpha |
| Emissive | sRGB | 8–16 | sRGB PNG |
| Glossiness (legacy) | data | 8 | linear PNG |

---

## 7. Performance design

**Budgets** (target, RTX 3060-class, 4k input, defaults):

| Stage | CPU | GPU |
|---|---|---|
| ingest + preprocess | 4 s | 4 s |
| lattice | 1.2 s | 0.4 s |
| seamless (classical) | 6 s | 1.5 s |
| delight | 3 s | 0.8 s |
| derive (all maps) | 8 s | 2 s |
| pack + qc | 2 s | 2 s |
| **total** | **~25 s** | **~11 s** |

Rules that keep it there:

1. **Float32 everywhere, float16 on GPU only.** No float64 in any pixel path.
2. **Tile at 1024 px** for anything that is not a pure pointwise operation. 8k images never
   exist as one array if avoidable.
3. **Downsample before analysing, not before producing.** Lattice detection and classification run
   at 512 px; the actual seam solve runs at full resolution only inside the seam band.
4. **The seam band is small.** A 96 px band on a 4k image is 3.4% of the pixels. Never solve for
   the whole image.
5. **One upload per texture to the GPU.** Keep the pipeline resident in VRAM between stages.
6. **Preview at 1/4 res while dragging.** Two quality tiers: `fast` (¼) and `full`.
7. **Cache everything.** Iteration is the common case; a full run is the rare case.
8. **Measure or it did not happen.** Every stage records `elapsed_s` into the QC report; the
   doctor and `--profile` print the breakdown.

**Memory ceilings** (hard, enforced, not advisory): 6 GB CPU / VRAM budget auto-detected minus
1 GB, per job. Hitting the ceiling is a clean, reported failure with the stage named — never an
OOM crash mid-run.

---

## 8. AI integration boundary

AI lives behind one interface so it can be absent:

```python
class AITask(Protocol):
    available: bool                    # model present AND VRAM sufficient
    licence: str
    vram_mb: int
    deterministic: bool                # True only with a pinned seed AND model version
    def run(self, img: Image, **kw) -> Image: ...
```

Rules:

- **Optional by construction.** `ai/registry.py` reports what exists. Every UI surface that needs
  a model checks `available` first and degrades to the classical path with the reason stated.
- **Never in the default export path.** `defaults.ai_enabled = false`. Enabling AI is an explicit,
  per-material, recorded decision.
- **Always labelled.** Any AI-touched artifact carries the model name and seed into
  `material.json`, and the UI badges it violet.
- **Always reversible.** "Compare without" is a first-class action on every AI stage.

Details and the model list: [AI_FEATURES.md](AI_FEATURES.md).

---

## 9. What was cut from the commercial plan, and why

| Cut | Saved | Reason it was safe to cut |
|---|---|---|
| Postgres + pgvector | A service to run | SQLite + `json1` + a keyword index covers one person's library. Similarity search is a nice-to-have. |
| S3/MinIO object store | A service | The filesystem *is* the object store when there is one machine. |
| Celery/Redis/Temporal | Two services | An in-process thread pool with a job registry is enough for one user's concurrency. |
| Kubernetes, Docker, CI CD pipelines | Days of setup | `python -m pbrforge studio` is the deployment. |
| Accounts, orgs, roles, SSO, audit logs | Weeks | One user. |
| Billing, quotas, credits, metering | Weeks | Nothing to meter. |
| Licence server, offline activation | Weeks | Personal use. |
| Multi-tenancy, tenant isolation, signed URLs | Weeks | One tenant. |
| Renderer CI matrix (7 renderers, headless DCC) | Weeks | Keep **one** automated check (Blender headless) for the top-2 presets you actually use. |
| Observability stack (OTel, Prometheus, Grafana) | Days | Structured logs + `--profile` + the QC report. |
| KTX2/BCn compression, UDIM, Wang tiles, marketplace tools | Weeks | Add only if a real need appears. |

**Roughly 70% of the original plan, and 100% of its operational risk.**

---

## 10. Extension points

Where to add things without touching anything else:

| Want to… | Touch |
|---|---|
| …add a seam algorithm | `stages/seamless.py` + one entry in the method table + a default param block in `params.py` |
| …add a renderer | one JSON in `export/presets/` + a naming template + optional handoff snippet in `export/handoff.py` |
| …add a QC metric | `stages/qc.py` + a column in the QC dashboard + a threshold in `qc_thresholds.toml` |
| …add an AI model | a manifest row in `ai/registry.py` + one `AITask` implementation |
| …add a preview mode | one branch in `ui/preview.js` + one toolbar entry |
| …add a CLI verb | one `@click.command` in `cli.py` that calls the orchestrator |

If a change requires touching more than two of those, the abstraction is wrong — fix the
abstraction first.
