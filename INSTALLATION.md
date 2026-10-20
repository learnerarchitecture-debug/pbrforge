# Installation

Windows-first, because that is where archviz lives. Linux/macOS notes are at the bottom.

There are two routes, and most people want the first one:

| Route | For | Time | What you run |
|---|---|---|---|
| **A. The packaged build** | just using the tool | ~2 minutes | `PBRForge-Setup-0.1.0.exe` |
| **B. From source** | changing the code, or running the pipeline stages as they land | 20–40 minutes, mostly downloads | Python, then `python -m pbrforge serve` |

**Route A needs no Python and no dependency install.** The single-file executable contains the CLI,
the HTTP server, the UI and the PNG codec. Steps 1–8 below are route B; they exist because the
pipeline stages (libvips, numpy, optionally torch) will need an interpreter, and because a source
checkout is how you debug anything.

### Route A — the packaged build

Copy `PBRForge-Setup-0.1.0.exe` to the machine and double-click it. It is not code-signed, so
SmartScreen warns on first run on a new PC: **More info → Run anyway**. Signing needs a paid
certificate and is not worth it for a personal build.

The installer already does the things that are easy to forget. Nothing below is a step you have to
remember afterwards:

| What it does | Why it matters | Needs elevation? |
|---|---|---|
| Installs the exe, `ui/`, all 12 documents and the 4 deploy scripts | `doctor`, `deploy\allow-firewall.cmd` and the docs are repair paths, so they ship with the app | no |
| Runs `pbrforge tls init` (task **Create a local HTTPS certificate**, on by default) | without TLS, browsers refuse to grant a secure context on `http://192.168.x.x`, so every LAN client silently loses WebGPU and falls back to WebGL2 | no — the certificate goes to `%APPDATA%`, not a machine store |
| **Stops a running server** before copying files, and again at uninstall | the running process holds the old `pbrforge.exe` open, and it is *two* processes — closing the one in Task Manager leaves the child owning the port | no |
| Offers **Open the firewall** and **Start the server automatically when I log in** | both are opt-in, because both need Administrator | yes, if you tick them |

Afterwards, `pbrforge doctor` is the single command that answers "is this machine ready". If you are
installing on a machine that will never be the server — a laptop that just opens the URL — you do not
need this document at all; see [README.md](README.md).

**Do not install the server inside OneDrive.** That applies to route A as well, and the reason is
step 0 below.

---

## Step 0 — Do not install this inside OneDrive (important)

Your current working directory is inside `OneDrive\Documents`. **Do not put the project or your
texture library there.**

Why it will bite you:

- OneDrive locks files while syncing → `PermissionError` mid-run, corrupt intermediate files.
- Sync churn: a single 8k pipeline run writes hundreds of MB of temporaries, and OneDrive will
  happily upload all of it.
