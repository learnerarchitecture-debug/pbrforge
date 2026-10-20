# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

While in `0.x`, the pipeline shape may change between minor versions. `pipeline_hash` in each
`material.json` is the real compatibility marker — a material made at `0.3.1` remains reproducible
at `0.3.9` because the hash pins the code path, parameters and model versions that produced it.

---

## [Unreleased]

### Planned — Phase 1 (core)

Built already, and listed here only because a roadmap that lies about itself is worse than no
roadmap: `core/cache.py`; and the **imaging core** — `core/image.py` (colour-space-typed `Image`,
tiling-safe resample with wrap-around kernel padding, content hashing), `core/colour.py` (the sRGB
transfer functions, the map-space registry and the export gate) and `core/errors.py` (typed
failures). See `### Added — imaging core` in the 0.1.0 entry below.

Still to build:

- `stages/ingest.py`: format support, PBR-set detection (filename + statistics + cross-correlation)
- `stages/preprocess.py`: rectify, white balance, guarded auto-levels, vignette, denoise, dejpeg
- `stages/lattice.py`: FFT period detection with structural confirmation and sub-period rejection
- `stages/seamless.py`: cascade — lattice-snap → offset search → heal → spectral
- `stages/delight.py`: guided-filter illumination removal with mean-luminance restore
- `stages/derive_*.py`: height → normal, AO/cavity, roughness, metallic
- `stages/pack.py`: channel packing, filename templates, colour-space hard gates
- `stages/qc.py`: the metrics in `docs/QC_METRICS.md`
- `export/presets/`: `enscape.json`, `vray.json`, `generic-pbr.json`
- `core/params.py` and `core/context.py`: the knobs and the per-job context
- CLI: `make`, `batch` (`doctor` and `selftest` are built)
- Local database + library layout
- Studio UI: workbench, viewport, map stack, controls, export

### Planned — Phase 2

- Seam brush and mask editing
- Draggable lattice overlay
- Macro variation and detail-map split
- Alphamatte with edge decontamination
- A/B compare and diff overlay
- Batch queue UI
- Presets: Corona, Blender, Unreal, Unity, glTF, Lumion/Twinmotion
- Per-renderer handoff sheets
- Command palette
- Detachable viewport window

### Planned — Phase 3 (optional)

- Quilting / graph-cut synthesis
- AI: tiling-aware super-resolution
- AI: constrained seam refinement
- AI: semantic matting
- AI: material classification → auto preset
- KTX2 / BCn compression
- Blender addon
- Wang tile / variation atlas
- MaterialX / OpenPBR export

---

## [0.1.0] — 2026-10-18

### Added — documentation and design

- **README.md** — project overview, design principles, anti-goals
- **INSTALLATION.md** — Windows-first setup, libvips via MXE build, GPU tiers, `doctor` output
  shape, OneDrive warning
- **REQUIREMENTS.txt** — pinned core stack with optional GPU and AI sections commented out
- **LICENSE.txt** — personal-use MIT, with notes on derivative works, AI model licences, and the
  LGPL/GPL boundaries to respect if this is ever distributed
- **docs/UI_DESIGN.md** — design principles, three-column layout, full design token set
  (including the amber accent and the violet-means-AI rule), component specs, keyboard map,
  accessibility requirements, states, multi-monitor
- **docs/ARCHITECTURE.md** — single-process architecture, module map, stage protocol, DAG and
  cache-key rules, determinism and `material.json`, colour-space enforcement, performance
  budgets, AI boundary, and an explicit list of what was cut from the commercial plan
- **docs/PIPELINE.md** — every stage with parameters, defaults, methods, failure modes; the
  single-height-field rule; the seamless cascade; tiling-safe resize; the lattice detection
  detail; the mean-luminance trap in de-lighting; parameter defaults per material class
- **docs/PRESETS.md** — preset schema, filename templates, colour-space/bit-depth matrix,
  renderer cookbooks (Enscape, V-Ray, Corona, Blender, Unreal, Unity, glTF/USD/MaterialX,
  Lumion/Twinmotion/D5), physical scale and texel density, physical reference values,
  memory budgets, the "wire it into" handoff sheet
