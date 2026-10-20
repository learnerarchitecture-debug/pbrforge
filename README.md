# 🧱 PBRForge

### Turn a photographed texture into a tileable, render‑ready PBR material in one pass.

Drop in a photo of a brick wall, a floor tile, a wood plank, a fabric swatch. Get back a
seamless material set — **albedo, normal, roughness, metallic, AO, height** — packaged with the
right colour spaces, bit depths and filenames for **Enscape, V‑Ray, Corona and Blender**.

Built for one person's own library. 🙅‍♂️ **No accounts, no cloud, no subscription.** Runs locally.

---

## 📚 Documentation Index

| 📄 Document | 🎯 Read it when |
|---|---|
| **[INSTALLATION.md](INSTALLATION.md)** | Setting up one machine. Start here if you just cloned this. |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Running it on a LAN. One server, many clients, HTTPS, firewall, upgrades. |
| **[docs/UI_DESIGN.md](docs/UI_DESIGN.md)** | Building or changing the interface. Design system, tokens, layout, screens, keyboard map. |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Understanding how the pieces fit, or deciding what to add. |
| **[docs/PIPELINE.md](docs/PIPELINE.md)** | Working on the algorithms. Every stage, every parameter, every default. |
| **[docs/PRESETS.md](docs/PRESETS.md)** | Wiring output into a renderer. Enscape/V‑Ray/Corona/Blender cookbook. |
| **[docs/FEATURES.md](docs/FEATURES.md)** | Planning what to build next. ~40 suggested features with effort estimates. |
| **[docs/AI_FEATURES.md](docs/AI_FEATURES.md)** | Adding or tuning AI. Models, prompts, constraints, failure modes. |
| **[docs/AUTOMATION.md](docs/AUTOMATION.md)** | Batching, watch folders, scheduled runs, CLI recipes. |
| **[docs/PROMPTS.md](docs/PROMPTS.md)** | Handing work to an AI coding agent, or editing runtime AI prompts. |
| **[docs/QC_METRICS.md](docs/QC_METRICS.md)** | Defining "good enough". Metric formulas, thresholds, reporting. |
| **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** | Committing code. Layout, conventions, tests, how to add things. |
| **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | Something looks wrong in the render. Symptom → cause → fix. |
| **[docs/GLOSSARY.md](docs/GLOSSARY.md)** | You forgot what ORM stands for again. |

💡 **Live UI prototype:** open [`ui/prototype.html`](ui/prototype.html) in Chrome or Edge.
Real working preview — actual derived maps, actual PBR shading, actual tiling and seam‑heatmap maths, no build step.

---

## 🚀 Quickstart

Three ways in. Pick the first unless you are changing code.

### 1️⃣ Install it (no Python required)

Run `PBRForge-Setup-<version>.exe`. Installs per‑user without Administrator, starts the server,
prints the address for other PCs.

```powershell
# Or build the installer yourself from source:
powershell -ExecutionPolicy Bypass -File deploy\build-installer.ps1

# Stop it from any window on the host:
pbrforge stop
```

