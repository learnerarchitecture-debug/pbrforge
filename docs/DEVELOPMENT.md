# Development

Conventions, layout, tests, and how to add things without breaking the two rules that matter
(colour space, and map alignment).

---

## 1. Repo layout

```
pbrforge/
├── README.md  INSTALLATION.md  REQUIREMENTS.txt  LICENSE.txt  CHANGELOG.md
├── docs/
│   ├── ARCHITECTURE.md  PIPELINE.md  PRESETS.md  UI_DESIGN.md
│   ├── FEATURES.md  AI_FEATURES.md  AUTOMATION.md  PROMPTS.md
│   ├── QC_METRICS.md  DEVELOPMENT.md  TROUBLESHOOTING.md  GLOSSARY.md
├── ui/prototype.html          design reference; keep it working; served by the server
├── pbrforge/
│   ├── cli.py  config.py  doctor.py  __main__.py  lifecycle.py  netutil.py
│   ├── resources.py  tls.py  selftest.py
│   ├── core/       png.py  cache.py  synth.py              ← built
│   │               image.py colour.py params.py context.py errors.py   ← planned
│   ├── server/     simple.py  auth.py  discover.py         ← built
│   │               jobs.py  ws.py                          ← planned
│   ├── stages/     one module per stage                    ← planned
│   ├── export/     base.py naming.py writers.py handoff.py presets/*.json   ← planned
│   ├── ai/         registry.py runtime.py <task>.py         ← planned
│   └── library/    store.py thumbs.py                      ← planned
├── tests/
│   ├── unit/  property/  benchmark/  failure/
│   └── fixtures/   small committed images only
├── qc_thresholds.toml
└── project.pbr.yaml.example
```

The `pbrforge/` block mixes what exists with what is planned, and the labels are load-bearing:
`ARCHITECTURE.md` §3.1 is the authoritative built-versus-planned table, and it is the one to update
when a module lands. Do not add a file to this tree without marking which it is.

---

## 2. Environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r REQUIREMENTS.txt
pip install pytest pytest-cov hypothesis ruff mypy psutil playwright
pre-commit install
```

`pre-commit` runs ruff (format + lint), mypy on `pbrforge/`, and the fast test subset. It must
stay under 15 seconds or it will get disabled, and a disabled hook is worse than none.

---

## 3. Code conventions

**Style** — ruff with `line-length = 100`, `target-version = "py312"`, rules `E,F,I,N,UP,B,SIM,RUF`.
Let the formatter decide layout; do not hand-format.

**Types** — mypy in strict mode on `core/`, `stages/`, `export/`. `numpy.typing.NDArray` for arrays,
`Literal` for enums. Runtime `assert_space` checks are *not* a substitute for type annotations.

**Naming**

| Thing | Convention | Example |
|---|---|---|
| Stage modules | noun | `seamless.py`, `derive_normal.py` |
| Parameters | `snake_case`, matching the JSON in PIPELINE.md exactly | `seam_band_px` |
| Artifact keys | `snake_case` | `height_f32`, `normal_u16` |
| Presets | `<renderer>-<major>` | `vray-6.json`, `enscape.json` |
| Cache keys | sha256 hex | — |

**Parameter names in code must match the names in `material.json` and in the UI.** Three names for
one knob is how a tool becomes unmaintainable in a year. If a name has to change, change it
everywhere in one commit, including the docs.

**Docstrings explain WHY.** The code says what it does:

```python
def restore_mean_luminance(img: Image, target_mean: float) -> Image:
    """Restore the mean after de-lighting.

    Without this, flattening illumination lowers the average albedo, every material
    comes out slightly dark, and the artist compensates by raising scene exposure --
    which then breaks every other material in the scene. The mean is restored rather
    than the histogram, because a histogram match re-introduces the gradient that
    delight just removed.
    """
```

**Hot loops** are numpy-vectorised or numba-jitted. A Python loop over pixels is a review blocker.
If it cannot be vectorised, say why in a comment and add a benchmark.

**Errors** are typed:

```python
class PBRForgeError(Exception): ...
class NonTileableError(PBRForgeError): ...    # includes the recommendation
class ColourSpaceError(PBRForgeError): ...    # includes required vs actual
class VramError(PBRForgeError): ...           # includes needed vs available
class CorruptInputError(PBRForgeError): ...   # includes byte offset where possible
```

Every raise site must produce a message that answers: which stage, what happened, what to do.
`raise ValueError("bad image")` is a review blocker.

---

## 4. Testing

Four tiers. All four exist for a reason; none is optional.

### 4.1 `tests/unit/` — fast, one function at a time

Golden-image comparison with a threshold, not exact equality (except where the operation is
supposed to be exact, in which case assert byte equality and mean it).

```python
def test_sobel_normal_matches_golden():
    h = load_fixture("fixtures/height_bump_256.f32")
    n = derive_normal(h, strength=1.0, orientation="opengl")
    assert psnr(n, load_fixture("fixtures/normal_bump_256.npy")) > 45
