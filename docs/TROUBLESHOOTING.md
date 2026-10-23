# Troubleshooting

Symptom → cause → fix. Organised by where you notice the problem, because that is how you will
arrive here.

---

## A. The render looks wrong

### A1. The surface looks wet, chrome-like or plastic

**Likely cause:** roughness is too low, or the roughness map is inverted, or a normals-versus-
smoothness convention mismatch.

1. Check the roughness histogram. If `r_mean < 0.15` on a material that should be matte, the
   estimator clamped down, probably because it misread specular highlights as gloss.
2. Raise `roughness.base` and raise the `clamp` lower bound to 0.15 minimum. Real materials
   almost never sit at 0.05 except polished metal and wet floors.
3. If it looks wet *everywhere* while the map varies, the map is inverted (glossiness where
   roughness was expected). Enscape and V-Ray both want roughness; legacy glossiness is `1 − r`.
4. In Unity, check that the Mask Map's **alpha** channel carries smoothness, not roughness. An
   alpha channel that was flattened during a pipeline step reads as smoothness = 0 or 1, which
   looks like uniform wetness or uniform chalk.

### A2. Bumps look like dents (or dents look like bumps)

**Cause:** the normal map's green channel orientation is wrong. This is the single most common
integration error in this entire field.

| Engine convention | |
|---|---|
| Blender, Unity, glTF, USD | **+Y** (OpenGL, green up) |
| Unreal, DirectX-style | **−Y** (green down) |
| V-Ray / Corona / Arnold | Follows the host DCC — verify per host |

**Test:** render a lit sphere with a single obvious bump. Light from the upper-left. The bump
should catch light on its **upper-left**.

**Fix:** press `G` in the viewport to flip, then re-export with the correct orientation recorded
in the preset. In Blender, also check the Normal Map node's strength is 1.0 and the image's
colour space is `Non-Color`.

### A3. Textures look washed out / too bright in the renderer

**Cause:** a data map is being read as sRGB, or the albedo is being gamma'd twice.

1. **V-Ray:** the Bitmap node's gamma must be **Override 1.0** for every data map. If the node is
   set to "Automatic" and the file is tagged sRGB, V-Ray applies a gamma curve to your roughness.
2. **Blender:** every data map must be `Non-Color`. Anything else gets the sRGB transfer curve.
   This is a silent failure — it looks *plausible*, which is why it survives review.
3. **Unreal:** sRGB must be OFF on the ORM texture and any data map. It is ON by default.
4. Check the export's `material.json` — the `space` field per map is the promise the packer made.

### A4. It looks too dark

**Cause:** de-lighting lowered the albedo mean, or the albedo is genuinely dark for the material.

1. Check `albedo_mean` in the QC report. Concrete should be 0.35–0.45, plaster 0.5–0.75, asphalt
   0.12–0.20. If it is far below the reference for the category, the mean was not restored.
2. Raise `delight.strength` downward (to 0.5) and re-run — less flattening, less mean loss.
3. Check for pure-black pixels. Pure black albedo causes fireflies in V-Ray and Corona. The
   pipeline floors at 0.04; if you are seeing 0.0, a post-process clipped it.
4. Remember that cathedrals and night scenes are supposed to be dark. Compare against the
   reference table, not against your memory of the photo.

### A5. It looks flat — no relief

1. **Is displacement actually on?** In V-Ray, `VRayDisplacementMod` must be added *and* the
   amount must be in millimetres. In Corona the control is **screen size in pixels**, not mm —
   a very common miss. In Blender, displacement needs subdivision; on an unsubdivided plane it
   does nothing.
2. **Is the amplitude plausible?** 3 mm on a brick wall is about right; 0.1 mm is invisible and
   50 mm is a caricature.
3. **Is the geometry dense enough?** 2D displacement needs vertices. A wall with four faces will
   not show relief no matter what the map says. Use bump for that surface, or subdivide.
4. **Is `height.detail_boost` too low?** A flat-lit source produces a flat height field. Raise it
   to 0.4.