- Path length: OneDrive prefixes paths with `C:\Users\<you>\OneDrive\Documents\`, and Windows
  still has a 260-character limit in plenty of tools. Deep texture trees blow past it.
- Version history: OneDrive will version your 200 MB EXRs. People have filled drives this way.

Recommended layout:

```
C:\dev\pbrforge\              <- code (git repo)
D:\textures\                  <- your source photos and generated library
C:\dev\pbrforge\cache\        <- pipeline cache, gitignored, can be deleted any time
```

If you only have C:, use `C:\dev\` — outside the OneDrive-synced folder.

---

## Step 1 — Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 21H2+ / 11 | Long-path support helps; see Step 4 |
| Python | **3.11 or 3.12** | 3.13+ lacks wheels for a few imaging packages as of writing. Use 3.12. |
| Visual C++ Redistributable | 2015–2022 x64 | Required by almost everything native |
| Git | any | Optional if you just download a ZIP |
| Chrome / Edge | 113+ | For the UI (WebGPU path). The UI degrades to WebGL2 on older builds. |
| NVIDIA driver | 550+ | Only needed for GPU acceleration. CPU-only works. |
| Disk | ~15 GB | 3 GB env + models, rest for cache and outputs |

**GPU tiers:**

| GPU | What you get |
|---|---|
| None (integrated) | Full pipeline, classical only. Fine for 2k textures, slow at 8k. |
| GTX 1660 / RTX 3050 (6 GB) | GPU pipeline, super-resolution. AI seam refinement marginal at 4k. |
| **RTX 3060 12 GB / 4070 12 GB** | The sweet spot. Everything runs, 4k AI comfortable. |
| RTX 4080/4090 (16–24 GB) | 8k AI, batch, no compromises. |

---

## Step 2 — Install Python

1. Download Python 3.12.x from python.org ("Windows installer (64-bit)").
2. **Tick "Add python.exe to PATH"** on the first screen.
3. Tick "py launcher" (default).
4. Verify:

```powershell
py -3.12 --version
```

Do **not** use the Microsoft Store build. It is an MSIX package, and MSIX gives it a private,
copy-on-write view of `AppData\Roaming`: its **writes** are redirected into the package's own store,
while its **reads** check that store first and only then fall through to the real folder. The
practical result is that hand-edited settings can be half-seen, and two `tls\ca.pem` files can
coexist — one of which will not match the server, producing a valid certificate that is reported as
invalid. `docs/TROUBLESHOOTING.md` C12 has the full picture and the observed directory listings. The
python.org installer does not do this.

If you already have Store Python and want to keep it, point both builds at one directory outside
`AppData` so there is nothing to shadow:

```powershell
$env:PBRFORGE_CONFIG_DIR = 'C:\dev\pbrforge-config'
```

Environment variables override the built-in paths in both builds, so setting it permanently solves
the problem for the source run and the installed `.exe` at once.

---

## Step 3 — Install libvips (the one genuinely fiddly dependency)

libvips is what makes 8k images possible without running out of memory. It ships as a ZIP on
Windows, not a pip wheel with binaries.

1. Go to <https://github.com/libvips/build-win64-mxe/releases>
2. Download the **`vips-dev-w64-all-<version>.zip`** (the `all` build — the `web` and `static`
   variants are missing codecs).
3. Extract to `C:\libs\vips\` so you get `C:\libs\vips\bin\vips.exe`.
4. Add to PATH (permanent, user-level):

```powershell
[Environment]::SetEnvironmentVariable(
  "PATH",
  $env:PATH + ";C:\libs\vips\bin",
  "User"
)
```

5. **Close and reopen the terminal**, then verify:

```powershell
vips --version
# should print something like: vips-8.15.2-Tue Jun  4 ...
```

### Verify the Python binding

```powershell
python -c "import pyvips; print(pyvips.__version__); print(pyvips.version(0), pyvips.version(1), pyvips.version(2))"
```

If you see `Could not find library 'libvips-42.dll'` → the PATH entry did not take effect
(reopen the terminal), or you downloaded the `web` build (download `all`).

**Fallback if libvips refuses to cooperate:** set `PBRFORGE_IMAGE_BACKEND=pillow` and install
`Pillow` + `tifffile`. The pipeline runs, but memory use is ~4× higher and 8k inputs may fail.
Do not ship this mode as the default.

---

## Step 4 — Enable long paths (recommended)

```powershell
# Run as Administrator, once
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Then, if the repo is a git repo:

```powershell
git config --global core.longpaths true
```

Texture pipelines generate paths like
`cache\<sha256>\<sha256>\stage_name\intermediate\float32\normal_prefilter_v3.png`.
You *will* hit 260 characters eventually.

---

## Step 5 — Create the environment

```powershell
cd C:\dev\pbrforge
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel setuptools
pip install -r REQUIREMENTS.txt
```

If PowerShell refuses to run the activate script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

---

## Step 6 — GPU acceleration (optional but recommended)

The CUDA wheels are ~2.5 GB. Install the CPU-only build if you are unsure.