```

### 4.2 `tests/property/` — the invariants (hypothesis)

**State: not created yet.** The portable half of this tier is enforced today by `pbrforge selftest`,
deliberately: the frozen build has no pytest, and the packaged build is the one run where a broken
image path must not slip through. A pytest tier added here would not replace that — it explores
*more inputs* for the same invariants.

The invariants, in order of value, and where each one stands:

| # | Invariant | Enforced today by |
|---|---|---|
| 1 | Tileability survives a resample | `selftest :: resample tiling`, plus the clamped counter-example |
| 2 | A data map never receives gamma | `selftest :: colour space` — 9 kinds x 2 directions + the export gate |
| 3 | Derived maps align to the albedo | `selftest :: derived maps` (shapes, unit length) and the map-set block in `resample tiling`. The pixel-level misalignment *detector* lands with the `derive_*` stages |
| 4 | Normals stay unit length after a resize | `selftest :: resample normals`, counter-example asserted first |
| 5 | Delight preserves mean luminance | not yet — the stage is not written. The resample half is asserted (channel means within 0.001) |
| 6 | Deterministic output is byte-identical | partly: `png` asserts a byte-identical re-write, `image io` asserts a stable content hash |
| 7 | Seam idempotence | not yet — needs `seamless` |

Three rules for anything added here, all learned while writing #1:

- **Assert the counter-example.** `assert seam_ratio(out) < 1.05` is the sketch this section used to
  carry, and it is a bad assertion: it passes when the operation is a no-op, and it fails on a
  source whose own boundary joint is sharper than its interior ones — which is a property of the
  content, not of the resample. The enforced version asserts that a periodic source keeps the *same*
  ratio (1.143 → 1.136 across 1.5x–8x) **and** that a clamped resample of the same source diverges
  (6.1x at 8x). The second sentence is what gives the first one teeth.
- **Thresholds come from a measurement, not a round number.** Every figure in the table above is
  printed by the test that asserts it, so the next person sees the margin instead of trusting it.
- **A threshold is not a promise about all inputs.** `< 1.05` holds for a resample of a source whose
  features are resolved at the target size. It is not true of a source that already scores 1.43,
  and no resample rule can make it so.

The API these are written against is the real one — `Image(width, height, channels, space, data)`,
with `resample()`, `to_linear()`, `to_srgb()`, `renormalise()` and `unit_error()` as methods:

```python
def test_data_maps_never_get_gamma():              # the silent poison
    with pytest.raises(ColourSpaceError):
        Image(4, 4, 1, "data", [0.5] * 16).to_srgb()

def test_normals_stay_unit_length_after_resize():
    assert normal.resample(512, 512, wrap=True).unit_error() > normal.unit_error()
    assert normal.resample(512, 512, wrap=True, renormalise=True).unit_error() < 0.02
```

### 4.3 `tests/benchmark/` — **your own textures**

The most important test file in the project. 30–50 real textures of your own, across the classes
in [PIPELINE.md](PIPELINE.md) §2, each with:

- the class and expected behaviour (lattice confidence, expected seam method)
- a human rating from the [QC_METRICS.md](QC_METRICS.md) §13 checklist
- an acceptable seam score

```powershell
pytest -m benchmark -q          # prints a scorecard table, not pass/fail
```

```
class      n   lattice%   seam_mean   structure   density   human_ok
brick      8      100%        97.2       0.981      2.1      8/8
tile       7      100%        96.8       0.990      1.8      7/7
wood       6       83%        93.1       0.975      1.6      5/6
concrete   9        0%        91.4       0.972      1.2      8/9
marble     4        0%        --         --         --       4/4 (correctly refused)
...