- **docs/FEATURES.md** — ordered build list plus 25 suggested features ranked by value ÷ effort,
  and an explicit anti-features section
- **docs/AI_FEATURES.md** — why AI is not the default, model catalogue with licences, feature
  catalogue, runtime architecture, evaluation gates, what not to do
- **docs/AUTOMATION.md** — full CLI reference, watch folders with intent in the filename,
  Task Scheduler recipes, project config, seven recipes, logging format, resumability rules
- **docs/PROMPTS.md** — ten build prompts for scaffolding the implementation, plus the runtime
  prompt strings for every AI feature, plus prompt-writing rules
- **docs/QC_METRICS.md** — ten metrics with formulas, thresholds and verdict logic; the human
  review checklist; and a table for resolving metric-versus-metric disagreements
- **docs/DEVELOPMENT.md** — repo layout, conventions, four-tier test strategy with the benchmark
  scorecard, performance profiling, how to add a stage/preset/model/verb, Windows gotchas, git
  conventions, definition of done
- **docs/TROUBLESHOOTING.md** — symptom → cause → fix across renders, tool behaviour, environment,
  editing, and the three cases where the right answer is to change approach
- **docs/GLOSSARY.md** — material maps, colour management, tiling, PBR, and tool-specific terms
- **ui/prototype.html** — interactive UI prototype: real procedurally generated brick material,
  real Sobel-derived normal map, real PBR shading, real 1×/3×/8× tiling, real wrap-join seam
  heat map, working de-light/heal slider, normal green-channel flip, export dialog, and a live
  server-connection badge that reports host, version, library path and secure-context state

### Added — server, CLI and packaging

The runtime is **standard library only**. See `docs/ARCHITECTURE.md` §2.1 for the reasoning and
`REQUIREMENTS.txt` for what was removed and why.

- **`pbrforge/server/simple.py`** — `ThreadingHTTPServer` app: routing, gzip, ETag, mandatory
  Content-Length, security headers with a CSP, static UI, `/api/ping`, `/api/health`, the `/pair`
  page, a path-traversal guard, port-walking bind and a console banner that names the LAN URL and
  the pairing code
- **`pbrforge/server/auth.py`** — pairing code derived from a stable secret (so it can be looked up
  after the fact on a headless host), stateless HMAC session cookies valid 90 days, 5-failure/30 s
  lockout, no session table
- **`pbrforge/server/discover.py`** — UDP discovery beacon and probe on port 7789
- **`pbrforge/tls.py`** — local CA and leaf generation with SANs covering loopback, hostname,
  `.local` and every LAN address; detects SAN coverage drift after a DHCP change; writes the
  `client-trust/` folder (`ca.pem`, `trust-ca.ps1`, `READ-ME.txt`)
- **`pbrforge/core/png.py`** — stdlib PNG codec, deterministic bytes, explicit refusals for
  interlaced and palette images rather than a wrong answer
- **`pbrforge/core/cache.py`** — content-addressed cache with atomic `os.replace` and
  `O_CREAT|O_EXCL` lock files. Deliberately *not* `msvcrt.locking`/`fcntl`: byte-range locking is
  unreliable over SMB, and the multi-PC case is the whole point
- **`pbrforge/core/synth.py`** — seamless procedural fixtures (periodic brick, value noise, fbm,
  running bond), Sobel normals and AO that wrap correctly, and the seam metric
- **`pbrforge/netutil.py`, `resources.py`, `config.py`, `doctor.py`, `selftest.py`** — interface
  enumeration, frozen-bundle resource lookup, TOML config with `PBRFORGE_*` overrides, the
  readiness report, and 13 end-to-end tests
- **`pbrforge/lifecycle.py`** — one authoritative record of the running server (`server.pid`,
  written only after the socket is bound) and a `stop` path that corroborates before it acts. It
  refuses to terminate a PID it cannot positively identify, because a stale PID file naming a
  recycled PID would otherwise kill an unrelated program. See §13.5 in `DEPLOYMENT.md`
- **`pbrforge/cli.py`** — `argparse`: `serve stop doctor selftest url code discover tls cache config
  version`, with `--config-dir/--tls/--no-tls/--no-auth/--library/--cache/--json` on all of them