```powershell
# Check what you have
nvidia-smi

# CUDA build matched to your driver (cu124 shown; check pytorch.org for the current index)
pip install torch --index-url https://download.pytorch.org/whl/cu124

# GPU ONNX runtime
pip uninstall -y onnxruntime
pip install onnxruntime-gpu

# Verify
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expect `True` and your GPU name. If it prints `False` after installing CUDA wheels, the driver is
older than the CUDA runtime — update the driver, do not downgrade PyTorch.

---

## Step 7 — Verify everything

```powershell
python -m pbrforge doctor
```

This is real output from `dist\pbrforge.exe`, with the paths shortened. It is worth reading
line by line once, because it is the fastest way to know what this machine can and cannot do:

```
  PBRForge 0.1.0  ·  doctor

  ✓ Python             3.12.10  (…\pbrforge\dist\pbrforge.exe)
  ✓ Platform           Windows 11  AMD64
  ✓ Python build       not a sandboxed store build
  · Elevation          running as a normal user
  ✓ stdlib server      http.server / ssl / zlib — always available
  ✓ cryptography       50.0.2
  · not installed (5)  numpy, Pillow, libvips, OpenCV, PyTorch
                     note     numpy (the pipeline stages: pip install numpy)
                              Pillow (image format coverage beyond PNG: pip install Pillow)
                              libvips (memory-safe 8k resizing: see INSTALLATION.md step 3)
                              OpenCV (inpainting and blending: pip install opencv-contrib-python)
                              PyTorch (the optional AI stages: pip install torch   (large; only for AI features))
  · GPU                no GPU stack installed
  ✓ openssl            C:\Program Files\Git\usr\bin\openssl.exe   (not on PATH)
  · ffmpeg             not on PATH
  ✓ Config dir         C:\Users\you\AppData\Roaming\pbrforge
  ✓ Library            C:\Users\you\Documents\PBRForge\library   57.1 GB free
  ✓ Cache              C:\Users\you\AppData\Roaming\pbrforge\cache   57.1 GB free
  · Config file        C:\Users\you\AppData\Roaming\pbrforge\config.toml   (from defaults)
  ✓ LAN address        192.168.1.100   (Wi-Fi)
  ✓ Primary IP         192.168.1.100
  ✓ Server             not running
  ✓ Port 7788          free
  · TLS                off — LAN clients get the WebGL2 preview, not WebGPU
  ✓ Auth               pairing code required
  ✓ Bind address       0.0.0.0 (reachable on the LAN)
  · Firewall           no inbound rule found for TCP 7788
                     note     run deploy\allow-firewall.cmd as Administrator, or
                              netsh advfirewall firewall add rule name="PBRForge TCP 7788" dir=in action=allow protocol=TCP localport=7788 profile=private

  15 ok · 7 optional · 0 failed  →  ready to run
```

Read the **last line** first: that is the verdict. Everything above it is evidence. `✗ FAIL`
is the only thing that stops you, and every FAIL line prints the command that resolves it.
`·` lines are informational and are supposed to be there — the five uninstalled packages are
the imaging and AI stack, which the current build does not use yet.

Four notes, so that a difference does not worry you:

- **Satisfying a `·` check turns it into a `✓`, so `ok` rises by one and `optional` falls by one.**
  The `failed` count is the only one that has to stay at zero. On the machine this was captured from,
  a config file existing and a certificate being issued had already moved those two lines across,
  which is why that run reads `17 ok · 5 optional` — the same machine, further along.
- **`✓ cryptography` and `✓ openssl` are both about one feature, and you only need one of them.**
  They are the two ways `pbrforge tls init` can generate the LAN certificate that keeps WebGPU on
  client machines. The packaged build bundles `cryptography`, so `✓` there is normal; on a build
  made without it you see `·` and openssl takes over. A machine with Git installed shows a `✓`
  for openssl *even though it is not on `PATH`* — the well-known install locations are searched
  deliberately, and the line says so.
- **`Microsoft Store build detected`** appears only if you used Store Python. It is a `·`
  line, not a failure — but read step 0 and use the python.org installer anyway.
- **`Server running: pid N, port N`** replaces `not running` while the server is up, and
  `Port 7788` becomes `✓ Port 7788 serving`. A running server is *not* reported as a port
  conflict, which is a deliberate fix: it previously looked like the one warning that stops
  you, when in fact it was the thing you had just started.

---

## Step 8 — First run

```powershell
# Smoke test — generates a synthetic test texture and round-trips it
python -m pbrforge selftest                  # 18/18