CHANGED since last run: concrete seam_mean 89.1 -> 91.4 (+2.3)
```

**Run this before and after every algorithmic change and keep both in the commit message.** It is
the only thing that tells you whether a change made the tool better or just different.

Grow it: every time a material fails in real use, add it to the benchmark. The set becomes the
tool's institutional memory, and after a year it is the most valuable file in the repo.

### 4.4 `tests/failure/` — hostile and broken input

```python
@pytest.mark.parametrize("bad", [
    "truncated.jpg", "empty.png", "huge.exr", "srgb_declared_data.tif",
    "badcrc.png", "almost_tileable.png", "readonly_cache_dir",
])
def test_clean_failure(bad):
    with pytest.raises(PBRForgeError) as e:
        run_pipeline(bad)
    assert e.value.stage and e.value.reason and e.value.fix
```

The assertion is not "it raises" — it is **"it raises something with a stage, a reason, and a
fix"**. A crash with a traceback is a failed test.

---

## 5. Performance

Every stage records `elapsed_s`. `--profile` prints the breakdown and a per-stage budget check.

```powershell
pbrforge make brick.jpg --profile
```

```
stage            time    budget   Δ
ingest           0.42s   1.50s   -1.08
preprocess       1.81s   2.00s   -0.19
lattice          0.61s   1.20s   -0.59
seamless         0.24s   1.50s   -1.26
delight          0.93s   0.80s   +0.13  ⚠ over budget
derive           2.14s   2.00s   +0.14  ⚠
pack             0.58s   0.60s   -0.02
total            6.73s  11.00s
```

Add a `@pytest.mark.perf` test when a stage gains a budget. Fail the perf test at > 20% over
budget, because performance decays silently otherwise.

Profile before optimising. The usual suspects, in order:

1. A full-resolution array where a 512 px subsample would answer the question.
2. float64 sneaking in from a numpy default.
3. A GPU↔CPU round trip inside a loop.
4. A Python loop over tiles that could be one vectorised call.
5. Recomputing something the cache already has.

---

## 6. How to add things

### A new stage

1. `stages/<name>.py` implementing the `Stage` protocol.
2. Parameters in `core/params.py` with defaults, ranges and validation.
3. Register in the DAG (`stages/__init__.py`) and declare upstream dependencies.
4. Metrics into the QC report; thresholds into `qc_thresholds.toml`.
5. Unit + golden test; a property test if an invariant exists; a failure test.
6. A section in [PIPELINE.md](PIPELINE.md) in the same commit. **Docs and code ship together**,
   or the docs become fiction within a month.

### A new renderer preset

Follow the checklist in [PRESETS.md](PRESETS.md) §9. A preset is not done until the normal
orientation is confirmed by looking at a lit sphere.

### A new AI model

1. Manifest row in `ai/registry.py`: key, task, source, sha256, licence, **provenance**,
   vram_mb, deterministic.
2. An `AITask` implementation with `available`, `licence`, `provenance`, `deterministic`, `run`.
3. If it is not deterministic, it must not be in the default export path.
4. A determinism test (same seed twice = identical bytes).
5. Docs section in [AI_FEATURES.md](AI_FEATURES.md).

### A new CLI verb

One `add_parser(...)` in `cli.py` plus one entry in the `handlers` dict and one in `COMMANDS`.
The CLI is **argparse, not click** — deliberately, because click is a dependency and the runtime has
none (see `ARCHITECTURE.md` §2.1). It never contains pipeline logic; it is a thin shell over the same
functions the UI calls. If a verb needs logic the UI does not, the logic belongs in `core/` or
`stages/`.

Two argparse traps already cost time here, so they are worth knowing:

- `--json` lives in `add_common()`. Adding it again to a subparser raises `ArgumentError` at import
  time, which takes down *every* command rather than just the new one.
- The handler signature is `cmd_<name>(args) -> int`. Returning a non-zero int is how a command
  reports failure to a script; raising `RuntimeError` is how it reports "the user can fix this", and
  `main()` prints it with the `error` prefix instead of a traceback.

### A command that manages a process

Anything that stops or signals another process goes through `lifecycle.py`. It is the only place that
knows a packaged build is two processes, and it will not terminate a PID it cannot positively
identify. Adding a second implementation of "find the server" is how the uninstall bug happened.

### A feature that needs an optional dependency

The pattern is established by `tls.py` and is the one to copy. A feature that *can* use a third-party
package must also work without it, because the frozen build cannot import what was never bundled and
a second PC will not have run `pip`.

1. **Detect, do not import at module scope.** `_crypto_available()` returns
   `(bool, reason)`; `openssl_path()` returns a path or `None`. Neither raises. `doctor` calls the
   same functions, so what it reports and what the code will do cannot drift apart.
2. **Have a second backend, or a documented degradation.** For TLS it is the `openssl` CLI, found on
   `PATH` *and* in the well-known install locations. Git for Windows ships one, which is why this is
   a real fallback rather than a technicality.
3. **Make the backends produce identical output.** Both write the same four files in the same layout,
   so nothing downstream branches on which one ran. A backend that needs its own code path further
   up is a design smell.
4. **Test every available backend, not just the preferred one.** `selftest.test_tls` loops over both
   and completes a real handshake against each. In a source checkout `cryptography` wins the
   dispatch, so without the loop the openssl path — the only one a packaged build can use — would
   never be executed by any test.
5. **Say which one is in play.** `pbrforge tls status` prints the backend by name. When something
   works on one machine and not another, this is the first question.

An honest skip beats a false failure: with neither backend available, `test_tls` returns *pass* with
the reason, because failing would make a correct minimal build look broken.

---

## 7. Windows-specific gotchas

Real, learned, worth the paragraph:

| Issue | Fix |
|---|---|
| OneDrive file locks | Never work inside a synced folder. See INSTALLATION.md step 0. |
| Long paths | `LongPathsEnabled` registry key + `git config core.longpaths true` |
| Antivirus scanning the cache | Exclude the cache directory. It cuts batch time noticeably. |
| `cv2` and `pyvips` DLL conflicts | Both ship their own libjpeg/libpng builds. Import order can matter. Keep imports centralised in `core/image.py`. |
| Path separators in the sidecar | Always write forward slashes into JSON, even on Windows. The renderer may be on another machine. |
| File locking in the cache | **`O_CREAT\|O_EXCL` lock files, in `core/cache.py`.** Not `msvcrt.locking` and not `fcntl` — neither is reliable over SMB, and the multi-PC case puts the cache on a share. Stale locks are reclaimed after 600 s. |
| Console encoding | `rich` is not a dependency; print plainly and set `encoding="utf-8"` on every explicit file write. |
| Unicode filenames | Test with a Devanagari filename; the library will contain them. |
| Microsoft Store Python and `%APPDATA%` | Writes are redirected into the package's private store, reads fall through to the real folder. Two copies of `tls\ca.pem` can then exist. See `TROUBLESHOOTING.md` C12. Prefer the python.org installer. |
| A file the running server has open | `pbrforge.exe` cannot be rebuilt while it is serving — the lock is real. `pbrforge stop` first; the build script does not do it for you. |

---

## 8. Git conventions

```
<type>(<scope>): <imperative summary>