- **`deploy/`** — `pbrforge.spec` (PyInstaller, with a documented `excludes` list that is the
  difference between 11 MB and 2 GB), `build-portable.ps1`, `build-installer.ps1`,
  `install-server.ps1`, `install-autostart.ps1`, `make-client-shortcut.ps1`,
  `allow-firewall.cmd`, `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `pbrforge.service`
- **`installer/pbrforge.iss`** — Inno Setup script for `PBRForge-Setup-<version>.exe`; per-user by
  default, never touches the library or the config, and removes the scheduled task and firewall
  rules on uninstall
- **`DEPLOYMENT.md`** — the three deployment models with a decision table, the secure-context
  explanation, TLS trust distribution, firewall, pairing, discovery, autostart, Docker/NAS
  specifics, the SMB locking caveat, versioned installs with rollback, backup, and a
  symptom-to-fix troubleshooting matrix

### Fixed

- **The seam metric produced a false positive on a perfectly tileable texture.** The original
  definition compared the wrap join against the *mean* of every interior join — a units mismatch
  that dilutes the reference to almost nothing on any texture with sparse strong features, and
  scored a flawless brick field **30/100**. It is now `e = wrap / p95(interior join profile)`,
  worst axis, floored at 1, with `score = 100 / e`. A perfect tile scores 100; a mild lighting
  ramp scores 60. The same definition is implemented in `core/synth.py` and in `ui/prototype.html`,
  and `selftest` asserts that a tileable fixture scores ≥ 90 with ≥ 35 points of separation from a
  broken one. See `docs/QC_METRICS.md` §1.3 for the measurements and the reasoning.
- **`animateHeal()` clobbered the value it had just applied.** It set the final state and then ran
  the animation's first frame *synchronously*, which computed `v = from` and wrote the old value
  back. Clicking Run showed the corrected score for one frame, reverted, and only converged after
  the animation finished — or never, on a backgrounded tab where `requestAnimationFrame` is
  throttled. Now the result is applied first and unconditionally, the sweep is scheduled rather
  than run inline, and its last frame lands exactly on the target.
- **The normal-map green-channel flip left a stale HUD label.** `btnFlip` called `draw()` but not
  `updateQC()`, so the viewport flipped while the label still asserted "normal: OpenGL +Y" — a
  confidently wrong label on the one control whose entire purpose is to be sure which convention
  is being exported.
- **`selftest` sent GET where it meant POST** to `/pair`, so a wrong pairing code appeared to
  return `200`. `_request()` now builds its opener from a cookie jar and a follow flag in one
  place, because two independent booleans that must agree is a bug waiting to happen.

These next ones were all found by *running the packaging path for the first time*, which had never
been done. Every one of them passed review and failed on execution.

- **The PyInstaller spec computed the project root one level too high.** `PROJECT_ROOT` used
  `Path(SPECPATH).resolve().parents[1]`; `parents[0]` is the immediate parent, so this pointed at the
  directory *containing* the repository. The visible symptom was a hard failure (`script
  <repo>\__main__.py not found`), but the quiet half was worse: `datas` skips missing files by
  design, so the documentation and `ui/` were simply omitted and the build would have "succeeded"
  with a server that served nothing. `build-portable.ps1` runs the artefact and checks that the UI
  resolves, which is why this surfaced at all.
- **The frozen executable could not import its own package.** PyInstaller runs the entry script as a
  *top-level* module, so `pbrforge/__main__.py`'s relative `from .cli import main` died with
  `ImportError: attempted relative import with no known parent package` — after the banner had
  already printed, so it read as "the installer is broken". The import is absolute now, which
  resolves in both the `-m pbrforge` and frozen cases. The handler also prints the likely causes and
  waits for a keypress only when there is a terminal, because a double-clicked exe closes its
  console instantly and the traceback is otherwise never seen.
- **The packaged exe could hang an unattended build.** `_pause_if_double_clicked()` decided purely
  on the console-process count, and `& pbrforge.exe version` from a build script satisfied it — so
  the build stopped dead on an `input()` prompt. A pause that cannot be distinguished from a hang is
  a worse failure than a window that closes early. It now also requires stdout *and* stdin to be
  terminals, and honours `PBRFORGE_NO_PAUSE`, which the build scripts set. A heuristic that can
  block an unattended script needs an override that cannot itself be misdetected.
- **Nothing could stop a running server, and the uninstaller could not delete it.** `pbrforge.exe`
  is a PyInstaller `--onefile` build: a bootloader that unpacks the bundle and a **child** that
  actually runs the app and holds the port. Both share a name and an icon; `Start-Process` and Task
  Manager show the bootloader. Ending it leaves the server running, the port bound and the
  executable locked — so the first-ever install/uninstall cycle left an 8.6 MB `pbrforge.exe` in the
  install directory with no uninstaller left to remove it. Fixed by `server.pid` (written after
  binding, naming the socket owner), a `pbrforge stop` command, and an `[UninstallRun]` step that
  stops the server before files are deleted. `selftest` now asserts that the recorded PID is the one
  that owns the port — the bug, stated as an assertion.
- **The uninstaller hung silently under `/SILENT`.** Inno Setup's plain `MsgBox` ignores `/SILENT`
  and `/VERYSILENT`; only `SuppressibleMsgBox` honours them. So the "delete your textures too?"
  dialog blocked an unattended uninstall forever on a modal window nobody could answer. Switched to
  `SuppressibleMsgBox` with a default of **NO** — an automated uninstall must never destroy a
  texture library because it could not ask.
- **The uninstaller left `install-mode.txt` behind.** It is written by the `[Code]` section rather
  than a `[Files]` entry, so the uninstaller had no record of it. Added to `[UninstallDelete]`.
- **`pbrforge url` contradicted itself about the port.** It printed the *configured* port while
  `serve --port 7804` had overridden it, and then reported the live server on the real one — two
  different answers in one screenful. It now prefers the live server's port and says so explicitly
  when the two differ.
- **A PID file written by anything other than the app was silently ignored.** A UTF-8 BOM from
  PowerShell or a hand edit made `json.loads` fail, and `read()` returned `None` — which `stop`
  reported as "no server.pid file", sending the user to look for a server that did not exist. Reads
  now tolerate a BOM, and "absent" is distinguished from "present but unparseable" in both `stop`
  and `url`.

These last ones were found by running `pbrforge tls` for the first time. The TLS *design* was
load-bearing — LAN clients only get WebGPU on a secure origin — but no command in the group had ever
been executed, so all four defects sat behind a green build.

- **`tls status` raised `AttributeError` and `tls init` crashed after doing its work.**
  `get_values_for_type(x509.DNSName)` returns plain strings, not objects with a `.value`; the code
  called `.value` on them. The certificate files were written before the crash, so the symptom was
  "it generated the certs and then reported a bug", which is the least legible shape a failure can
  take. Now `str()`, which is correct for both `DNSName` (strings) and `IPAddress` (objects) — and
  the asymmetry is commented, because it is exactly the sort of thing that gets re-broken.
- **`tls trust` raised `NameError: name 'ipport' is not defined`.** An f-string placeholder that was
  never passed, in a string that also carried three other defects: a stray `"` that would have been
  written into the READ-ME as its first character, eight spaces of Python source indentation on every
  line (the "concatenation" was inside a triple-quoted f-string, so it was never a concatenation),
  and a no-op `.replace("\n", "\n")` left over from the rewrite that produced the mess. `tls trust`
  had never produced a file for anyone to notice.