🛑 `pbrforge stop` is not a Task Manager wrapper — see
[DEPLOYMENT.md § Stopping the server](DEPLOYMENT.md#stopping-the-server) for why the process you see
is **not** the one holding the port.

Result: single self‑contained executable (~12 MB), zero runtime deps —
see [docs/ARCHITECTURE.md §2.1](docs/ARCHITECTURE.md) for why that's an architectural rule.

### 2️⃣ Use it from another PC (LAN)

One machine runs the server; every other PC just opens a URL.

```powershell
# On the host machine:
powershell -ExecutionPolicy Bypass -File deploy\install-server.ps1
python -m pbrforge tls init                  # HTTPS so LAN clients keep WebGPU
python -m pbrforge serve                     # prints URL + pairing code
```

Open `https://192.168.1.100:7443` on any LAN PC, enter the code once. Full walkthrough including
the firewall step and why HTTPS is load‑bearing: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

### 3️⃣ Work on the code

```powershell
# Get the code OUTSIDE OneDrive (see INSTALLATION.md, step 0)
cd C:\dev
git clone https://github.com/learnerarchitecture-debug/pbrforge.git
cd pbrforge

# Verify the server needs nothing installed:
python -m pbrforge doctor                    # ends in "ready to run"
python -m pbrforge selftest                  # 18/18

# Optional (only dep with real UX cost):
python -m pip install cryptography           # local HTTPS for LAN clients

# Start it:
python -m pbrforge serve
```

📦 The imaging stack (libvips, numpy, OpenCV) is **not** installed deliberately. Imaging core
(`core/image.py`, `core/colour.py`) is pure stdlib; above a measured budget a large resample
raises `LimitError` naming the fix instead of running for minutes. When stages land and
vectorised paths are measured, use a venv:

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r REQUIREMENTS.txt
```

---

## ✨ Design Principles

*Load‑bearing. When a feature request conflicts with one, the principle wins.*

1. **One authoritative height field.** Fix seams once on geometry, derive normal, AO, cavity, roughness *from it*. Never process maps independently — that's the #1 source of "my normal map doesn't line up with my albedo" bugs.
2. **Deterministic by default.** Same input + same params + same version = byte‑identical output. Pipeline hash stored every run. AI pinned to seed/model version, off by default for exports.
3. **Show the failure.** Seam score 71%? Say so, jump viewport to worst spot. Silent mediocrity destroys trust faster than an outright error.
4. **Every automatic decision is reversible.** AI/heuristic output = new version with visible before/after + one‑click "do it the dumb way instead".
5. **The export is the product.** Beautiful preview that produces mis‑named, gamma‑mangled PNGs has failed. Renderer correctness > algorithmic elegance.
6. **Non‑destructive.** Nothing overwritten. Stages re‑runnable from any point.

---

## ⛔ Anti‑Goals (Deliberate Non‑Features)

Kept here so they don't creep back in:

- ❌ No accounts, teams, billing, licences, telemetry, phoning home
- ❌ No cloud dependency — works with network unplugged
- ❌ No procedural node‑graph authoring — that's Substance Designer
- ❌ No `.sbsar` export — Adobe SAT licence restricted; `.mtlx`/`.usda`/`.blend` cover it
- ❌ No "one click is always right" — tool reports confidence, not certainty
- ❌ No photogrammetry, modelling, UV unwrapping, scene assembly
- ❌ No training on your images. Ever.

---

## 📊 Status

| 🎯 Area | ✅ State |
|---|---|
| Scoping, architecture, presets, QC design | documented |
| UI design + interactive prototype | `ui/prototype.html` — real material, real WebGL preview, real seam metric |
| Server (HTTP, pairing, HTTPS, discovery) | built — stdlib only, no deps |
| CLI, config, `doctor`, `selftest` | built — 18/18 end‑to‑end tests |
| Imaging core (typed image, tiling‑safe resample, colour‑space gate) | built — pure stdlib; enforces two rules in code, 4 selftests |
| TLS certificate generation | built — `cryptography` or `openssl` CLI; both verified |
| Packaging (portable `.exe`, installer, Docker, systemd) | built & **verified** — both artefacts compiled; install/use/uninstall cycle tested |
| LAN HTTPS (client POV) | verified — 6 checks including "untrusted CA refused" |
| Pipeline stages (ingest → seamless → derive → pack) | to build — see [docs/PIPELINE.md](docs/PIPELINE.md) |
| Library index + job orchestration | to build |
| AI layers (super‑res, seam refinement, matting) | optional, later |
| Automation (watch folder, scheduled audit) | after core |

The deployment layer and imaging core are finished first — deliberately. The two rules preventing most pipeline bugs (data maps never receive gamma; derived maps aligned by construction) are cheapest to enforce before code exists to retrofit them, and are now enforced at the type level. A transport exercised end‑to‑end on the real network stack isn't something to discover at the end.

See [docs/FEATURES.md](docs/FEATURES.md) for the ordered build list.

---

## 📄 Licence

**Personal use only.** See [LICENSE.txt](LICENSE.txt).

---

*Generated with ❤️ by OpenCode — a coding agent harness.*