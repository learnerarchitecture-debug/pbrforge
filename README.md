# 🧱 PBRForge

### Turn a photographed texture into a tileable, render‑ready PBR material in one pass.

Drop in a photo of a brick wall, a floor tile, a wood plank, a fabric swatch. Get back a
seamless material set — **albedo, normal, roughness, metallic, AO, height** — packaged with the
right colour spaces, bit depths and filenames for **Enscape, V‑Ray, Corona and Blender**.

Built for one person's own library. 🙅‍♂️ No accounts, no cloud, no subscription. Runs locally.

---

## 📖 Documentation Index

| 📄 Document | 👁️ Read it when |
|---|---|
| **[INSTALLATION.md](INSTALLATION.md)** | Setting up one machine. Start here if you just cloned this. |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Running it on a LAN. One server, many clients, HTTPS, firewall, upgrades. |
| **[docs/UI_DESIGN.md](docs/UI_DESIGN.md)** | Building or changing the interface. Contains the design system, tokens, layout, screens, keyboard map. |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Understanding how the pieces fit, or deciding what to add. |
| **[docs/PIPELINE.md](docs/PIPELINE.md)** | Working on the algorithms. Every stage, every parameter, every default. |
| **[docs/PRESETS.md](docs/PRESETS.md)** | Wiring output into a renderer. The Enscape/V‑Ray/Corona/Blender cookbook. |
| **[docs/FEATURES.md](docs/FEATURES.md)** | Planning what to build next. Includes ~40 suggested features with effort estimates. |
| **[docs/AI_FEATURES.md](docs/AI_FEATURES.md)** | Adding or tuning AI. Models, prompts, constraints, failure modes. |
| **[docs/AUTOMATION.md](docs/AUTOMATION.md)** | Batching, watch folders, scheduled runs, CLI recipes. |
| **[docs/PROMPTS.md](docs/PROMPTS.md)** | Handing work to an AI coding agent, or editing the runtime AI prompts. |
| **[docs/QC_METRICS.md](docs/QC_METRICS.md)** | Defining "good enough". Metric formulas, thresholds, reporting. |
| **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** | Committing code. Layout, conventions, tests, how to add things. |
| **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | Something looks wrong in the render. Symptom → cause → fix. |
| **[docs/GLOSSARY.md](docs/GLOSSARY.md)** | You forgot what ORM stands for again. |

💡 **Live UI prototype:** open [`ui/prototype.html`](ui/prototype.html) in Chrome or Edge.
It is a real working preview — actual derived maps, actual PBR shading, actual tiling and
seam‑heatmap maths, no build step required.

---

## 🚀 Quickstart

Three ways in. Pick the first unless you are changing code.

### 1. Install it (no Python required)

Run `PBRForge-Setup-<version>.exe`. It installs per‑user without Administrator, starts the server,
and prints the address to open on your other PCs. Nothing else on the machine is touched.

```powershell
# Or, from a source checkout, build the installer yourself:
powershell -ExecutionPolicy Bypass -File deploy\build-installer.ps1

# Stop it again, from any window on the host:
pbrforge stop
```