- **The trust folder told clients to use the wrong URL.** `write_trust_script` built its URL from
  `cfg.serve_port`, which returns the *plain* port until `tls = true` is set — and the folder is
  generated *before* that switch by definition. So the instructions in a folder whose entire purpose
  is HTTPS pointed at the insecure address. It now names `https://<ip>:<https_port>` explicitly, and
  mentions the http address separately as the one that redirects.
- **`config.toml` was silently ignored if it had a UTF-8 BOM.** `tomllib` rejects a BOM, and
  PowerShell's `Set-Content -Encoding utf8` adds one, as did older Notepad. The loader printed a
  warning and fell back to *every* default, so a hand-edited config ran the application on settings
  the user had not chosen — surfacing much later as "why is my library in the wrong place". Reads are
  now `utf-8-sig`, and an unparseable config is a `doctor` **FAIL** that says the defaults are in use
  rather than a warning that scrolls past.
- **`doctor` reported openssl as absent when it was installed.** It checked `shutil.which` only. Git
  for Windows ships openssl 3.x at `usr\bin\openssl.exe`, so every machine with Git has one; `doctor`
  said "not on PATH" and the TLS story looked unavailable when it was one path lookup away. It now
  searches the well-known locations and prints where it found it.
- **The `lifecycle` self-test failed whenever a server was running.** Its first assertion is "a fresh
  workspace reports nothing running", which asks about the *configured* port — and the workspace used
  the default 7788, so the test failed exactly when a real server was up, which is exactly when a
  rebuild happens. It was measuring the machine's state rather than the code's behaviour. The
  workspace now takes a port nothing else can hold.