### A6. Visible grid / obvious repetition across the surface

**Cause:** the tile is fine, the *period* is visible. This is a perceptual problem, not a seam
problem, and it is the one clients actually notice.

1. Look at `repetition_at_8x` in the QC report. Above 0.45 means it is measurable.
2. Raise `variation.macro_strength` to 0.25–0.35. That single change fixes most cases.
3. Generate 4–6 variants and alternate them across the UV grid (`pbrforge variants`).
4. For large floors, use a Wang tile set — a genuinely repetition-free surface with no shader work.
5. If the surface is far away, the honest answer is a lower-resolution texture, not a better one.
   Repetition is a function of viewing distance and tile count.

### A7. Soft/blurry when the camera is close

**Cause:** texel density.

1. Read the density in the status bar. Below 1 px/mm, a close-up will be soft. It is arithmetic,
   not a bug.
2. Re-export at a higher resolution, or re-shoot the photo closer.
3. Then wrap the material in real-world units: if the brick module is 190 mm, tile the UVW at an
   integer multiple of 190 mm, not at "4 repeats". Most "blurry texture" complaints are actually
   a wrong UV scale, which no amount of pixels fixes.

### A8. Shimmering / sparkle at grazing angles, or aliasing while moving

1. 8-bit normals band and sparkle. Export 16-bit for offline renderers. Opus of this: V-Ray and
   Blender take 16-bit normals happily; realtime engines generally want 8-bit and BC5.
2. Set `normal.clamp_z` to 0.1–0.2 to tame extreme grazing angles in realtime engines.
3. Make sure mipmaps are generated with a proper filter and that the normal map's compression is
   BC5 (two channels, Z reconstructed) — BC1 on a normal map is a common and very visible mistake.
4. Anisotropic filtering: 8× or 16× for floors seen at grazing angles.

### A9. Dark fringe / outline around foliage

**Cause:** a naive matte. The semi-transparent edge pixels still contain background colour, and in
a premultiplied-alpha pipeline they multiply it into the result.

1. Turn on `alphamatte.decontaminate` — this solves for the foreground colour in the edge pixels
   instead of leaving the background blend in place.
2. Set `erode_px` to 1 (2 for high-contrast backgrounds).
3. Check the renderer's alpha convention: **straight** (Unity, most glTF) vs **premultiplied**
   (some V-Ray/Unreal setups). Getting this wrong is the whole error.
4. Never let the matte rewrite the RGB inside the opaque region (`preserve_rgb: true`).

### A10. Banding in gradients, especially in roughness

1. Export 16-bit for that map. Iterate: 8-bit rounding is visible on a large smooth surface.
2. Turn on dithering for the final quantisation.
3. Check that the banding is not inherited from the source: a heavily compressed JPEG has DCT
   banding that de-jpeg can only partially remove. Re-shoot or accept it.

---

## B. The tool misbehaves

### B1. It made it seamless but the result looks obviously synthetic

You are probably in the wrong method for the material.

- **Blocks of structure that do not align** → quilting, or reduce the patch size.
- **Smeared / mushy** → the method is low-passing too much. Reduce `detail_boost`, or switch off
  spectral and use healing with detail re-injection.
- **Wrong brick bond / impossible grout** → an AI method hallucinated. Turn AI off; lattice
  detection is exact for this case and should have won. Check `lattice.sensitivity`.
- **Mirrored lines** → mirror mode is on. Mirror is only for noise. Turn it off.

### B2. It claims a lattice period where there is none

Check the evidence in the report. Common false positives:

- an accidental colour rhythm (e.g. a light roughly every 800 px)
- a vignette creating a radial pattern
- the windowing function itself

Fixes: raise `lattice.sensitivity`, set `axis_lock`, or just drag the overlay grid to the correct
position — the manual override exists precisely because detection is a heuristic.

### B3. It refuses to tile a material I think should tile