# Make your first material
python -m pbrforge make "D:\photos\brick_wall.jpg" --preset enscape --out D:\textures\brick

# The UI
python -m pbrforge studio
# → http://127.0.0.1:7788

# Stop it again, from any other window
python -m pbrforge stop
```

The UI is served locally and binds to `127.0.0.1` only by default. It never talks to the internet
unless you explicitly enable an AI feature that downloads a model.

Ctrl+C stops the server in the console that owns it. `pbrforge stop` is for every other case — and
for the packaged `.exe`, where Task Manager shows you the *bootloader* rather than the process
holding the port. `DEPLOYMENT.md` §13.5 explains why, and it is worth thirty seconds of your
attention the first time the server will not go away.

---

## Step 9 — AI models (optional, later)

Skip entirely until the classical pipeline is working. When you want it:

```powershell
# Model catalogue + download (all go to models\, gitignored)
python -m pbrforge models list
python -m pbrforge models pull upscale-realplksr
python -m pbrforge models pull matting-rmbg

# Diffusion seams need a ComfyUI instance or a local diffusers setup
# Point at a running ComfyUI:
$env:PBRFORGE_COMFY_URL = "http://127.0.0.1:8188"
```

Model downloads are several GB and are checksum-verified against `models/manifest.json`.
See [docs/AI_FEATURES.md](docs/AI_FEATURES.md) for what each one does and what it costs in VRAM.

---

## Configuration

Config lives at `%APPDATA%\pbrforge\config.toml`, created on first run with safe defaults.

```toml
[paths]
cache       = "C:/dev/pbrforge/cache"
library     = "D:/textures"
temp        = ""                # empty = system temp

[performance]
backend          = "auto"       # auto | gpu | cpu
max_workers      = 0            # 0 = physical core count - 1
vram_budget_mb   = 0            # 0 = auto-detect
cpu_mem_budget_mb = 6144
tile_size        = 1024         # processing tile for huge images

[ui]
port            = 7788
bind            = "127.0.0.1"
theme           = "dark"
accent          = "amber"
preview_quality = "auto"        # auto | fast | full
detach_viewport = false         # true = open preview in a second window

[defaults]
preset          = "generic-pbr"
resolution      = 4096
deterministic   = true
ai_enabled      = false

[integrations]
blender_path    = ""
max_script_dir  = ""
```

Environment variables always win over the config file (`PBRFORGE_CACHE`, `PBRFORGE_BACKEND`, …),
which makes scheduled tasks and one-off experiments easy.

---

## Linux / macOS

Everything is cross-platform except the libvips install step.

```bash
# Debian/Ubuntu
sudo apt install libvips-dev libvips-tools
# macOS
brew install vips
```

Then the same `venv` + `pip install -r REQUIREMENTS.txt` flow. Path assembly uses forward
slashes internally, so mixed separators are fine. GPU acceleration on Linux is easier than on
Windows (better driver story); on Apple silicon, PyTorch uses MPS and the pipeline runs, but the
classical GPU kernels are CUDA/OpenCL-only, so expect CPU-class timings.

---

## Uninstall

Everything lives in three places. Delete them all and the machine is clean:

```
C:\dev\pbrforge\        (code, venv, cache)
%APPDATA%\pbrforge\     (config)
D:\textures\            (your library — do not delete this one)
```

No registry keys, no services, no scheduled tasks are created unless you explicitly install the
watch-folder agent (`python -m pbrforge agent install`).

---

## Next

- Read [docs/UI_DESIGN.md](docs/UI_DESIGN.md) and open `ui/prototype.html` to see the target UI.
- Read [docs/PIPELINE.md](docs/PIPELINE.md) before touching the algorithms.
- Read [docs/PRESETS.md](docs/PRESETS.md) before touching the export layer — that is where
  correctness actually lives.