- **A cleanup that reported success while doing nothing.** `cfg.tls_dir` is a read-only property;
  `config_dir` is what it derives from, so the self-test's attempt to give each backend its own
  certificate silently changed nothing until it was caught. Both backends now create their own output
  directory rather than trusting the caller, which is also what made calling them directly possible.
- **`doctor` checked one firewall port and there are two.** With TLS on, the plain port exists only to
  redirect to the https one, and a bookmark saved before TLS was turned on still points at it. The
  check tested `serve_port` alone, so a half-open setup reported healthy and then let that bookmark
  time out with no explanation — the exact symptom the check exists to pre-empt. It now tests every
  listening port and names the one that is missing.
- **`doctor` printed the config path twice.** `Config file  C:\...\config.toml   (from
  C:\...\config.toml)`, because `source` *is* the path once the file has been read. The suffix now
  appears only when it says something the path does not.
- **`pbrforge url` blamed `--port` for every port mismatch.** The common case is a server started with
  `--tls` while the config has no `tls` line — so the one hint printed sent you looking for an
  argument nobody used. It now distinguishes the three possibilities it can actually tell apart.
- **Microsoft Store Python's `%APPDATA%` redirection was undocumented, and it produced a false TLS
  failure.** MSIX gives the package a private copy-on-write view: writes are redirected into
  `...\LocalCache\Roaming\`, reads check that store first and fall through to the real folder. Two
  consequences, both observed on this machine while verifying the LAN HTTPS path: a *source* run can
  read the installed build's `config.toml` and then write its own shadow of anything it generates,
  and two matching-looking `tls\ca.pem` files can coexist. A client trusting one rejects a server
  holding the other, and the error is `self-signed certificate in certificate chain` — a valid
  certificate reported as invalid. This cost real time to diagnose, because the visible symptom was
  a certificate problem and the cause was a directory lookup. Now explained in full in
  `TROUBLESHOOTING.md` C12 with the observed directory listings, summarised in `INSTALLATION.md`
  step 0, and `doctor`'s Store-Python note says what actually happens rather than "sandboxes the
  filesystem".
- **The installer could not upgrade over a running server.** There was a stop at *uninstall*
  (`[UninstallRun]`) and none at install, so upgrading while the server was up left the old
  `pbrforge.exe` locked — and because a packaged build is two processes, closing "pbrforge.exe" in
  Task Manager closes the bootloader and leaves the child holding the port. That is the precise
  failure that reads as "the upgrade worked" while the old version keeps serving. `PrepareToInstall`
  now runs the old build's `stop`, which identifies the process by the port it owns rather than by
  name, and ignores failure on purpose: an install must never be blocked by a server it could not
  stop, and Inno Setup's own in-use handling remains the backstop.
- **`ignoreerrors` in `[Run]`: an invalid flag, on a claim that was also false.** The entry that
  creates the certificate carried `ignoreerrors`, with a comment asserting that this was what made a
  failure non-fatal. Two things were wrong at once — the compiler rejects the flag in `[Run]` (it is
  only valid in `[UninstallRun]`), and the assertion was untrue anyway. Probed rather than assumed: a
  `[Run]` entry returning exit code 3 during a `/VERYSILENT` install produced **setup exit code 0**
  with the installation completed normally. Flag removed, and the comment now states the measured
  behaviour. The comment is longer than the entry it explains, which is the right ratio here: the next
  person to read it will otherwise "fix" the missing `ignoreerrors`.

- **The tileable fixture was not tileable at every size, because its mortar was sub-pixel.** The
  brick fixture's mortar band is `0.055 x (size/cols)` px wide — 0.29 px at 64 — and a smoothstep
  band narrower than a pixel is sampled at an arbitrary point on its ramp, so a joint's rendered
  depth depends on where the pixel grid happens to fall. The joint at `fx = 0` is always sampled at
  its centre and therefore always at full depth, while the interior joints are not: the fixture
  measured **e = 1.43 (70/100)** at 64 px while measuring a perfect 1.00 at 128 px. Not a seam in
  the code, but a seam in the *reference*, which is worse — the metric's known-good control had a
  false positive built into it, and the new tiling test would have been built on top of it. Fixed by
  flooring the band at one pixel and averaging four sub-samples per pixel for the mortar term only
  (the fbm terms are already at roughly one sample per feature and cost far more per call). The
  fixture now scores 100/100 at 16, 24, 32, 48, 64, 96, 128 and 256 px. Cost: `brick_material` at
  256 px is ~3.9 s, against ~3 s before.
- **A `SyntaxWarning` on every run, from an unescaped Windows path in a docstring.** `tls.py`
  carried `usr\bin\openssl.exe` in a non-raw docstring, so `\b` was a literal backspace — a silent
  corruption of the text that nobody would ever have read as a bug — and `\o` was an invalid
  escape, which Python 3.12 warns about and a future version rejects outright. Both escaped. It
  surfaced only because `selftest` imports the module and its stderr is visible on the console;
  nothing failed, which is exactly why it had survived three rounds of TLS testing.

### Added - TLS generation that works in a frozen build

`pbrforge tls init` had exactly one backend, `cryptography`, and a PyInstaller build cannot import a
package that was never bundled — so on an installed copy TLS generation was impossible, and the
feature existed only on a machine with a source checkout. That is the wrong way round for a feature
whose only purpose is the multi-PC setup.

There are now three layers, and `doctor` reports which one is in play:

| Layer | When it applies | What it costs |
|---|---|---|
| `cryptography`, bundled | The build machine had it. PyInstaller bundles it via `hiddenimports`, so an installed copy generates certificates with nothing else present | ~3.6 MB on the exe |
| `cryptography`, installed | Any source checkout after `pip install cryptography` | nothing |
| the `openssl` CLI | The bundle was built without `cryptography`, or `cryptography` is broken | nothing |

The openssl backend produces a byte-for-byte equivalent result: a proper local CA (`CA:TRUE`,
`pathlen:0`, `keyCertSign`), a leaf signed by it with every DNS name and LAN address in the SANs, and
`server.pem` holding leaf + CA so a client that wants the chain gets it. It excludes MSYS argument
rewriting, because Git's openssl will otherwise turn `-subj /O=…/CN=…` into a Windows path and
generate a certificate with a nonsense subject. It checks the openssl version first, because
`-addext` needs 1.1.1 and fails confusingly on anything older.

An installed build now also generates certificates with no external tool at all, which is the case
that actually matters on a second PC.

### Added — imaging core

The two rules from `ARCHITECTURE.md` §6, enforced in code rather than by convention, plus the
module the rest of the pipeline will be written against. Pure stdlib: every line of it works in a
build with nothing installed.

| Module | What it gives the pipeline |
|---|---|
| `core/image.py` | `Image(width, height, channels, space, data)` — row-major floats in 0..1 nominal, tagged with what the values *mean*. `resample(w, h, wrap=True)` pads the kernel **circularly**, so a resample cannot break tiling, and stretches it by the downscale ratio so a 4k→1k resample does not alias into moire. Pixel-centre alignment, so a same-size resample is the identity and derived maps stay registered to the albedo. Plus `to_linear()` / `to_srgb()` / `renormalise()` / `unit_error()` / `luminance()` / `from_png()` / `to_png()` / `content_hash()` |
| `core/colour.py` | The exact IEC 61966-2-1 transfer functions (piecewise, with the linear segment near black — not `c ** 2.2`, which is 1.7% out where it matters), BT.709 luma, the `MAP_SPECS` registry of 11 map kinds as the single source of truth for a map's colour space, and `assert_writable(map_kind, space)` — the export gate that raises `ColourSpaceError` before a file exists |
| `core/errors.py` | `ColourSpaceError`, `NonTileableError`, `LimitError`. A stage may raise these and let the orchestrator report them; it may not warn and continue |

Three decisions worth stating, because they are the kind that get relitigated:

- **A data map refuses gamma in both directions, by type.** `to_linear()` and `to_srgb()` raise on
  a `data` image. The failure this prevents is silent — a roughness map with a transfer function
  over it still looks like a roughness map — so a warning was not enough.
- **`renormalise()` is opt-in, not automatic.** It refuses anything that is not a 3-channel `data`
  image, because a packed AO/roughness/metallic map also has 3 channels, and normalising it would
  destroy the data while still looking plausible.
- **No untested numpy path.** The heavy dependencies remain optional layers, so the resample is pure
  Python and refuses a job above a measured operation budget with `LimitError` naming the fix
  (`pip install numpy`) rather than running for minutes in silence. Shipping a vectorised branch
  that had never executed here would have repeated exactly the mistake the TLS bugs were.

Four selftests, all core, so the packaged-build run cannot skip them: `colour space`,
`resample tiling`, `resample normals`, `image io`. The suite is 18 tests in 11.1 s.

### Notes

The pipeline **stages** are not implemented. This release is the design, the conventions, the
guardrails, the imaging core they will be written against, and a **verified transport** —
deliberately, because the two rules that prevent the majority of bugs (data maps never receive
gamma; derived maps are aligned by construction) are cheapest to enforce before there is any code
to retrofit them into, and a deployment that has been exercised end to end on the real network
stack is not something to discover at the end.

### Verified

Not claims — measurements, all taken on the development machine with the packaged artefacts rather
than the source tree.

| Check | Result |
|---|---|
| `pbrforge doctor` | **0 failed**, which is the only count that has to be zero. On this machine the frozen build reads `17 ok · 5 optional` and a source run reads `17 ok · 6 optional` — the extra `·` is the Store-Python line, which the packaged build does not have. A fresh install reads `15 ok · 7 optional`; `INSTALLATION.md` step 7 tabulates what moves. A *running* server reads `✓ Server running: pid N` with `✓ Port 7443 serving`, rather than looking like a busy port |
| `pbrforge selftest` (source) | **18/18** · 11.1 s |
| `pbrforge selftest` (inside the frozen bundle, via the build script) | **18/18** · 11.3 s, `ui/` found at `_MEIPASS\ui` (63 KB) |
| `pbrforge selftest` run from a copy while a real server held the configured port | **18/18** — the suite no longer depends on the machine's state |
| Portable build | `dist\pbrforge.exe`, **12.4 MB**, 16.3 s, runs on a machine with **no Python installed** |
| Installer build | `dist\PBRForge-Setup-0.1.0.exe`, **14.1 MB**, ISCC 6.7.3, 4.4 s, SHA-256 written to `dist\SHA256SUMS.txt` |
| Silent install | exit 0; 27 files placed including `ui/`, 12 docs and 4 deploy scripts; uninstall registry entry created |
| Installer's certificate task (on by default) | created the CA and leaf — 4 files in `tls\` — with no user action, against a fresh empty config directory |
| Upgrade over a **running** server | install exit 0; the installer stopped the live server (2 processes were running: the bootloader and the child that owns the port), released port 7788, cleared `server.pid`, and replaced the exe **in place** — `PendingFileRenameOperations` does not mention pbrforge, so no reboot was scheduled. The upgraded install then passed 18/18 |
| Non-zero exit code from a `[Run]` step | probed with a scratch `.iss`: a step returning 3 under `/VERYSILENT` gave **setup exit 0** and installed normally, which is what makes a failing `tls init` harmless |
| Installed exe, run from `C:\` with no working directory in common | `version` and `doctor` correct; resources resolved from inside the bundle |
| Installed exe serving | UI loads over HTTP, pairing returns 302 + `Set-Cookie`, **0 console messages**, 7 canvases, map thumbnails read real pixels (normal centre `118,131,255`) |
| TLS, both backends | `cryptography` and `openssl` each produce a certificate that completes a **real TLSv1.3 handshake** with chain validation *and* hostname verification. Asserted in `selftest`, not inspected by eye |
| LAN HTTPS, from a client's point of view | six checks, all passing: a client trusting `ca.pem` gets `200` over TLS; a client that has *not* installed it is rejected (so the trust step is not theatre); `/pair` renders over TLS; `/ui/` still requires pairing; plain HTTP 301s to https with the path preserved; the certificate carries `192.168.1.100` in its SANs |
| Uninstall of a **running** server | install directory **fully removed**; port released; no stray processes; `server.pid` cleared; config and library kept |
| `stop` safety guards | refuses a live foreign PID, a stale record, a corrupt record and a BOM'd record — 4/4, killing nothing |
| `stop` on a real server | `stopped pid N and released port 7443`; both 7443 and 7788 free afterwards |
| UI seam metric | 47 → 100 on Run; 100/100 tileable vs 45/100 broken (separation 55); Python and JavaScript agree |
| Resample, tiling-safety | A periodic source holds the same seam ratio across 1.5x–8x upscale (1.143 → 1.136, drift 0.007); the *same* source with edge clamping diverges to 6.96, i.e. 6.1x. A known-tileable fixture stays at 1.000 through 2x and 4x. A constant image survives resampling to 17, 33 and 100 px exactly (2e-16), which is what proves the kernel weights are normalised rather than assumed to be |
| Resample, normals | The fixture's normal map goes from 0.0017 unit-length error to **0.2764** through a Lanczos resample, and to 0.0000 once renormalised. This is the measurement that makes "renormalise after every resize" load-bearing instead of tidy |
| Colour-space gate | 9 registered data kinds x 2 conversion directions, plus the export gate in both directions — 29 refusals, all `ColourSpaceError`, none leaving a side effect on the image. sRGB 0.5 → **0.214041** (the exact curve; `0.5 ** 2.2` is 0.217638), and linear → sRGB → linear round trips to 1.1e-16 |
| Quantisation and hashing | 8-bit write/read is bit-exact; 16-bit is within **7.63e-06**, which is exactly the half-step bound (0.1 is 6553.5 in 16-bit units), and 1/3 round trips exactly because 65535 = 3 x 21845. Overshoot clamps (1.5 → 255) instead of wrapping to 127. The content hash is stable within a run, changes on a single 16-bit step, and differs between the same pixels tagged `srgb` and `data` |
| Tileable fixture | `brick_material` scores 100/100 at 16, 24, 32, 48, 64, 96, 128 and 256 px. Before the mortar was anti-aliased it scored 70/100 at 64 px (e = 1.43) — a false seam in a pattern that has none by construction |
| `deploy/*.ps1` | all 5 parse clean under the PowerShell AST parser |
| `installer/pbrforge.iss` | compiles clean under ISCC |

Two of these numbers moved for a reason worth stating: the artefacts grew by roughly 3.7 MB because
the build machine now has `cryptography` and the spec bundles it deliberately. A build machine
without it produces the 8.7 MB exe again and falls back to the openssl CLI for certificate
generation — which is exactly the case both backends are tested for.

What is still unverified: no build has been run on a second machine; the installer has not been
exercised with `firewall` / `autostart` selected, because both need elevation or a persistent
scheduled task; and `deploy/allow-firewall.cmd` remains untested for the same reason — it
self-elevates. The installer's `tls` task *has* now been exercised, since it is on by default and
the install → verify → upgrade → uninstall cycle above ran it. The client-trust folder has been
generated and its contents checked, but `trust-ca.ps1` has not been run against a real certificate
store, because doing so adds a root CA to this machine's trust store and that is the user's decision
to make, not a test's.

The imaging core adds one more honest gap. This machine has no numpy, so the resample's *large*
path has never run: above a measured operation budget it raises `LimitError` and names the fix, and
that refusal is tested, but the work it refuses is not. There is deliberately no vectorised branch,
for the same reason the TLS module's four dead code paths were found only by running them —
shipping a numpy path that had never executed here would repeat that mistake rather than fix it.