Check `seam.method` and the confidence. If the material genuinely has no repeat unit (marble slab,
mural, weathered plaster, a natural stone wall), refusal is correct behaviour. The honest options
are:

- crop to the least-bad window and accept a seam
- use a 2×2 variation atlas with randomised UV offsets
- don't tile it — it is one panel, so put it on one panel

See [FEATURES.md](FEATURES.md) §2.7. Being told not to do something is a feature.

### B4. Every run is slow the first time and instant afterwards

That is the cache working. If you want to force a full recompute:
`pbrforge make … --no-cache`. Prune with `pbrforge cache --prune 30d`.

### B5. Two materials produced different results from the same input and settings

Should be impossible. Check:

1. `pipeline_hash` in both `material.json` files. Different hashes mean different code or
   parameters — the real cause.
2. Same hash but different bytes means a genuine determinism bug: an unseeded RNG, a
   thread-order dependency in a reduction, or a GPU kernel with non-deterministic atomics.
   Report it with both hashes; it is a real bug worth fixing.
3. `deterministic: false` in the report means an unseeded AI step ran. Pin the seed.

### B6. Warnings appear that I do not understand

`pbrforge explain <metric>` prints the formula, this material's value, the thresholds, and what to
do. Every warning in the report maps to a metric in [QC_METRICS.md](QC_METRICS.md) §-numbered
section by design.

---

## C. Installation and environment

### C1. `Could not find library 'libvips-42.dll'`

1. The PATH entry did not take effect — close and reopen the terminal (PATH is read at process
   start).
2. You downloaded the `web` or `static` libvips build. You need **`vips-dev-w64-all`**.
3. Verify: `vips --version` in a fresh terminal, then the Python binding check in
   [INSTALLATION.md](INSTALLATION.md) step 3.
4. Last resort: `$env:PBRFORGE_IMAGE_BACKEND = "pillow"` — works, but ~4× the memory and 8k
   inputs may fail.

### C2. `torch.cuda.is_available()` is False

Your driver is older than the CUDA runtime the wheel was built for. **Update the driver.** Do not
downgrade PyTorch — you will spend an evening on it and end up with a slower stack.

### C3. CUDA out of memory, or the GPU process hangs

1. `nvidia-smi` — if a previous run died, it may still hold VRAM. Kill it or reboot.
2. Lower `vram_budget_mb` in the config to leave headroom for the desktop compositor; on Windows
   the desktop itself uses ~500 MB–1 GB.
3. Run the stage with `--cpu` to confirm it is a VRAM issue and not a logic error.
4. The browser preview holds VRAM too. Closing the preview tab is a legitimate fix.

### C4. Very slow, GPU not being used

1. `pbrforge doctor` — check the GPU probe line.
2. Scheduled tasks do not always get a CUDA context without an interactive session. Run the task
   as the logged-in user, or accept CPU for scheduled runs.
3. Laptop: check you are not on integrated graphics or a power-saving profile.

### C5. `PermissionError` on a file in the cache or output directory

OneDrive, almost certainly. Move the project and the library outside any synced folder
([INSTALLATION.md](INSTALLATION.md) step 0). Antivirus real-time scanning is the second cause —
exclude the cache directory.

### C6. Long path errors

Enable `LongPathsEnabled` (registry) and `git config core.longpaths true`. Deep cache paths are
unavoidable; the alternative is shorter hash prefixes, which risks collisions.

### C7. The UI does not load, or the viewport is blank

1. Check the browser console for a WebGL error. The 2.0.0 viewport uses WebGL; if a context
   cannot be created, hardware acceleration may be disabled in the browser.
2. If the shader failed to compile, the console names the GLSL error. The viewer falls back to the
   albedo map rather than showing a blank canvas.
3. `127.0.0.1:7788` blocked by a firewall rule or another process. Change the port in the config.

### C8. Batch job stops overnight

1. Check the progress file — `--resume` continues from there.
2. Laptop on battery: set the scheduled task to "Start only if on AC power".
3. Check the 72-hour task limit ("Stop the task if it runs longer than…") — uncheck it.
4. Check disk space. A 300-file 4k batch legitimately needs tens of GB in the cache.