feat(lattice): sub-period rejection for running bond
fix(image): wrap padding in resample (seam regression)
perf(ao): vectorise horizon marching, 4.2x
docs(presets): corona screen-size displacement note
test(property): assert derived map alignment is exactly 0
chore(deps): bump pyvips
```

Body, required for algorithmic changes:

```
Benchmark before:  seam_mean 91.4  structure 0.972  brick_lattice 100%
Benchmark after:   seam_mean 93.8  structure 0.981  brick_lattice 100%
Worse:             concrete repetition 0.29 -> 0.34 (needs coarser default macro_scale)
Docs updated:      PIPELINE.md §1.4
```

That "Worse:" line is the part people skip and the part that matters. Every algorithmic change
makes something worse. If you cannot name what, you have not measured properly.

**Version bumps:** `0.x` while the pipeline is changing shape. `pipeline_hash` changes whenever
code or parameters change, so materials stay traceable across any version bump.

---

## 9. Definition of done

A change is done when all of these are true:

- [ ] It works on the benchmark set and the scorecard has not regressed
- [ ] `pytest` passes, including the property and failure suites
- [ ] mypy and ruff are clean
- [ ] A failure path exists and produces a stage + reason + fix
- [ ] Docs updated in the same commit (PIPELINE/PRESETS/AI_FEATURES as appropriate)
- [ ] Colour-space and map-alignment invariants still hold
- [ ] If it is slower, the benchmark justified it
- [ ] `material.json` still round-trips: a material made before the change still opens and
      explains itself after

---

## 10. The two rules, restated

Because they account for most real bugs in tools like this:

> **1. Data maps never receive gamma.**
> **2. Derived maps are aligned by construction, and any alignment test that is not exactly zero
>    is a bug.**

Everything else is a normal engineering problem.