🛑 `pbrforge stop` is not a convenience wrapper around Task Manager — see
[DEPLOYMENT.md § Stopping the server](DEPLOYMENT.md#stopping-the-server) for why the process you can
see in Task Manager is **not** the one holding the port.

The result is a single self‑contained executable (~12 MB) with no runtime dependencies at all —
see [docs/ARCHITECTURE.md §2.1](docs/ARCHITECTURE.md) for why that is an architectural rule here
rather than a nice‑to‑have.

### 2. Use it from another PC

One machine runs the server; every other PC on the LAN just opens a URL.

```powershell
# On the machine that will host it:
powershell -ExecutionPolicy Bypass -File deploy\install-server.ps1
python -m pbrforge tls init                  # so LAN clients keep WebGPU
python -m pbrforge serve                     # prints the URL and the pairing code
```

Then open `https://192.168.1.100:7443` on any other PC and enter the code once. Full walkthrough,
including the firewall step that everybody hits and the reason HTTPS is load‑bearing rather than
just hygiene: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

### 3. Work on the code

```powershell
# Get the code somewhere OUTSIDE OneDrive (see INSTALLATION.md, step 0)
cd C:\dev
git clone https://github.com/learnerarchitecture-debug/pbrforge.git
cd pbrforge

# The server needs nothing installed. Verify that first:
python -m pbrforge doctor                    # should end in "ready to run"
python -m pbrforge selftest                  # 18/18

# Optional, and the only dependency with a real UX cost:
python -m pip install cryptography           # local HTTPS for LAN clients

# Start it:
python -m pbrforge serve
```

🖼️ The imaging stack (libvips, numpy, OpenCV) is **not** installed yet, deliberately. The imaging core
(`core/image.py`, `core/colour.py`) is pure stdlib and runs without any of it; above a measured
operation budget a large resample raises `LimitError` and names the fix rather than running for
minutes, and no numpy code path is shipped that has never executed here. When the stages land and
the vectorised paths are measured, use a venv:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r REQUIREMENTS.txt
```

---

## ✨ Design Principles

These are load‑bearing. When a feature request conflicts with one of these, the principle wins.

1. **One authoritative height field.** Fix the seams once on geometry, then derive normal, AO,
   cavity and roughness *from it*. Never process maps independently — that is the single biggest
   source of "my normal map doesn't line up with my albedo" bugs, and deriving them makes
   misalignment structurally impossible.
2. **Deterministic by default.** Same input + same parameters + same version = byte‑identical
   output. Every run stores a pipeline hash. AI steps are pinned to a seed and a model version,
   and are always off by default for exports.
3. **Show the failure.** If the seam score is 71%, say so, and jump the viewport to the worst
   spot. Silent mediocrity destroys trust in the tool faster than an outright error.
4. **Every automatic decision is reversible.** AI or heuristic output is always a new version
   with a visible before/after and a one‑click "do it the dumb way instead".
5. **The export is the product.** A beautiful preview that produces a mis‑named, gamma‑mangled
   PNG set has failed. Renderer correctness beats algorithmic elegance every time.
6. **Non‑destructive.** Nothing is ever overwritten. Stages are re‑runnable from any point.

---

## ⛔ Anti‑goals (Deliberate Non‑Features)

Kept here so they don't creep back in:

- ❌ No accounts, teams, billing, licences, telemetry, or phoning home.
- ❌ No cloud dependency. Everything works with the network unplugged.
- ❌ No procedural node‑graph authoring — that is Substance Designer's job.
- ❌ No `.sbsar` export. Adobe's Substance Automation Toolkit licence is required and restricted;
   `.mtlx` / `.usda` / `.blend` cover the same ground.
- ❌ "One click is always right" claims. The tool reports confidence, not certainty.
- ❌ No photogrammetry, modelling, UV unwrapping, or scene assembly.
- ❌ No training on your own images. Ever.

---

## 📊 Status

| 🎯 Area | ✅ State |
|---|---|
| Scoping, architecture, presets, QC design | documented |
| UI design + interactive prototype | ✅ `ui/prototype.html` — real material, real WebGL preview, real seam metric |
| Server (HTTP, pairing, HTTPS, discovery) | ✅ built — stdlib only, no dependencies |
| CLI, config, `doctor`, `selftest` | ✅ built — 18/18 end‑to‑end tests |
| Imaging core (typed image, tiling‑safe resample, colour‑space gate) | ✅ built — pure stdlib; enforces the two rules in code, with 4 selftests |
| TLS certificate generation | ✅ built — `cryptography` or the `openssl` CLI; both verified with a real handshake |
| Packaging (portable `.exe`, installer, Docker, systemd) | ✅ built and **verified** — both artefacts compiled; install/use/uninstall cycle run end to end |
| LAN HTTPS, from a client's point of view | ✅ verified — six checks including "a client that has not trusted the CA is refused" |
| Pipeline stages (ingest → seamless → derive → pack) | ⬜ to build — see [docs/PIPELINE.md](docs/PIPELINE.md) |
| Library index + job orchestration | ⬜ to build |
| AI layers (super‑res, seam refinement, matting) | ⬜ optional, later |
| Automation (watch folder, scheduled audit) | ⬜ after core |

The deployment layer and the imaging core are finished before the stages, and that ordering is
deliberate. The two rules that prevent most pipeline bugs — data maps never receive gamma, derived
maps are aligned by construction — are cheapest to enforce before there is code to retrofit them
into, and they are now enforced at the type level rather than by convention. A transport that has
been exercised end to end on the real network stack is also not something to discover at the end.

See [docs/FEATURES.md](docs/FEATURES.md) for the ordered build list.

---

## 📄 Licence

Personal use. See [LICENSE.txt](LICENSE.txt).

---

*Generated with ❤️ by OpenCode — a coding agent harness.*