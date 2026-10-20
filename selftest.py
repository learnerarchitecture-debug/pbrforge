"""`pbrforge selftest` -- prove the installed thing actually works, on this machine.

Why this exists as a first-class command rather than a pytest suite:

The interesting failures for this project are environmental, not logical. A second
PC where the server binds but the firewall silently drops packets. A OneDrive
folder that turns every atomic rename into a sync conflict. A cache on an SMB
share where the lock is not actually exclusive. None of those are caught by unit
tests, and all of them are caught in ten seconds by a test that starts the real
server on the real network stack and talks to it over a real socket.

So every test here exercises the real code path end to end:
  - the real config loader reading a real TOML file
  - the real Auth, with a real secret on disk
  - the real PNG codec writing and reading real bytes
  - the real cache, including the cross-process lock
  - the real HTTP server, over a real loopback socket, with real cookies
  - the real discovery beacon, over real UDP
  - the real seam metric, on a texture that is tileable and one that is not

A test that cannot fail is not a test: the seam metric check asserts a *wide
separation* between the two cases rather than that both produce plausible numbers.
"""

from __future__ import annotations

import http.cookiejar
import json
import math
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import APP_NAME, DISCOVERY_PORT, __version__
from .config import Config
from .core import png, synth
from .core.cache import Cache, CacheError, human_bytes


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    seconds: float = 0.0


@dataclass
class Workspace:
    root: Path
    cfg: Config
    extras: dict = field(default_factory=dict)