### C9. The server will not stop

**Symptom:** you end `pbrforge.exe` in Task Manager and the port is still bound, the UI still
answers, or the file cannot be deleted.

You ended the wrong process. The packaged build is a PyInstaller `--onefile` executable, which is a
bootloader that unpacks itself and runs the application in a **child**. Both are named
`pbrforge.exe`; Task Manager shows the bootloader first. The child holds the port and the file lock.

```powershell
pbrforge stop            # reads server.pid and stops the process that owns the socket
pbrforge url             # prints "Live now: ..." or "Not running: ..." with the reason
```

If `stop` refuses, read what it says — the refusals are deliberate and each names its cause:

| Message | Meaning |
|---|---|
| `refusing to terminate it` / `is notepad.exe` | the `server.pid` on disk names a PID that is now some *other* program. Nothing was killed. Delete `%APPDATA%\pbrforge\server.pid`, or just start the server again to overwrite it |
| `the recorded port N has no listener, so this record is stale` | the server already exited, or the PID was recycled after a crash. The record is cleaned up automatically |
| `exists but could not be parsed` | the file was hand-edited or half-written. Delete it |
| `that is this process` | you ran `stop` from inside the server process, which is not a thing that should happen |

Only the "refusing" case needs you. The rest are the tool telling you the truth about a file it did
not trust.

### C10. Uninstall left `pbrforge.exe` behind

This was a bug, fixed in 0.1.0 — the uninstaller now stops the server before it deletes anything,
because a running server keeps the executable locked. If you are on an older build: `pbrforge stop`,
then uninstall again.

A related old-build symptom is an uninstall that hangs with no visible window. That was plain
`MsgBox` in the Inno script, which ignores `/VERYSILENT`. It is a `SuppressibleMsgBox` now, and its
suppressed default is **keep your textures**.

### C11. The installer will not run, or reports an error with no window

Run it from a console so you can see the log:

```powershell
.\PBRForge-Setup-2.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=%TEMP%\pbrforge-install.log
```

Then read `%TEMP%\pbrforge-install.log`. The two things that actually go wrong are a leftover
`pbrforge.exe` from an old install (see C10) and an antivirus quarantine — and the log names both,
while the dialog does not.

### C12. The app prints a path that does not exist

The symptom is precise and confusing: `pbrforge config` says the config directory is
`C:\Users\you\AppData\Roaming\pbrforge`, and there is nothing there. `Test-Path` on that exact path
returns `False` from PowerShell while the app happily writes to it. Files appear to vanish.

This is **Microsoft Store Python**, which is an MSIX package, and MSIX gives it a private,
copy-on-write view of `AppData\Roaming` at:

```
C:\Users\you\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Roaming\
```

The exact rule, and both halves matter:

| Operation | Where it goes |
|---|---|
| **write** to `%APPDATA%\pbrforge\...` | redirected into the private store, and that copy then shadows the real one |
| **read** of `%APPDATA%\pbrforge\...` | private store first, then **falls through to the real location** |

So a source run under Store Python can *read* a file the installed `.exe` wrote and then *write* its
own version somewhere else — after which it silently reads its own, and the two copies drift. The
observed shape of this, from a machine that had done both:

```
Real     %APPDATA%\pbrforge\                    written by the installed .exe
    config.toml   secret.key
    tls\ca.pem  tls\ca.key  tls\server.pem  tls\server.key
                        <- reads fall through to here when no shadow exists

Private  ...\LocalCache\Roaming\pbrforge\       written by a source checkout
    tls\ca.pem  tls\ca.key  tls\server.pem  tls\server.key
    tls\client-trust\...
                        <- a different CA and a different leaf, same names
```

Note what is *missing* from the private store: `config.toml` and `secret.key`. That is why the source
run appeared to read the real config — nothing was shadowing it yet. Generating a certificate from
the source checkout is what creates the split, and from then on the two builds disagree about which
certificate is theirs.

