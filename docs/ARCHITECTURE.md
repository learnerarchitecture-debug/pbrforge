# Architecture

PBRForge is a **local material studio**: one Python process on one machine, a browser UI on that
machine or on the LAN, and an imaging pipeline that never leaves the box. This document describes
the architecture as built in 2.0.0, not as imagined.

---

## 1. Shape

```
  browser UI (one static HTML file, no build step)
        │  same-origin HTTP, pairing cookie + CSRF token
        ▼
  pbrforge serve ────────────────────────────────────────────────┐
        │                                                         │
        ├── /api/jobs     multipart PNG → staging → one worker ───┤
        ├── /api/capture  bracketed merge → the same worker ──────┤
        ├── /api/surface  procedural source → the same worker ────┤
        ├── /api/projects ordered layer stack → render → worker ──┤
        ├── /api/library  metadata, filters, doctor, delivery ────┤
        └── /api/vault    encrypted backup ───────────────────────┘
                                   │
                                   ▼
        config dir            library dir              cache dir
        config.toml           <slug>/maps              content-addressed
        secret.key            <slug>/previews          stage artifacts
        tls/                  <slug>/export/<preset>   staging/<job-id>
        cache/                material.json
                              qc_report.json
                              job.json
                              meta.json (user metadata)
```

**One process, many clients.** There is no broker, no queue and no worker pool. Exactly one server
holds the library and runs the pipeline; every other PC is a browser. That removes the entire class
of problem where two copies of a cache disagree about which is authoritative.

**One worker.** Jobs run serially in-process. This keeps memory predictable on a laptop and makes a
job's progress report honest. The server is threaded for I/O, not for parallel image processing.

**Three storage roots.** Code, identity and data are separate directories. Upgrades replace code;
the library and config survive uninstall unless you delete them explicitly.

---

## 2. Technology choices

| Choice | Reason | What changing it would cost |
|---|---|---|
| **Python 3.12** | The pipeline is arithmetic on typed image buffers; Python keeps it inspectable and cross-platform. The packaged installer bundles the interpreter, so the user never installs one. | A rewrite buys speed and loses the single-file deliverable. |
| **Standard-library imaging** (`core/png.py`, `core/tiff.py`, `core/image.py`) | Deterministic bytes, no decoder surprises, and a build that cannot break because numpy wheels changed. The PNG/TIFF codecs refuse what they cannot do correctly instead of guessing. | libvips/Pillow would cover more formats and add a large, version-sensitive dependency to every build. |
| **`ThreadingHTTPServer`** | One user, one LAN, one writer. A request is a function call; a thread per connection is fine at this concurrency. | An ASGI stack adds tens of megabytes and a second failure surface for no measurable LAN benefit. |
| **`argparse`** | Covers the whole command surface without entering the frozen build's dependency graph. | `click` is nicer to write; it is not nicer to audit. |
| **`cryptography`** | The only third-party runtime package. It provides AES-GCM and scrypt for the vault, Ed25519 for release signatures and certificate generation for TLS. | Rolling either primitive by hand is not a trade, it is a defect. |
| **Filesystem library + `meta.json` sidecars** | One material is one folder. It survives a copy, a backup and a sync folder; provenance stays in `material.json` and user edits live beside it. | SQLite would add a schema-migration surface for a library that is measured in hundreds of folders. |
| **Single-file HTML/JS UI, no framework** | One page, one shader, no build step, no CDN. The UI is served from the same process and runs offline. | A framework would multiply files and add a toolchain for a page this size. |
| **WebGL preview** | Enough to judge albedo, normal, roughness, AO and metallic under a moving light. | A GPU renderer is not a renderer certification; the exported maps are the truth. |

### 2.1 The dependency boundary

The rule, enforced in review:

1. The server, CLI, config, PNG/TIFF codecs, cache, jobs, projects, Set Doctor, library metadata,
   watch loop and selftest import **only the standard library**, plus `cryptography` behind lazy
   imports in the modules that need it.
2. A pipeline stage that needs a heavy library may import it lazily inside the function that uses
   it. In 2.0 there are no such stages: the pipeline is arithmetic on `core.image.Image`.
3. Nothing heavy is added to `deploy/pbrforge.spec` unless rule 1 says the product cannot run
   without it. Right now the only bundled package is `cryptography`.

What happens without `cryptography`:

| Operation | Without `cryptography` |
|---|---|
| Auto Forge, capture, surface, projects, doctor, delivery, watch | Works — none of them import it |
| Vault backup/restore | Refuses with a named reason and an install line; no weak-cipher fallback |
| Release signature verification | Refuses; an unsigned release is never auto-installed |
| TLS certificate generation | Falls back to the `openssl` CLI if present; otherwise refuses with a fix |