def _free_port() -> int:
    """Ask the OS for a port nobody is using, then let go of it.

    A fixed port would make the self-test fail whenever the real server happens to
    be running, which is exactly when you are most likely to be debugging.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------- tests
def test_resources(ws: Workspace) -> Result:
    from .resources import index_file, ui_dir

    ui = ui_dir()
    if ui is None:
        return Result("resources", False, "the ui/ directory was not found",
                      "run from a source checkout, or reinstall the packaged build")
    index = index_file()
    if index is None:
        return Result("resources", False, f"{ui} contains no index.html or prototype.html",
                      "the packaged build is missing its UI data files")
    size = index.stat().st_size
    return Result("resources", True, f"ui={ui}  index={index.name} ({size // 1024} KB)")


def test_config(ws: Workspace) -> Result:
    """Write a config, read it back, confirm every field survives the round trip.

    The bug this catches: a TOML writer that emits a value the reader cannot parse,
    which shows up as a silently ignored setting rather than an error.
    """
    path = ws.root / "config.toml"
    path.write_text(Config().write_default(path, force=True).read_text(encoding="utf-8"),
                    encoding="utf-8")
    loaded = Config.load(config_dir=ws.root)
    if loaded.config_file != path:
        return Result("config", False, f"expected {path}, got {loaded.config_file}")

    mismatches: list[str] = []
    for name, expected in (("port", 7788), ("https_port", 7443), ("auth", True),
                           ("tls", False), ("discovery", True), ("resolution", 4096),
                           ("max_upload_mb", 512), ("preset", "generic-pbr")):
        actual = getattr(loaded, name)
        if actual != expected:
            mismatches.append(f"{name}={actual!r} (want {expected!r})")
    if mismatches:
        return Result("config", False, "round-trip lost: " + ", ".join(mismatches),
                      "the TOML writer and reader disagree")

    # An environment override must beat the file, because scheduled tasks rely on it.
    import os
    os.environ["PBRFORGE_PORT"] = "7789"
    try:
        overridden = Config.load(config_dir=ws.root)
    finally:
        del os.environ["PBRFORGE_PORT"]
    if overridden.port != 7789:
        return Result("config", False, "PBRFORGE_PORT did not override the file",
                      "check the env_map in config.py")
    return Result("config", True, "write/read/override all correct")


def test_auth(ws: Workspace) -> Result:
    from .server.auth import Auth

    secret = ws.root / "secret.key"
    a = Auth(secret, enabled=True)
    if not secret.exists():
        return Result("auth", False, "the secret file was not created", "check permissions on the config dir")

    # Stable across instances: otherwise every restart would un-pair every client.
    b = Auth(secret, enabled=True)
    if a.pairing_code() != b.pairing_code():
        return Result("auth", False, "the pairing code changed across instances",
                      "pairing_code() must derive from the secret, never from random state")
    if not a.check_pairing_code(a.pairing_code().replace("-", "").lower()):
        return Result("auth", False, "the pairing code was rejected by its own validator")

    # The alphabet excludes I/L/O/U/0/1 precisely so a human can retype it.
    code = a.pairing_code()
    if len(code) != 9 or code[4] != "-":
        return Result("auth", False, f"unexpected code shape {code!r}")

    if a.check_pairing_code("ZZZZ-ZZZZ") and a.pairing_code() != "ZZZZ-ZZZZ":
        return Result("auth", False, "a wrong code was accepted",
                      "this is the one auth bug that matters")

    token = a.issue_session()
    if not a.verify_session(token):
        return Result("auth", False, "a freshly issued session failed verification")
    if a.verify_session(token + "x"):
        return Result("auth", False, "a tampered session token was accepted")
    if a.verify_session("1.deadbeef"):
        return Result("auth", False, "an expired session token was accepted")

    # Rotation must revoke without leaving stale state behind.
    a.rotate()
    if a.verify_session(token):
        return Result("auth", False, "rotating the secret did not revoke existing sessions",
                      "sessions must be signed with the live secret")
    return Result("auth", True, f"code={a.pairing_code()}  sessions and rotation correct")


def test_png(ws: Workspace) -> Result:
    """Round-trip plus determinism. Determinism is not cosmetic: the cache is
    content-addressed, so non-deterministic bytes mean a permanent cache miss."""
    size = 32
    rgb = bytearray()
    for y in range(size):
        for x in range(size):
            rgb += bytes(((x * 8) % 256, (y * 8) % 256, ((x ^ y) * 4) % 256))
    original = png.Image(size, size, 3, 8, bytes(rgb))

    a = ws.root / "a.png"
    b = ws.root / "b.png"
    png.write(a, original)
    png.write(b, original)
    if a.read_bytes() != b.read_bytes():
        return Result("png", False, "writing the same image twice produced different bytes",
                      "the writer must not use timestamps or randomised filters")

    back = png.read(a)
    if (back.width, back.height, back.channels, back.bit_depth) != (size, size, 3, 8):
        return Result("png", False, f"header changed: {back.width}x{back.height}x{back.channels}")
    if back.data != original.data:
        diff = sum(1 for i in range(min(len(back.data), len(original.data)))
                   if back.data[i] != original.data[i])
        return Result("png", False, f"{diff} of {len(original.data)} bytes differ after a round trip")

    # A 16-bit write/read must preserve the full range, not silently clip to 8.
    import struct
    vals = [(i * 257) % 65536 for i in range(size * size)]
    sixteen = png.Image(size, size, 1, 16, b"".join(struct.pack(">H", v) for v in vals))
    png.write(ws.root / "c.png", sixteen)
    if png.read(ws.root / "c.png").data != sixteen.data:
        return Result("png", False, "16-bit data was corrupted by a round trip")

    return Result("png", True, f"{a.stat().st_size} bytes, byte-identical on re-write, 8+16 bit ok")


def test_png_errors(ws: Workspace) -> Result:
    """The reader must name what it cannot do instead of guessing."""
    junk = ws.root / "junk.png"
    junk.write_bytes(b"not a png at all, but long enough to look like one")
    try:
        png.read(junk)
    except png.PngError as exc:
        if "signature" not in str(exc):
            return Result("png errors", False, f"unhelpful message for bad input: {exc}")
    else:
        return Result("png errors", False, "garbage input was accepted as a PNG")

    # A truncated file must fail loudly -- silently returning a partial image is
    # how a half-written export reaches a renderer.
    good = ws.root / "good.png"
    png.write(good, png.Image(16, 16, 3, 8, bytes(16 * 16 * 3)))
    truncated = ws.root / "trunc.png"
    truncated.write_bytes(good.read_bytes()[:60])
    try:
        png.read(truncated)
    except png.PngError:
        pass
    else:
        return Result("png errors", False, "a truncated PNG did not raise")
    return Result("png errors", True, "rejects garbage and truncation with a named reason")


def test_cache(ws: Workspace) -> Result:
    cache = Cache(ws.root / "cache")
    key = Cache.key("stage", "normal", {"strength": 1.0, "flip_y": False})
    if cache.has(key, "normal.png"):
        return Result("cache", False, "a cold cache already had the artifact")

    payload = b"\x89PNG-ish bytes" * 100
    cache.put_bytes(key, "normal.png", payload)
    got = cache.get(key, "normal.png")
    if got is None or got.read_bytes() != payload:
        return Result("cache", False, "stored bytes did not come back identical")

    # Keys must be stable and must differ when any parameter differs.
    if Cache.key("a", {"x": 1, "y": 2}) != Cache.key("a", {"y": 2, "x": 1}):
        return Result("cache", False, "key() depends on dict insertion order",
                      "parameters must be sorted before hashing")
    if Cache.key("a", {"x": 1}) == Cache.key("a", {"x": 2}):
        return Result("cache", False, "different parameters produced the same key")

    # The lock must actually exclude. A second Cache object stands in for a second
    # process, which is the real scenario (two PCs, or two CLI runs).
    other = Cache(ws.root / "cache")
    with cache.lock(key, timeout=5):
        try:
            with other.lock(key, timeout=0.4):
                return Result("cache", False, "a second holder acquired the same lock",
                              "exclusion failed; concurrent runs would corrupt artifacts")
        except CacheError:
            pass
    with other.lock(key, timeout=2):
        pass          # released correctly
    return Result("cache", True, "content-addressed, order-independent keys, lock is exclusive")


def test_tileability(ws: Workspace) -> Result:
    """The single most important test in the suite.

    The seam metric is the number the whole UI is built around. If it does not
    clearly separate a tileable texture from a non-tileable one, every score in
    the app is decoration. And if it produces a *false positive* on a perfect
    texture -- which the first version of it did -- the flagship feature is worse
    than useless, because it sends the user hunting for a seam that is not there.
    """
    size = 128
    material = synth.brick_material(size=size, seed=7)
    clean = [int(round(h * 255)) for h in material.height]
    ratio_clean = synth.seam_ratio_grey(clean, size)
    score_clean = synth.ratio_to_score(ratio_clean)

    # A lighting gradient is exactly what an un-healed photograph has: it is
    # continuous everywhere except across the wrap, where it becomes a step.
    ramp = [int(round(max(0.0, min(1.0,
                                   h * 0.4 + 0.6 * ((i % size) / size)))) * 255)
            for i, h in enumerate(material.height)]
    ratio_raw = synth.seam_ratio_grey(ramp, size)
    score_raw = synth.ratio_to_score(ratio_raw)

    if score_clean < 90:
        return Result("tileability", False,
                      f"a known-tileable field scored only {score_clean} "
                      f"(e={ratio_clean:.2f}, so the wrap is {ratio_clean:.1f}x the "
                      f"texture's own strongest join)",
                      "a false positive here means the UI reports seams on "
                      "textures that have none")
    if score_raw > 60:
        return Result("tileability", False,
                      f"a badly non-tileable field still scored {score_raw} "
                      f"(e={ratio_raw:.2f})",
                      "the metric does not discriminate; every score in the UI is "
                      "meaningless")
    if score_clean - score_raw < 35:
        return Result("tileability", False,
                      f"separation is only {score_clean - score_raw} points "
                      f"({score_raw} vs {score_clean})")

    # The metric must be blind to texture content, not just to brick.
    noise = [int(round(synth._hash2(i % size, i // size, 5) * 255))
             for i in range(size * size)]
    score_noise = synth.ratio_to_score(synth.seam_ratio_grey(noise, size))
    if score_noise < 90:
        return Result("tileability", False,
                      f"white noise scored {score_noise}; random noise tiles "
                      f"perfectly, so anything below 90 is a bug")

    flat = [128] * (size * size)
    score_flat = synth.ratio_to_score(synth.seam_ratio_grey(flat, size))

    return Result("tileability", True,
                  f"tileable {score_clean}/100 (e={ratio_clean:.2f})  "
                  f"vs broken {score_raw}/100 (e={ratio_raw:.2f})  "
                  f"separation {score_clean - score_raw}  "
                  f"noise {score_noise}  flat {score_flat}")


def test_derived_maps(ws: Workspace) -> Result:
    """Normal, AO and roughness must be derived from the height field and must
    themselves tile. A normal map with a discontinuity at the wrap is the classic
    invisible bug: it looks fine until the texture is tiled."""
    size = 64
    material = synth.brick_material(size=size, seed=3)

    if material.normal.width != size or material.normal.channels != 3:
        return Result("derived maps", False, "the normal map has the wrong shape")

    # Every texel of a tangent-space normal map must be a unit vector, encoded
    # around 0.5. Values drifting far from that mean the encode is wrong.
    worst = 0.0
    for i in range(0, size * size * 3, 3):
        nx = material.normal.data[i] / 255 * 2 - 1
        ny = material.normal.data[i + 1] / 255 * 2 - 1
        nz = material.normal.data[i + 2] / 255 * 2 - 1
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        worst = max(worst, abs(length - 1.0))
    if worst > 0.02:
        return Result("derived maps", False,
                      f"normal vectors are not unit length (worst error {worst:.3f})",
                      "the encode or the normalisation step is wrong")

    # Flat height must give a flat normal: this is what catches a flipped green
    # channel or a scale applied in the wrong direction. Height 0.5 everywhere
    # means zero slope, so the encoded normal must be exactly (128, 128, 255).
    normal_flat = synth._normal_from_height([0.5] * (size * size), size)
    if set(normal_flat.data) != {128, 255}:
        unexpected = sorted(set(normal_flat.data) - {128, 255})[:6]
        return Result("derived maps", False,
                      f"a flat height field produced normals containing {unexpected}",
                      "expected exactly (128, 128, 255) everywhere")

    ao_mean = sum(material.ao.data) / len(material.ao.data)
    if not 80 < ao_mean < 250:
        return Result("derived maps", False, f"AO mean is implausible ({ao_mean:.0f}/255)")
    if material.ao.data == bytes([material.ao.data[0]] * len(material.ao.data)):
        return Result("derived maps", False, "AO is a constant -- the height field was ignored")

    rough_mean = sum(material.roughness.data) / len(material.roughness.data)
    if not 40 < rough_mean < 240:
        return Result("derived maps", False, f"roughness mean is implausible ({rough_mean:.0f}/255)")

    return Result("derived maps", True,
                  f"normals unit-length (err {worst:.4f}), AO mean {ao_mean:.0f}, "
                  f"roughness mean {rough_mean:.0f}")


def test_colour_space(ws: Workspace) -> Result:
    """A data map must never receive gamma, and the refusal must be in the code.

    docs/ARCHITECTURE.md 6 calls this the largest single source of "correct on
    screen, wrong in the render" bugs. It is silent when broken -- a roughness map
    with a transfer function over it still looks like a roughness map -- so the test
    has to prove the guard *fires*, and then prove the guard protects something real.
    A guard on an identity function would refuse nothing meaningful.
    """
    from .core.colour import MAP_SPECS, Space, assert_writable, linear_to_srgb, srgb_to_linear
    from .core.errors import ColourSpaceError
    from .core.image import Image

    data_kinds = sorted(k for k, spec in MAP_SPECS.items() if spec.space.is_data)
    colour_kinds = sorted(k for k, spec in MAP_SPECS.items() if spec.space.is_colour)
    if not data_kinds or not colour_kinds:
        return Result("colour space", False, "the map registry is incomplete")

    refusals = 0
    for kind in data_kinds:
        img = Image.filled(4, 4, 1, Space.DATA, 0.5)
        untouched = list(img.data)
        for method in ("to_linear", "to_srgb"):
            try:
                getattr(img, method)()
                return Result("colour space", False,
                              f"{method}() succeeded on the {kind} map",
                              "a data map that accepts a transfer function is the "
                              "silent poison this guard exists for")
            except ColourSpaceError:
                refusals += 1
        if img.data != untouched:
            return Result("colour space", False,
                          f"a refused conversion still modified the {kind} map",
                          "refusing must not be a warning with a side effect")
        try:
            assert_writable(kind, Space.SRGB)
            return Result("colour space", False,
                          f"the export gate allowed {kind} to be written as sRGB")
        except ColourSpaceError:
            refusals += 1
        try:
            assert_writable(kind, MAP_SPECS[kind].space)
        except ColourSpaceError as exc:
            return Result("colour space", False,
                          f"the export gate refused {kind} in its own space: {exc}")

    # The other direction: colour written as data loses the transfer function the
    # renderer applies on read, so it is the same class of mistake.
    for kind in colour_kinds:
        try:
            assert_writable(kind, Space.DATA)
            return Result("colour space", False,
                          f"the export gate allowed {kind} to be written as data")
        except ColourSpaceError:
            refusals += 1

    albedo = Image.filled(2, 2, 3, Space.SRGB, 0.5)
    linear = albedo.to_linear()
    if linear.space is not Space.LINEAR:
        return Result("colour space", False, f"to_linear() tagged the result {linear.space}")
    # IEC 61966-2-1: 0.5 -> 0.2140411. The usual approximation, 0.5 ** 2.2 =
    # 0.217638, is 1.7% out, and the error is largest in the darkest stops -- which
    # is where a roughness map's subtle variation lives.
    if abs(linear.data[0] - 0.2140411) > 1e-6:
        return Result("colour space", False,
                      f"sRGB 0.5 became {linear.data[0]:.7f}, expected 0.2140411",
                      "a plain 2.2 power curve is not the sRGB transfer function")
    if albedo.data[0] != 0.5:
        return Result("colour space", False, "to_linear() modified its source image")

    worst_rt = 0.0
    for i in range(1001):
        v = i / 1000.0
        worst_rt = max(worst_rt, abs(linear_to_srgb(srgb_to_linear(v)) - v))
    if worst_rt > 1e-12:
        return Result("colour space", False,
                      f"the transfer function does not round trip ({worst_rt:.2e})")
    back = linear.to_srgb()
    if abs(back.data[0] - 0.5) > 1e-9:
        return Result("colour space", False,
                      f"linear -> sRGB round trip gave {back.data[0]:.9f}")

    return Result("colour space", True,
                  f"{len(data_kinds)} data maps refused in both directions and at the "
                  f"export gate ({refusals} refusals); sRGB 0.5 -> {linear.data[0]:.6f}, "
                  f"round trip exact to {worst_rt:.1e}")


def test_resample_tiling(ws: Workspace) -> Result:
    """THE classic regression: resampling a tileable image must not create a seam.

    docs/PIPELINE.md 1.4 makes wrap-around kernel padding a hard rule, because this
    failure is delayed and mis-attributed: the preview looks seamless and the exported
    2k version has a line down it, months later, after the seam stage reported
    success.

    Three claims, because "the ratio looked small" is not evidence:

      1. An exactly periodic source keeps the *same* ratio at 1.5x to 8x. That is
         what "the output is still periodic" means. The expected ratio is not 1.0 --
         the wrap column sits where the signal is steepest and the reference is the
         95th percentile of the interior joins, so 1.14 is the correct reading for
         this input. What matters is that resampling does not move it.
      2. The same source resampled with clamping diverges -- measured at 6x by 8x
         upscale. Without this, claim 1 would not show that the wrap path is doing
         anything clamp does not.
      3. A clean fixture stays clean at 2x and 4x, which is the assertion the pipeline
         doc actually promises (< 1.05) and which the fixture could not have passed
         before its mortar was anti-aliased.
    """
    from .core.image import Image

    def ratio(img: Image) -> float:
        return synth.seam_ratio_grey(img.data, img.width)

    size = 32
    values = []
    for y in range(size):
        for x in range(size):
            v = 0.5
            for k in (1, 2):
                v += (0.30 / k) * math.sin(2 * math.pi * k * x / size)
                v += (0.20 / k) * math.sin(2 * math.pi * k * y / size)
            values.append(0.0 if v < 0.0 else (1.0 if v > 1.0 else v))
    periodic = Image(size, size, 1, "data", values)
    base = ratio(periodic)

    drift = 0.0
    wrapped_ratios: dict[int, float] = {}
    clamped_ratios: dict[int, float] = {}
    for target in (48, 96, 256):
        wrapped = periodic.resample(target, target, wrap=True)
        if (wrapped.width, wrapped.height) != (target, target):
            return Result("resample tiling", False,
                          f"a {target}x{target} request produced "
                          f"{wrapped.width}x{wrapped.height}",
                          "a transposed axis would misalign every derived map")
        wrapped_ratios[target] = ratio(wrapped)
        drift = max(drift, abs(wrapped_ratios[target] - base))
        clamped_ratios[target] = ratio(periodic.resample(target, target, wrap=False))

    if drift > 0.02:
        return Result("resample tiling", False,
                      f"a periodic source changed ratio by {drift:.3f} "
                      f"({base:.3f} -> {base + drift:.3f}) after resampling",
                      "the wrap resample is not reproducing the source's periodicity")

    teeth = clamped_ratios[256] / wrapped_ratios[256]
    if teeth < 4.0:
        return Result("resample tiling", False,
                      f"clamping is only {teeth:.1f}x worse at 8x upscale "
                      f"({clamped_ratios[256]:.2f} vs {wrapped_ratios[256]:.2f})",
                      "if clamping were harmless there would be nothing to test")

    same = periodic.resample(size, size)
    identity = max(abs(a - b) for a, b in zip(same.data, periodic.data))
    if identity > 1e-9:
        return Result("resample tiling", False,
                      f"resampling to the same size moved pixels by {identity:.2e}",
                      "a half-pixel offset here misaligns every derived map")

    # Weight normalisation. A constant image has an exact known answer at every size,
    # so a DC gain error -- the same error that would break the delight stage's
    # mean-luminance guarantee -- shows up immediately.
    worst_flat = 0.0
    for target in (17, 33, 100):
        got = Image.filled(64, 64, 3, "srgb", 0.37).resample(target, target)
        worst_flat = max(worst_flat, abs(got.data[0] - 0.37), abs(got.mean(1) - 0.37))
    if worst_flat > 1e-9:
        return Result("resample tiling", False,
                      f"a constant image came back changed by {worst_flat:.2e}",
                      "the kernel weights are not normalised: downscaling would shift "
                      "the mean brightness")

    fixture96 = synth.brick_material(size=96, seed=7)
    fixture64 = synth.brick_material(size=64, seed=7)
    worst_clean = 0.0
    for material, target in ((fixture96, 192), (fixture64, 256)):
        src = Image.from_png(material.albedo, "srgb").luminance()
        if ratio(src) >= 1.05:
            return Result("resample tiling", False,
                          f"the fixture itself does not tile at {material.size} "
                          f"(e={ratio(src):.3f})",
                          "the fixture is wrong, not the resample")
        worst_clean = max(worst_clean, ratio(src.resample(target, target, wrap=True)))
    if worst_clean >= 1.05:
        return Result("resample tiling", False,
                      f"a clean fixture scored e={worst_clean:.3f} after upscaling",
                      "docs/PIPELINE.md 1.4 promises < 1.05")

    albedo = Image.from_png(fixture64.albedo, "srgb")
    resampled = albedo.resample(96, 96, wrap=True)
    worst_mean = max(abs(albedo.mean(c) - resampled.mean(c)) for c in range(3))
    if worst_mean > 0.001:
        return Result("resample tiling", False,
                      f"a resample shifted a channel mean by {worst_mean:.5f}",
                      "delight asserts a mean luminance, so a resample that moved the "
                      "mean would break it for an unrelated reason")

    # The whole map set must stay in lockstep. Derived maps are aligned by
    # construction, and resampling some maps but not others is exactly how that
    # guarantee is lost.
    set_out = [Image.from_png(img, space).resample(96, 96, wrap=True)
               for img, space in ((fixture64.albedo, "srgb"), (fixture64.normal, "data"),
                                  (fixture64.ao, "data"), (fixture64.roughness, "data"))]
    sizes = {(img.width, img.height) for img in set_out}
    if len(sizes) != 1:
        return Result("resample tiling", False,
                      f"the map set resampled to different sizes: {sorted(sizes)}",
                      "derived maps are aligned by construction and must stay that way")

    return Result("resample tiling", True,
                  f"periodic {base:.3f} stable to {drift:.3f} over 1.5x-8x; clamping "
                  f"diverges {teeth:.1f}x; identity {identity:.0e}; constant exact to "
                  f"{worst_flat:.0e}; clean fixture {worst_clean:.3f} at 2x-4x; "
                  f"means within {worst_mean:.5f}")


def test_resample_normals(ws: Workspace) -> Result:
    """A resample shortens normal vectors, and renormalising must be the fix.

    The other half of PIPELINE.md 1.7, kept as its own test because the two failures
    have opposite symptoms: a missing wrap shows up as a line, a missing renormalise
    shows up as a surface that has gone soft, and neither appears in the other's
    check.

    The first assertion is deliberately the counter-example -- that resampling
    *without* renormalising does break unit length -- because otherwise the second
    assertion would pass whether or not the rule mattered.
    """
    from .core.errors import ColourSpaceError
    from .core.image import Image

    material = synth.brick_material(size=48, seed=3)
    normal = Image.from_png(material.normal, "data")
    before = normal.unit_error()
    if before > 0.01:
        return Result("resample normals", False,
                      f"the fixture's normal map is not unit length to begin with "
                      f"({before:.4f})",
                      "8-bit encoding alone costs about 0.005, so this is not a bug")

    plain = normal.resample(31, 37, wrap=True)
    if (plain.width, plain.height) != (31, 37):
        return Result("resample normals", False,
                      f"a 31x37 request produced {plain.width}x{plain.height}",
                      "a transposed axis would misalign every derived map")
    shortened = plain.unit_error()
    if shortened <= before:
        return Result("resample normals", False,
                      f"resampling did not shorten the vectors "
                      f"({before:.4f} -> {shortened:.4f}); this test is not exercising "
                      f"the rule it exists for")

    fixed = normal.resample(31, 37, wrap=True, renormalise=True)
    after = fixed.unit_error()
    if after > 0.02:
        return Result("resample normals", False,
                      f"renormalised vectors are still off unit length ({after:.4f})")

    for bad, why in ((Image.filled(4, 4, 3, "srgb"), "a colour image"),
                     (Image.filled(4, 4, 1, "data"), "a 1-channel data map"),
                     (Image.filled(4, 4, 2, "data"), "a 2-channel data map")):
        try:
            bad.renormalise()
            return Result("resample normals", False,
                          f"renormalise() accepted {why}",
                          "a packed AO/roughness/metallic map also has 3 channels, and "
                          "normalising it destroys it while still looking plausible")
        except ColourSpaceError:
            pass

    return Result("resample normals", True,
                  f"unit error {before:.4f} -> {shortened:.4f} raw -> {after:.4f} "
                  f"renormalised; refuses colour and non-3-channel input")


def test_image_io(ws: Workspace) -> Result:
    """The float <-> byte boundary: quantise, clamp, and hash.

    Three things this pins down, all of which are the kind of bug that survives every
    visual check:

      - Quantisation must round trip exactly. `65535 = 255 * 257`, so an 8-bit value
        scaled to 16 bits is exact -- and a scale factor of 65536 would not be, which
        is how this catches that class of slip.
      - Overshoot must clamp, not wrap. Lanczos is sharpening kernel and does produce
        values outside 0..1 at a hard edge; `int(1.02 * 255)` is 260, which wraps to 4
        and puts a black speckle beside every bright edge.
      - The hash is the cache's identity function. It must be stable across runs and
        sensitive to a single 16-bit step, because a hash that missed a change serves
        a stale map as if it were current.
    """
    from .core.image import Image

    material = synth.brick_material(size=32, seed=3)
    src = Image.from_png(material.albedo, "srgb")

    path = ws.root / "image-io-roundtrip.png"
    png.write(path, src.to_png(8))
    back = Image.from_png(png.read(path), "srgb")
    worst8 = max(abs(a - b) for a, b in zip(src.data, back.data))
    if worst8 != 0.0:
        return Result("image io", False,
                      f"an 8-bit write/read changed pixels by {worst8:.2e}",
                      "values that came from 8 bits must survive quantisation exactly")

    # Two probes, pinning different things.
    #
    # 0.1 is *exactly* halfway between two 16-bit codes (6553.5), so it measures the
    # worst case rounding error rather than a convenient one. 1/3 is exactly
    # 21845/65535 -- because 3 divides 65535 -- so it must survive bit-exactly, and
    # 0x5555 pins both the scale factor (65535, not 65536) and the big-endian order.
    probe = Image(4, 1, 1, "data", [0.0, 0.1, 1.0 / 3.0, 1.0])
    wide = probe.to_png(16)
    if len(wide.data) != 8:
        return Result("image io", False,
                      f"a 4-pixel 16-bit image produced {len(wide.data)} bytes")
    got = Image.from_png(wide, "data")
    worst16 = max(abs(a - b) for a, b in zip(probe.data, got.data))
    # Half a step is the most rounding can be wrong by. The 1e-9 of slack is float
    # noise, because 0.1 * 65535 is 6553.500000000001 in double precision.
    if worst16 > 0.5 / 65535.0 + 1e-9:
        return Result("image io", False,
                      f"16-bit quantisation error is {worst16:.2e}, over half a step",
                      "half a step (1/131070) is the most rounding can be wrong by")
    if wide.data[2:4] != b"\x19\x9a":
        return Result("image io", False,
                      f"0.1 encoded as {wide.data[2:4].hex()}, expected 199a",
                      "0.1 is 6553.5 in 16-bit units, so this also pins round-half-up")
    if wide.data[4:6] != b"\x55\x55":
        return Result("image io", False,
                      f"1/3 encoded as {wide.data[4:6].hex()}, expected 5555",
                      "65535 = 3 * 21845, so 1/3 is exact in 16 bits")
    if got.data[2] != probe.data[2]:
        return Result("image io", False,
                      f"1/3 did not round trip exactly (read back {got.data[2]!r})",
                      "65535 / 3 is an integer, so this is exact arithmetic")

    clash = Image(3, 1, 1, "data", [-0.2, 0.5, 1.5])
    if list(clash.to_png(8).data) != [0, 128, 255]:
        return Result("image io", False,
                      f"overshoot clamped to {list(clash.to_png(8).data)}, "
                      f"expected [0, 128, 255]",
                      "an unclamped 1.5 encodes to 383, which wraps to 127")
    if list(clash.to_png(16).data) != [0, 0, 128, 0, 255, 255]:
        return Result("image io", False,
                      f"16-bit overshoot clamped to {list(clash.to_png(16).data)}")

    digest = src.content_hash()
    if digest != src.content_hash():
        return Result("image io", False, "the content hash is not stable within a run")
    if len(digest) != 64:
        return Result("image io", False, f"the content hash is {len(digest)} chars")
    bumped = src.copy()
    bumped.data[7] += 1.0 / 65535.0
    if bumped.content_hash() == digest:
        return Result("image io", False,
                      "a single 16-bit step did not change the content hash",
                      "the cache would serve the old map under the new key")
    if Image.from_png(material.albedo, "linear").content_hash() == digest:
        return Result("image io", False,
                      "the same pixels hashed the same at two different spaces",
                      "the space is part of the identity: writing the two differs")
    if src.content_hash(bits=8) == digest:
        return Result("image io", False, "the 8-bit and 16-bit hashes are equal")

    return Result("image io", True,
                  f"8-bit round trip exact, 16-bit within {worst16:.2e} "
                  f"(half step {0.5 / 65535.0:.1e}), overshoot clamps, hash stable and "
                  f"sensitive to one step")


def test_server(ws: Workspace) -> Result:
    """The LAN flow, over a real socket, with real cookies. This is the test that
    proves another PC can use this one."""
    from .server.auth import Auth, COOKIE_NAME
    from .server.simple import App, serve

    cfg = ws.cfg
    cfg.auth = True
    cfg.discovery = False
    cfg.tls = False

    auth = Auth(cfg.secret_file, enabled=True)
    app = App(config=cfg, auth=auth)
    port = _free_port()
    running = serve(app, host="127.0.0.1", port=port, span=1, banner=False)
    base = f"http://127.0.0.1:{running.bound_port}"
    try:
        # 1. /api/ping is reachable without pairing. Discovery depends on it, and
        #    it must not leak anything beyond the app identity.
        status, _, body = _request(base + "/api/ping")
        if status != 200:
            return Result("server", False, f"/api/ping returned {status}")
        ping = json.loads(body)
        if ping.get("app") != "pbrforge":
            return Result("server", False, f"/api/ping returned {body[:120]!r}")
        leaked = {"secret", "code", "pairing_code", "token"} & set(ping)
        if leaked:
            return Result("server", False, f"/api/ping leaked {sorted(leaked)}",
                          "discovery must not reveal anything usable for auth")

        # 2. An unpaired client is redirected to /pair, not served the app.
        status, headers, _ = _request(base + "/", follow=False)
        if status not in (301, 302, 303, 307, 308) or "/pair" not in headers.get("Location", ""):
            return Result("server", False, f"unpaired GET / returned {status}, "
                                           f"Location={headers.get('Location')!r}",
                          "the whole library would be readable by anyone on the network")

        # 3. An API call without a session is a 401 JSON, not an HTML redirect:
        #    the UI needs to detect this and show the pairing screen.
        status, _, body = _request(base + "/api/health", follow=False)
        if status != 401:
            return Result("server", False, f"unpaired /api/health returned {status}, not 401")
        if json.loads(body).get("pair_url") != "/pair":
            return Result("server", False, "the 401 body does not point at /pair")

        # 4. The pair form renders.
        status, _, body = _request(base + "/pair")
        if status != 200 or b"Pairing code" not in body:
            return Result("server", False, f"/pair returned {status} with {len(body)} bytes")

        # 5. A wrong code is rejected...
        jar = http.cookiejar.CookieJar()
        status, _, _ = _request(base + "/pair", method="POST", jar=jar,
                                follow=False, data=b"code=AAAA-AAAA")
        if status != 403:
            return Result("server", False, f"a wrong pairing code returned {status}, not 403")

        # 6. ...and the right one sets a session that actually works.
        code = auth.pairing_code().replace("-", "")
        status, headers, _ = _request(base + "/pair", method="POST", jar=jar,
                                      follow=False, data=f"code={code}".encode())
        if status != 302 or f"{COOKIE_NAME}=" not in headers.get("Set-Cookie", ""):
            return Result("server", False,
                          f"pairing returned {status}, Set-Cookie={headers.get('Set-Cookie')!r}")
        if not any(c.name == COOKIE_NAME for c in jar):
            return Result("server", False, "the session cookie was not retained by the client")

        status, _, body = _request(base + "/api/health", jar=jar)
        if status != 200:
            return Result("server", False, f"paired /api/health returned {status}")
        health = json.loads(body)
        if health.get("version") != __version__:
            return Result("server", False, f"health reports version {health.get('version')!r}")

        # 7. The app shell is served, and it is the real UI, not a placeholder.
        status, headers, body = _request(base + "/", jar=jar)
        if status != 200 or len(body) < 5000:
            return Result("server", False, f"paired GET / returned {status}, {len(body)} bytes")
        if b"PBRForge" not in body and b"pbrforge" not in body:
            return Result("server", False, "the served page does not mention PBRForge")
        if "Content-Length" not in headers:
            return Result("server", False, "the response had no Content-Length",
                          "HTTP/1.1 keep-alive will hang the browser without it")

        # 8. Path traversal is refused, and refused explicitly.
        for attack in ("/ui/../config.py", "/ui/..%2fconfig.py", "/ui/../../secret.key"):
            status, _, _ = _request(base + attack, jar=jar, follow=False)
            if status == 200:
                return Result("server", False, f"{attack} was served successfully",
                              "path traversal is possible; this is a security bug")
            if status == 500:
                return Result("server", False, f"{attack} produced a 500 rather than a refusal")

        # 9. A missing route is a 404 with an explanation.
        status, _, body = _request(base + "/nope", jar=jar, follow=False)
        if status != 404:
            return Result("server", False, f"an unknown route returned {status}")

        return Result("server", True,
                      f"ping, 401, pair, session, shell ({len(body)} byte 404 page) all correct")
    finally:
        running.stop()
        time.sleep(0.15)


def test_redirect(ws: Workspace) -> Result:
    """With TLS on, plain HTTP must send the client to HTTPS. Without this, a user
    who types the IP without a scheme lands on an insecure origin and silently
    loses WebGPU -- and will report it as a GPU bug."""
    cfg = ws.cfg
    from .server.auth import Auth
    from .server.simple import App, RedirectHandler, Server

    cfg.auth = True
    cfg.discovery = False
    app = App(config=cfg, auth=Auth(cfg.secret_file, enabled=True))
    port = _free_port()
    httpd = Server(("127.0.0.1", port), RedirectHandler, app)
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, headers, _ = _request(f"http://127.0.0.1:{port}/ui/prototype.html", follow=False)
        location = headers.get("Location", "")
        if status != 301:
            return Result("redirect", False, f"plain HTTP returned {status}, not 301")
        if not location.startswith("https://"):
            return Result("redirect", False, f"redirected to {location!r} instead of https")
        if not location.endswith("/ui/prototype.html"):
            return Result("redirect", False, f"the redirect dropped the path: {location!r}")
        return Result("redirect", True, f"301 -> {location}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tls(ws: Workspace) -> Result:
    """Generate a certificate and prove it works with a real TLS handshake.

    This is the test whose absence let two bugs ship. `tls status` raised
    AttributeError and `tls trust` raised NameError, because neither command had
    ever actually been executed -- both passed review in a design described as
    finished. The handshake below is the part that matters: it checks the chain,
    the hostname and the SANs the way a browser will, so a certificate that is
    merely *present* does not count as a pass.

    Both backends are tested when both are available. In a source checkout
    `cryptography` wins the dispatch, and the openssl path -- the only path a
    packaged build can use, since a frozen exe cannot import a package that was
    never bundled -- would otherwise go untested here.
    """
    from . import tls as tlsmod

    exe = tlsmod.openssl_path()
    backends: list[tuple[str, Callable[[Config, list[str], list[str]], list[Path]]]] = []
    if tlsmod._crypto_available()[0]:
        backends.append(("cryptography",
                         lambda c, d, i: tlsmod._init_cryptography(c, d, i)))
    if exe:
        backends.append(("openssl", lambda c, d, i: tlsmod._init_openssl(c, d, i, exe)))
    if not backends:
        # An honest skip. The frozen build is deliberately stdlib-only and a clean
        # machine may have no openssl either; failing here would make a correct
        # build look broken, which is worse than not testing.
        return Result("tls", True,
                      f"skipped: no backend ({tlsmod._crypto_available()[1]}, "
                      f"and no openssl on PATH or in the usual install locations)")

    dns, ips = tlsmod.sans_for_server()
    saved_config_dir = ws.cfg.config_dir
    notes: list[str] = []
    try:
        for label, generate in backends:
            cfg = ws.cfg
            # tls_dir is a read-only property (config_dir / "tls"), so the config
            # directory is what has to move to give each backend its own
            # certificate. Restored in the finally block below.
            cfg.config_dir = saved_config_dir / f"tls-check-{label}"
            cfg.config_dir.mkdir(parents=True, exist_ok=True)

            generate(cfg, dns, ips)
            for path in (cfg.ca_file, cfg.cert_file, cfg.key_file):
                if not path.exists():
                    return Result("tls", False, f"{label}: nothing written to {path.name}")

            # server.pem must be leaf + CA in that order. A context given only the
            # leaf still works on this machine and fails on a client that wants the
            # chain, so the count is worth asserting.
            pem = cfg.cert_file.read_text(encoding="utf-8")
            count = pem.count("-----BEGIN CERTIFICATE-----")
            if count != 2:
                return Result("tls", False,
                              f"{label}: server.pem holds {count} certificates, "
                              f"expected 2 (leaf + CA)")

            # ---- the trust folder ----
            tlsmod.write_trust_script(cfg)
            out = cfg.tls_dir / "client-trust"
            for name in ("ca.pem", "trust-ca.ps1", "READ-ME.txt"):
                if not (out / name).exists():
                    return Result("tls", False, f"{label}: the trust folder has no {name}")
            if (out / "ca.pem").read_bytes() != cfg.ca_file.read_bytes():
                return Result("tls", False,
                              f"{label}: the distributed CA is not the CA that signed the leaf")

            script = (out / "trust-ca.ps1").read_bytes()
            if not script.startswith(b"\xef\xbb\xbf"):
                # PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI and mangles
                # the comment lines, which is the sort of thing that looks like a
                # corrupted download to the person running it.
                return Result("tls", False, f"{label}: trust-ca.ps1 has no UTF-8 BOM")

            readme = (out / "READ-ME.txt").read_text(encoding="utf-8")
            # The trust folder is generated while tls is still OFF in the config, so
            # it must name the https port from the config rather than inferring the
            # scheme from the running state -- which would have sent every client to
            # the plain address the whole exercise exists to get them off.
            if f":{cfg.https_port}" not in readme:
                return Result("tls", False,
                              f"{label}: the READ-ME never mentions the https port "
                              f"{cfg.https_port}")
            if not readme.startswith("PBRForge client trust\n"):
                return Result("tls", False,
                              f"{label}: the READ-ME begins with {readme[:40]!r}")
            if "http://http" in readme:
                return Result("tls", False, f"{label}: the READ-ME has a doubled scheme")
            leftover = re.search(r"\{[a-z_]+\}", readme + script.decode("utf-8", "replace"))
            if leftover:
                # An f-string placeholder that never got filled in. This is exactly
                # how {ipport} reached the file and raised NameError at runtime.
                return Result("tls", False,
                              f"{label}: {leftover.group(0)} was written out unformatted")

            # ---- a real handshake, chain and hostname included ----
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                server_ctx.load_cert_chain(certfile=str(cfg.cert_file),
                                           keyfile=str(cfg.key_file))
            except ssl.SSLError as exc:
                return Result("tls", False, f"{label}: the key pair will not load: {exc}")

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(20)
            port = listener.getsockname()[1]
            server_side: list[str] = []

            def serve_once(listener=listener, server_ctx=server_ctx,
                           server_side=server_side) -> None:
                try:
                    raw, _ = listener.accept()
                    with server_ctx.wrap_socket(raw, server_side=True) as conn:
                        conn.recv(16)
                        conn.sendall(b"ok")
                except Exception as exc:      # noqa: BLE001
                    server_side.append(f"{type(exc).__name__}: {exc}")

            import threading
            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()

            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(cafile=str(cfg.ca_file))
            client_ctx.check_hostname = True
            client_ctx.verify_mode = ssl.CERT_REQUIRED
            negotiated = ""
            answer = b""
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=20) as raw:
                    with client_ctx.wrap_socket(raw, server_hostname="localhost") as conn:
                        negotiated = conn.version()
                        conn.sendall(b"ping")
                        answer = conn.recv(16)
            except Exception as exc:      # noqa: BLE001
                return Result("tls", False,
                              f"{label}: a client trusting ca.pem rejected the server: {exc}")
            finally:
                try:
                    listener.close()
                except OSError:
                    pass
            thread.join(timeout=10)
            if server_side:
                return Result("tls", False,
                              f"{label}: the server half of the handshake failed: "
                              f"{server_side[0]}")
            if answer != b"ok":
                return Result("tls", False,
                              f"{label}: the handshake completed but no data came back")

            # ---- status() must agree with what was just generated ----
            st = tlsmod.status(cfg)
            if not st.present or st.days_left <= 0:
                return Result("tls", False,
                              f"{label}: status() says present={st.present} "
                              f"days_left={st.days_left}")
            if st.missing_current_ips:
                return Result("tls", False,
                              f"{label}: the certificate does not cover "
                              f"{', '.join(st.missing_current_ips)}, so LAN clients "
                              f"would get a name mismatch")
            notes.append(f"{label}: {negotiated}, {len(st.names)} SANs")
    finally:
        ws.cfg.config_dir = saved_config_dir

    return Result("tls", True, " · ".join(notes))


def test_discovery(ws: Workspace) -> Result:
    """UDP beacon, on a non-default port so a running server cannot collide."""
    from .server.discover import Beacon, probe

    port = _free_port()
    payload = {"app": "pbrforge", "version": __version__, "host": "selftest",
               "scheme": "http", "port": 1, "auth": True, "library": "x"}
    beacon = Beacon(lambda: payload, port=port)
    beacon.start()
    time.sleep(0.4)          # let the socket bind before we shout at it
    try:
        found = probe("127.0.0.1", port=port, timeout=1.5)
        if found is None:
            return Result("discovery", False, "the beacon did not answer a unicast probe",
                          "check that the UDP port is free and not blocked by the firewall")
        if found.url != "http://127.0.0.1:1":
            return Result("discovery", False, f"the announcement decoded to {found.url!r}")

        from .server.discover import _payload_to_announcement
        if _payload_to_announcement({"app": "somethingelse"}, "1.2.3.4") is not None:
            return Result("discovery", False, "a foreign app's reply was accepted")
    finally:
        beacon.stop()
    return Result("discovery", True, f"unicast probe answered on UDP {port}, "
                                     f"foreign replies ignored")


def test_lan_bind(ws: Workspace) -> Result:
    """The claim the whole feature rests on: the port is reachable on the LAN
    address, not only on loopback. Binding 127.0.0.1 by accident is the most
    common reason a second PC cannot connect."""
    from .netutil import interfaces, primary_ip
    from .server.auth import Auth
    from .server.simple import App, serve

    ifaces = interfaces()
    if not ifaces:
        return Result("lan bind", False, "this machine has no LAN address",
                      "connect to a network before installing for multi-PC use")

    cfg = ws.cfg
    cfg.auth = False          # we are testing reachability, not auth
    cfg.discovery = False
    app = App(config=cfg, auth=Auth(cfg.secret_file, enabled=False))
    port = _free_port()
    running = serve(app, host="0.0.0.0", port=port, span=1, banner=False)
    try:
        ip = primary_ip()
        status, _, body = _request(f"http://{ip}:{running.bound_port}/api/ping", timeout=4)
        if status != 200:
            return Result("lan bind", False, f"GET http://{ip}:{port}/api/ping returned {status}")
        if json.loads(body).get("app") != "pbrforge":
            return Result("lan bind", False, "ping over the LAN address returned the wrong payload")
        return Result("lan bind", True,
                      f"reachable at http://{ip}:{running.bound_port} "
                      f"(other PCs use this address)")
    except urllib.error.URLError as exc:
        return Result("lan bind", False, f"could not reach {ip}: {exc.reason}",
                      "this is usually the Windows firewall; run deploy\\allow-firewall.cmd "
                      "as Administrator")
    finally:
        running.stop()


# ------------------------------------------------------------------- plumbing
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):     # type: ignore[override]
        return None


def _request(url: str, *, method: str = "GET", data: bytes | None = None,
             jar: http.cookiejar.CookieJar | None = None, follow: bool = True,
             timeout: float = 6.0) -> tuple[int, dict, bytes]:
    """Returns (status, headers, body). Redirects are followed only if asked.

    The opener is assembled here from a cookie jar and a follow flag rather than
    being passed in. An earlier version accepted a pre-built opener, and the
    caller built one with a cookie jar but no redirect suppression -- so urllib
    followed the 302 and reported the final page as if it were the response.
    Two independent booleans that must agree is a bug waiting to happen; one
    function that can see both is not.
    """
    handlers: list = []
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    if not follow:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", f"PBRForge-selftest/{__version__}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_lifecycle(ws: Workspace) -> Result:
    """The stop path, and the guards that stop it killing the wrong process.

    This is a regression test for a bug found during the first install/uninstall
    cycle ever run: `pbrforge.exe` is a PyInstaller --onefile build, so the process
    that holds the port is a *child* of the one Task Manager shows. Nothing could
    stop a running server, and the uninstaller therefore could not delete the
    executable it had just installed.
    """
    import subprocess

    from . import lifecycle
    from .netutil import port_in_use, port_owner_pid

    cfg = ws.cfg
    pfile = lifecycle.pid_file(cfg)

    # --- the guards, first: none of these may terminate anything --------------
    if lifecycle.read(cfg) is not None:
        return Result("lifecycle", False, "a fresh workspace already had a server.pid")

    running, detail = lifecycle.status(cfg)
    if running:
        return Result("lifecycle", False, f"nothing is running but status said: {detail}")

    # A corrupt file must not be reported as "no server.pid file" -- those are
    # different problems, and naming the wrong one sends the user hunting for a
    # server that does not exist.
    pfile.write_text("not json {{{", encoding="utf-8")
    if not lifecycle.unreadable(cfg):
        return Result("lifecycle", False, "a corrupt PID file was not flagged as unreadable")
    _, detail = lifecycle.stop(cfg, adopt_by_port=False)
    if "could not be parsed" not in detail:
        return Result("lifecycle", False,
                      f"a corrupt PID file produced the message: {detail!r}",
                      "it should name the file, not claim nothing is running")

    # A live PID that we cannot confirm is our server must be left alone. Pointing
    # the record at *this* process makes the assertion absolute: if the guard ever
    # fails, the self-test terminates itself.
    #
    # Note the assertion is on the outcome, not the message. There are two
    # independent guards here -- identify() refuses because the recorded port has no
    # listener, and _terminate() refuses because the PID is the caller -- and which
    # one fires depends on whether the executable is `python.exe` or
    # `pbrforge.exe`. Pinning the wording made this test pass in a source checkout
    # and fail inside the packaged build, which is the exact opposite of useful.
    bogus_port = _free_port()
    pfile.write_text(json.dumps({"pid": os.getpid(), "port": bogus_port,
                                 "scheme": "http", "started": 0}), encoding="utf-8")
    stopped, detail = lifecycle.stop(cfg, adopt_by_port=False)
    if stopped:
        return Result("lifecycle", False, "stop() claimed to have stopped a foreign process")
    if not detail.strip():
        return Result("lifecycle", False, "stop() refused to act but gave no reason")
    if not pfile.exists():
        return Result("lifecycle", False,
                      "stop() removed the record of a process it refused to stop",
                      "the file is the only evidence the user has; leave it in place")

    # A record naming a dead PID is stale, not an error: clean it and move on.
    dead = subprocess.Popen([sys.executable, "-c", "pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dead.wait()
    pfile.write_text(json.dumps({"pid": dead.pid, "port": bogus_port,
                                 "scheme": "http", "started": 0}), encoding="utf-8")
    stopped, detail = lifecycle.stop(cfg)
    if stopped:
        return Result("lifecycle", False, "stop() reported stopping a process that was gone")
    if pfile.exists():
        return Result("lifecycle", False, "the stale PID file was not cleaned up")

    # --- now the real thing: start a server as a separate process, then stop it --
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "PBRFORGE_CONFIG_DIR": str(cfg.config_dir),
        "PBRFORGE_LIBRARY": str(ws.root / "library"),
        "PBRFORGE_DISCOVERY": "false",
        "PBRFORGE_NO_PAUSE": "1",
    })
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        # `python -m pbrforge` needs the *parent* of the package on sys.path. The
        # test deliberately spawns with cwd set to the scratch workspace to prove
        # the server does not depend on being launched from the repository, so the
        # path has to be supplied explicitly.
        pkg_parent = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = pkg_parent + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    argv = ([sys.executable, "serve"] if frozen else [sys.executable, "-m", "pbrforge", "serve"])
    argv += ["--port", str(port), "--no-discovery"]

    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=str(ws.root))
    try:
        deadline = time.time() + 30
        record = None
        while time.time() < deadline:
            record = lifecycle.read(cfg)
            if record is not None and record.port == port:
                break
            if proc.poll() is not None:
                out = (proc.stdout.read() or b"").decode("utf-8", "replace")[-600:]
                return Result("lifecycle", False,
                              f"the spawned server exited with code {proc.returncode}",
                              out.strip() or "no output")
            time.sleep(0.25)

        if record is None or record.port != port:
            return Result("lifecycle", False,
                          f"no server.pid appeared for the server on port {port}",
                          "serve() must record the port it actually bound")

        # The recorded PID must be the one holding the port -- not the bootloader
        # that launched it. This is the whole bug, stated as an assertion.
        owner = port_owner_pid(port)
        if owner != record.pid:
            return Result("lifecycle", False,
                          f"server.pid says pid {record.pid} but port {port} is held "
                          f"by pid {owner}",
                          "the record must name the process that owns the socket")

        running, detail = lifecycle.status(cfg)
        if not running:
            return Result("lifecycle", False, f"a live server was reported as: {detail}")

        stopped, detail = lifecycle.stop(cfg)
        if not stopped:
            return Result("lifecycle", False, f"stop() failed on a live server: {detail}")
        if port_in_use("127.0.0.1", port):
            return Result("lifecycle", False, f"port {port} is still bound after stop()")
        if pfile.exists():
            return Result("lifecycle", False, "the PID file survived a successful stop")
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:      # noqa: BLE001
            pass

    return Result("lifecycle", True,
                  "PID file names the socket owner; stop() releases the port and "
                  "refuses to touch an unconfirmed PID")


TESTS: list[tuple[str, Callable[[Workspace], Result], bool]] = [
    ("resources", test_resources, True),
    ("config", test_config, True),
    ("auth", test_auth, True),
    ("png", test_png, True),
    ("png errors", test_png_errors, True),
    ("cache", test_cache, True),
    # Exercises the process lifecycle against a real second process. It is the
    # slowest test in the suite by far (it spawns a server and waits for it), which
    # is why it is worth saying: the uninstall bug it guards was invisible to every
    # other test, because every other test runs in-process.
    ("lifecycle", test_lifecycle, True),
    # "derived maps" and "tileability" are the two slow numeric tests, at roughly
    # 0.5 s and 2.5 s. Both are marked core anyway: they are the only tests that
    # exercise the actual imaging maths, and `--quick` skipping them meant the
    # packaged-build verification -- the one run where a broken build must not
    # slip through -- never checked the seam metric at all. Saving 3 seconds
    # there is the worst trade in the file.
    ("derived maps", test_derived_maps, True),
    ("tileability", test_tileability, True),
    # The type-level invariants from docs/ARCHITECTURE.md 6, all core for the same
    # reason: the two rules that prevent most pipeline bugs are enforced in
    # `core/image.py` and `core/colour.py`, and a build in which they can fail
    # silently must not ship. Together about 3 s, most of it generating fixtures.
    ("colour space", test_colour_space, True),
    ("resample tiling", test_resample_tiling, True),
    ("resample normals", test_resample_normals, True),
    ("image io", test_image_io, True),
    ("server", test_server, True),
    ("redirect", test_redirect, True),
    # Generates a real certificate with every available backend and completes a
    # real TLS handshake, including hostname verification. Both `tls status` and
    # `tls trust` were dead on arrival for want of this test.
    ("tls", test_tls, True),
    ("discovery", test_discovery, True),
    ("lan bind", test_lan_bind, True),
]


def run(*, quick: bool = False, keep: bool = False, verbose: bool = False,
        config: Config | None = None) -> tuple[list[Result], Path]:
    """Run every test. Returns (results, workspace root)."""
    root = Path(tempfile.mkdtemp(prefix="pbrforge-selftest-"))
    cfg = Config.load(config_dir=root, overrides={
        "library": root / "library",
        "cache": root / "cache",
        "temp": root / "temp",
        "discovery": False,
        "tls": False,
        # A port nobody else can be on. The lifecycle test asserts that a fresh
        # workspace reports "nothing is running", and that check asks about the
        # *configured* port -- so on the default 7788 it failed whenever a real
        # server was up, which is exactly when a rebuild happens. The test was
        # measuring the machine's state instead of the code's behaviour.
        "port": _free_port(),
    })
    cfg.ensure_dirs()
    ws = Workspace(root=root, cfg=cfg)

    results: list[Result] = []
    for name, fn, is_core in TESTS:
        if quick and not is_core:
            continue
        started = time.perf_counter()
        try:
            result = fn(ws)
        except Exception as exc:                     # noqa: BLE001 - a crashing test is a failure, not a crash
            import traceback
            detail = f"{type(exc).__name__}: {exc}"
            if verbose:
                detail = traceback.format_exc()
            result = Result(name, False, detail,
                            "this is a bug in PBRForge, not in your setup -- "
                            "report it with the full traceback (pbrforge selftest -v)")
        result.seconds = round(time.perf_counter() - started, 2)
        results.append(result)

    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    return results, root


def render(results: list[Result], root: Path, kept: bool) -> str:
    lines = ["", f"  {APP_NAME} {__version__}  \u00b7  selftest", ""]
    width = max((len(r.name) for r in results), default=10)
    for r in results:
        mark = "\u2713" if r.ok else "\u2717"
        lines.append(f"  {mark} {r.name:<{width}}  {r.detail}   [{r.seconds:.2f}s]")
        if not r.ok and r.fix:
            lines.append(f"    {'':<{width}}  fix      {r.fix}")
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    lines.append("")
    if failed:
        lines.append(f"  {passed} passed \u00b7 {failed} FAILED")
        lines.append("")
        lines.append("  The first failure is the one to fix; later ones are often its")
        lines.append("  consequence. Re-run with -v for full tracebacks.")
    else:
        total = sum(r.seconds for r in results)
        lines.append(f"  {passed} passed \u00b7 all good \u00b7 {total:.1f}s")
        lines.append("")
        lines.append("  This machine can host the server and other PCs can use it.")
        lines.append("  Next:  pbrforge tls init   (HTTPS, so LAN clients get WebGPU)")
        lines.append("         pbrforge serve      (then open the LAN URL on another PC)")
    if kept:
        lines.append(f"\n  workspace kept: {root}")
    lines.append("")
    return "\n".join(lines)