Why this is worth knowing rather than ignoring:

- Anything you hand-edit in `%APPDATA%\pbrforge` may or may not be seen, depending on whether the
  interpreter looking at it has already written a shadow.
- Two matching-looking `tls\ca.pem` files can exist. A client that trusts one will reject the server
  holding the other, and the error is `self-signed certificate in certificate chain` — a valid
  certificate being reported as invalid. Check the hidden copy before debugging the certificate.
- `doctor` flags it as `· Python build  Microsoft Store build detected`.

The fix is to stop using Store Python — step 0 of `INSTALLATION.md`. The workaround, if you want to
keep it, is to point both builds at one directory outside `AppData`, so there is nothing to shadow:

```powershell
$env:PBRFORGE_CONFIG_DIR = 'C:\dev\pbrforge-config'
```

Set it permanently for the user, and the installed `.exe` respects it too — environment variables
override the built-in paths in both builds.

The installed `.exe` is not an MSIX package and none of this applies to it. If you only ever run the
packaged build, this entry is not for you.

### C13. My `config.toml` edits were ignored

If the file is not valid TOML, the loader falls back to *every* default rather than failing to
start — deliberately, because a config typo should not stop the server. The cost is that a
hand-edited file can be silently ignored.

The most common cause was a **UTF-8 BOM**: `tomllib` rejects one, and PowerShell's
`Set-Content -Encoding utf8` always writes one (as did older Notepad). Reading is now
`utf-8-sig`, so a BOM is fine.

If edits are still being ignored, check for it rather than guessing:

```powershell
python -m pbrforge doctor | findstr /C:"Config file"
```

- `✓ Config file  ... (from C:\...\config.toml)` — it was read.
- `· Config file  ... (from defaults)` — the file does not exist, so nothing is being ignored.
- `✗ Config file  ... exists but could not be parsed` — a **FAIL**, and the lines below it name the
  parse error. This is what a typo looks like; it is deliberately not a warning that scrolls past,
  because running on defaults you did not choose is a much worse outcome than a loud complaint.

You can always see the effective values, and where they came from:

```powershell
python -m pbrforge config
```

Note that `PBRFORGE_*` environment variables override the file, so a stray `PBRFORGE_PORT` in a
shell you forgot about will beat the config and the file will look broken.

---

## D. Editing

### D1. Undo did not restore a stage

Undo covers parameter and brush edits. Pipeline stage *runs* are versioned, not undone — use the
timeline's **Restore** on the version you want, then **Fork** if you want to branch rather than
overwrite. The distinction is deliberate: a stage result is a computed artifact, and silently
recomputing history would make `pipeline_hash` meaningless.

### D2. My seam brush edits were lost

The brush writes to a mask artifact, so it invalidates the `seamless` stage and everything
downstream. If you then change a `delight` parameter, the mask survives (it is an input to
`seamless`, not a product of it). If you edit with the **AI** method selected, the mask is used
as the generation region — that is usually what you want, but it means a large mask can trigger
a slow AI pass.

### D3. Changing one slider recomputed everything

That is the DAG working correctly: a `delight` parameter invalidates `derive_*`, `pack` and `qc`.
Changing only `roughness.remap` invalidates `derive_rough` onward, which is fast. If a change feels
disproportionately slow, open the stage panel and look at which chips went stale — that is exactly
what is being recomputed.

---

## E. When to stop debugging and change approach

Three cases where the right answer is not a fix:

1. **The material has no repeat unit.** Marble, murals, facades. Do not tile it. Use a panel.
2. **The photo is not good enough.** Blurry, low-resolution, shot in bad light, motion-blurred.
   No amount of processing recovers information that was never captured. Re-shoot.
3. **The material needs to be authored, not captured.** Painted effects, worn edges that follow
   geometry, procedural grime. Use a procedural tool for that layer and this tool for the base.
   They are complementary, and a base texture plus a procedural overlay is the correct answer more
   often than either alone.