This is a deliberate asymmetry: the studio keeps working, while anything whose guarantee depends on
real cryptography stops rather than pretending.

---

## 3. Module map (2.0.0)

```
pbrforge/
├── __main__.py           entry point; UTF-8 console fix; import-crash reporting
├── cli.py                argparse commands (serve, make, batch, capture, surface,
│                         project, setdoctor, deliver, watch, plugin, vault,
│                         release, security, library, update, tls, cache, config,
│                         doctor, selftest, stop, url, code, discover, version)
├── config.py             config.toml (tomllib, BOM-tolerant) + PBRFORGE_* overrides
├── doctor.py             environment probing and the readiness report
├── selftest.py           20 end-to-end checks; the deployment acceptance test
├── lifecycle.py          server.pid, live-server detection, guarded `stop`
├── netutil.py            interface enumeration, LAN URLs, port ownership
├── resources.py          locates ui/ in a source tree and inside a frozen bundle
├── tls.py                local CA + leaf on two backends; client-trust folder
├── jobs.py               staging, worker, progress, promotion, previews, zip, job.json
├── update.py             GitHub release check and the signed installer path
│
├── core/
│   ├── png.py            stdlib PNG read/write; deterministic bytes; named refusals
│   ├── tiff.py           minimal uncompressed TIFF for 16-bit height maps
│   ├── image.py          colour-space-typed Image, wrap-aware resample, content hash
│   ├── colour.py         sRGB transfer functions, map-space registry, export gate
│   ├── cache.py          content-addressed artifact cache with atomic replace and locks
│   ├── synth.py          procedural fixtures and the seam metric
│   ├── ops.py            pixel operations shared by stages and studio modules
│   ├── filters.py        resampling and convolution helpers
│   ├── params.py         validated parameter dataclasses
│   ├── context.py        PipelineContext: paths, params, timings, physical hand-off
│   ├── pipeline.py       stage DAG execution, cache keys, run report
│   └── errors.py         typed failures (ColourSpaceError, NonTileableError, …)
│
├── stages/               one module per stage, identical interface
│   ├── base.py           Stage protocol: run(ctx) -> StageResult
│   ├── ingest.py  preprocess.py  lattice.py  seamless.py  delight.py
│   ├── derive_height.py  derive_normal.py  derive_ao.py  derive_rough.py
│   ├── derive_metal.py   variation.py  alphamatte.py  upscale.py
│   └── pack.py  qc.py
│
├── export/
│   ├── presets.py        preset loader and enforcement point
│   ├── presets/          blender corona enscape generic-pbr gltf lumion
│   │                     unity unreal vray-6
│   ├── writers.py        PNG/TIFF writer with correct colour tags; EXR refuses
│   ├── naming.py         filename templates
│   ├── manifest.py       machine-readable export manifest
│   └── handoff.py        README/script snippets per renderer
│
├── server/
│   ├── simple.py         ThreadingHTTPServer: routing, CSP/security headers,
│   │                     static UI, /api/*, CSRF/Host/origin checks, rate limits,
│   │                     path-traversal guard, pairing page
│   ├── auth.py           pairing code, HMAC sessions, cookie policy, lockout
│   └── discover.py       UDP beacon responder and probe client
│
├── capture.py            bracketed frame merge
├── surface.py            procedural tileable surfaces
├── project.py            ordered material projects
├── setdoctor.py          texture-set scan and safe repair
├── delivery.py           MaterialX / glTF / USD / Blender writers
├── library.py            sidecar metadata, filters, texel density
├── watch.py              polling watcher with a SHA-256 state file
├── plugins.py            explicit local plugin loader
├── security.py           AES-256-GCM vault, scrypt KDF, Ed25519, audit
│
└── ui/prototype.html     the studio UI: one self-contained file, hand-written
                          WebGL preview, Studio Lab panels, library editor

plugins/                  example local plugins (explicitly run)
deploy/                   PyInstaller spec, build scripts, release public key
installer/                Inno Setup definition
docs/                     this file plus studio, security, CLI, web-app and update guides
```

### 3.1 What exists today

| Layer | State | Notes |
|---|---|---|
| **Pipeline (15 stages)** | **built** | ingest → preprocess → lattice → seamless → delight → derive_height → derive_normal → derive_ao → derive_rough → derive_metal → variation → alphamatte → upscale → pack → qc. `upscale` is a declared disabled path; there is no silent bilinear substitute. |
| **PNG + TIFF codecs, colour gates** | **built** | deterministic bytes, 8/16-bit, explicit refusals; gamma on a data map is refused at the gate. |
| **Cache and orchestrator** | **built** | content-addressed stage cache, warm replay of maps and metrics, per-stage keys. |
| **Job manager** | **built** | validated uploads, staging, one worker, progress, promotion, previews, zip, `job.json`. |
| **Server, auth, TLS, discovery** | **built** | pairing, HMAC sessions, CSRF, Host/origin checks, rate limits, security headers, optional TLS. |
| **CLI** | **built** | every command in §3; `--json` on mutating and reporting commands. |
| **Studio modules** | **built** | capture, surface, project, setdoctor, delivery, library metadata, watch, plugins. |
| **Encryption and signatures** | **built** | chunked AES-256-GCM vault, Ed25519 release verification, security audit. |
| **UI** | **built** | Auto Forge plus Studio Lab behind one toggle; WebGL preview; library filters and metadata editor. |
| **Packaging** | **built** | PyInstaller one-file exe, Inno Setup installer, portable and per-user routes. |
| **AI service** | **not started** | Deliberately last. See §8 and `AI_FEATURES.md`. |
| **EXR** | **refused** | Names the reason and the 16-bit TIFF alternative. |

---

## 4. Pipeline DAG

Stages run in the fixed order above. Each stage declares what it needs and produces; the orchestrator
resolves those dependencies, hashes the inputs and parameters into a cache key, and either replays a
warm result or runs the stage. `core/pipeline.py` is the only place that walks the order.

Design rules that the test suite enforces:

- **Derived maps are aligned by construction.** A stage that changes geometry must produce the
  single height field the other maps derive from. Normal, AO, roughness and metallic never drift
  against albedo.
- **Data maps never receive gamma.** `core/colour.py` has one registry of map spaces and one export
  gate; the selftest asserts refusals in both directions.
- **A failed stage is a failure.** It is not retried with a weaker algorithm and it does not promote
  a partial material.

## 5. Determinism and provenance

- Every artifact in the cache is addressed by a hash of its bytes and the parameters that produced
  it.
- `material.json` records the parameter set, stage metrics, resolutions actually written and the
  `pipeline_hash` that pins the code path.
- `job.json` records the request, timing and outcome; `qc_report.json` records the verdict and the
  measured reasons.
- `meta.json` is user metadata only (tags, favourite, note, physical size, texel density). It never
  overwrites generated provenance.
- The library reports the resolution actually written, not the resolution requested.

## 6. Colour-space discipline

One rule, enforced at the type level: **sRGB-encoded colour images and linear data maps are different
types.** The registry names each map kind and its space; the export gate refuses to write a data map
through an sRGB transfer or a colour map through gamma handling. The selftest exercises both
directions and the export gate.

## 7. Performance design

- The pipeline is in-process and single-worker; a job reports stage boundaries rather than a fake
  smooth bar.
- Memory is bounded per job by the working resolution; there is no hidden global image buffer.
- The cache makes iteration cheap: changing one parameter re-runs that stage and its dependants.
- The server is threaded for concurrent browser I/O, not for parallel pixel work.
- The UI polls job status; there is no WebSocket requirement.

## 8. AI integration boundary

**AI is not implemented in PBRForge 2.0.** There is no model download, no inference runtime and no
prompt-to-texture feature. `AI_FEATURES.md` is a design document, not a description of shipped code.

If AI is ever added, the boundary is already declared: it is a refinement layer behind an explicit
configuration switch, pinned to a seed and a model hash, reversible, and never the reason a result is
acceptable. The deterministic classical path stays the product.

## 9. What was cut from the plan, and why

| Planned | Decision |
|---|---|
| FastAPI / uvicorn | Replaced by `ThreadingHTTPServer`: one user, one LAN, no benefit worth tens of megabytes. |
| libvips / Pillow / OpenCV / NumPy | Not needed for the implemented arithmetic. Optional libraries would not unlock hidden stages, because none exist. |
| SQLite library index | A folder per material plus JSON sidecars is easier to back up, copy and inspect by hand. |
| WebSocket progress | Polling is sufficient at this scale. |
| EXR writer | Needs a half-float encoder; refuses with a named reason and points at 16-bit TIFF. |
| AI service | Deferred entirely; see §8. |
| Cloud sync, accounts, telemetry | Out of scope for a personal local tool. |

## 10. Extension points

| Point | How to extend |
|---|---|
| **Presets** | Add a JSON file under `pbrforge/export/presets/`; the loader validates every token and format and refuses unknown names rather than guessing. |
| **Stages** | Implement the `Stage` protocol and register in `stages/__init__.py`; the orchestrator handles caching and ordering. |
| **Delivery formats** | Add a writer in `delivery.py`; textures are copied rather than re-encoded. |
| **Plugins** | A plugin folder with a manifest; run explicitly with `pbrforge plugin run`. Not sandboxed — treat it as local code. |
| **Update policy** | The signature requirement is a constant (`MIN_SIGNED_VERSION`); the public key lives in `update.py` and `deploy/release-signing-public.pem`. |
