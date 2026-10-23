"""Surface Lab: deterministic, tileable procedural material generators.

The photo pipeline remains the front door.  This module is for the case where
there is no photograph: a clean tile, a plank floor, a concrete panel or a
grunge blend layer.  Everything is generated with the same rule as the rest of
PBRForge: **periodic by construction**, so the result tiles at every size.

What is generated here is a *source* image.  The ordinary pipeline still owns
lighting removal, height derivation, normal/AO/roughness, QC and packaging.  A
generated height field is also returned for previews and for the direct map
export in ``pbrforge.surface`` CLI, but the web job deliberately goes through
the normal pipeline so a Surface Lab material is not a second, unmeasured
export path.

Standard library only.  At 512 px this is comfortable; 1024 px is allowed but
slower, and the UI says so.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import png, synth
from .core.errors import LimitError, PbrForgeError

PATTERNS = ("brick", "tile", "plank", "panel", "stone", "noise")
WEATHERINGS = ("none", "dust", "grime", "moss", "wear")
MAX_SIZE = 1024
MIN_SIZE = 64


@dataclass(frozen=True)
class SurfaceSpec:
    pattern: str = "brick"
    size: int = 512
    seed: int = 1234
    weathering: str = "dust"
    weather_amount: float = 0.35
    variant: int = 0
    rows: int = 6
    cols: int = 12
    mortar: float = 0.055
    hue: str = "warm"

    def validate(self) -> None:
        if self.pattern not in PATTERNS:
            raise PbrForgeError(
                f"unknown surface pattern {self.pattern!r}; choose one of "
                f"{', '.join(PATTERNS)}")
        if self.weathering not in WEATHERINGS:
            raise PbrForgeError(
                f"unknown weathering {self.weathering!r}; choose one of "
                f"{', '.join(WEATHERINGS)}")
        if not (MIN_SIZE <= int(self.size) <= MAX_SIZE):
            raise LimitError(
                f"surface size {self.size} is outside {MIN_SIZE}..{MAX_SIZE} px.\n"
                f"  why      this generator is pure Python, and larger sizes become "
                f"a multi-minute wait on a laptop\n"
                f"  fix      generate at {MAX_SIZE} px and upscale in the pipeline")
        if not (0.0 <= float(self.weather_amount) <= 1.0):
            raise PbrForgeError("weather_amount must be between 0 and 1")
        if not (1 <= int(self.rows) <= 40 and 1 <= int(self.cols) <= 80):
            raise PbrForgeError("rows must be 1..40 and cols must be 1..80")


@dataclass
class SurfaceResult:
    spec: SurfaceSpec
    albedo: png.Image
    height: png.Image | None = None
    normal: png.Image | None = None
    roughness: png.Image | None = None
    ao: png.Image | None = None
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.spec.pattern,
            "size": self.spec.size,
            "seed": self.spec.seed,
            "weathering": self.spec.weathering,
            "weather_amount": self.spec.weather_amount,
            "variant": self.spec.variant,
            "seconds": round(self.seconds, 3),
            "maps": [name for name in ("albedo", "height", "normal", "roughness", "ao")
                     if getattr(self, name) is not None],
            "notes": list(self.notes),
        }


def _hue_tint(hue: str, r: float, g: float, b: float) -> tuple[float, float, float]:
    if hue == "cool":
        return r * 0.92, g * 0.98, min(1.0, b * 1.08)
    if hue == "neutral":
        mean = (r + g + b) / 3.0
        return (r * 0.72 + mean * 0.28, g * 0.72 + mean * 0.28,
                b * 0.72 + mean * 0.28)
    if hue == "green":
        return r * 0.93, min(1.0, g * 1.04), b * 0.94
    return r, g, b


def _step(edge0: float, edge1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - edge0) / max(1e-9, edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _hash(x: int, y: int, seed: int) -> float:
    return synth._hash2(x, y, seed)


def _albedo_brick(spec: SurfaceSpec) -> SurfaceResult:
    data = synth.brick_material(
        spec.size, rows=spec.rows, cols=spec.cols, seed=spec.seed,
        mortar_width=spec.mortar,
    )
    # A small variant offset changes the per-brick pattern without changing the
    # generator's deterministic contract.
    if spec.variant:
        shift = (spec.variant * 37) % 255
        data.albedo = png.Image(data.albedo.width, data.albedo.height,
                                data.albedo.channels, data.albedo.bit_depth,
                                bytes((b + shift) % 256 for b in data.albedo.data))
    if spec.weathering != "none" or spec.hue != "warm":
        data.albedo = _weather_brick_albedo(data.albedo, spec)
    return SurfaceResult(
        spec=spec, albedo=data.albedo,
        height=data.height and _height_image(data.height, spec.size),
        normal=data.normal, roughness=data.roughness, ao=data.ao,
        notes=["brick bond is periodic by construction"],
    )


def _weather_brick_albedo(image: png.Image, spec: SurfaceSpec) -> png.Image:
    """Apply the same dust/grime/moss/wear palette to the brick generator."""
    size = spec.size
    inv = 1.0 / size
    out = bytearray(image.data)
    amount = spec.weather_amount
    for y in range(size):
        v = y * inv
        row = y * size
        for x in range(size):
            u = x * inv
            i = (row + x) * 3
            r, g, b = out[i] / 255.0, out[i + 1] / 255.0, out[i + 2] / 255.0
            dust = synth.fbm(u, v, 7, seed=spec.seed + 21)
            fine = synth.fbm(u, v, 64, seed=spec.seed + 23)
            if spec.weathering == "dust":
                mask = max(0.0, dust - 0.45) * 1.8 * amount
                r, g, b = r * (1 - mask) + 0.72 * mask, g * (1 - mask) + 0.69 * mask, b * (1 - mask) + 0.61 * mask
            elif spec.weathering == "grime":
                mask = max(0.0, dust - 0.38) * 2.0 * amount
                r, g, b = r * (1 - 0.45 * mask), g * (1 - 0.48 * mask), b * (1 - 0.40 * mask)
            elif spec.weathering == "moss":
                mask = max(0.0, dust - 0.30) * 1.9 * amount
                r, g, b = r * (1 - mask) + 0.20 * mask, g * (1 - mask) + 0.31 * mask, b * (1 - mask) + 0.14 * mask
            elif spec.weathering == "wear":
                mask = max(0.0, fine - 0.55) * 2.2 * amount
                r, g, b = min(1.0, r * (1 - mask) + 0.78 * mask), min(1.0, g * (1 - mask) + 0.74 * mask), min(1.0, b * (1 - mask) + 0.66 * mask)
            if spec.weathering != "none":
                shade = 0.92 + 0.16 * dust
                r, g, b = r * shade, g * shade, b * shade
            r, g, b = _hue_tint(spec.hue, r, g, b)
            out[i] = synth._to8(r)
            out[i + 1] = synth._to8(g)
            out[i + 2] = synth._to8(b)
    return png.Image(image.width, image.height, 3, 8, bytes(out))


def _height_image(values: list[float], size: int) -> png.Image:
    return png.Image(size, size, 1, 8,
                     bytes(synth._to8(v) for v in values))


def _gen_flat_pattern(spec: SurfaceSpec) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """Return (height, r, g, b, roughness), each length size*size."""
    size = spec.size
    inv = 1.0 / size
    heights = [0.0] * (size * size)
    reds = [0.0] * (size * size)
    greens = [0.0] * (size * size)
    blues = [0.0] * (size * size)
    roughs = [0.0] * (size * size)
    rows = spec.rows
    cols = spec.cols

    # Palette per pattern.  Values are deliberate mid-tones: the pipeline can
    # stretch them, but a source that is already clipped cannot be recovered.
    if spec.pattern == "tile":
        base = (0.62, 0.60, 0.56)
        grout = (0.43, 0.42, 0.40)
    elif spec.pattern == "plank":
        base = (0.47, 0.31, 0.17)
        grout = (0.28, 0.18, 0.10)
    elif spec.pattern == "panel":
        base = (0.56, 0.55, 0.52)
        grout = (0.39, 0.38, 0.36)
    elif spec.pattern == "stone":
        base = (0.47, 0.46, 0.43)
        grout = (0.32, 0.31, 0.29)
    else:
        base = (0.52, 0.48, 0.42)
        grout = (0.40, 0.37, 0.34)

    for y in range(size):
        v = y * inv
        for x in range(size):
            u = x * inv
            i = y * size + x
            if spec.pattern == "tile":
                # 4x4 square tiles with a small grout band.  The wrap is exact
                # because both cell coordinates use modulo arithmetic.
                gy = v * rows
                gx = u * cols
                cy, cx = math.floor(gy), math.floor(gx)
                fy, fx = gy - cy, gx - cx
                m = min(_step(0.0, spec.mortar, fx) * _step(0.0, spec.mortar, 1.0 - fx),
                        _step(0.0, spec.mortar, fy) * _step(0.0, spec.mortar, 1.0 - fy))
                tint = _hash(cx % cols, cy % rows, spec.seed)
                h = 0.40 + 0.55 * m + 0.045 * (tint - 0.5)
                r = (grout[0] + (base[0] + 0.10 * (tint - 0.5) - grout[0]) * m)
                g = (grout[1] + (base[1] + 0.08 * (tint - 0.5) - grout[1]) * m)
                b = (grout[2] + (base[2] + 0.06 * (tint - 0.5) - grout[2]) * m)
                ro = 0.58 + 0.22 * (1.0 - m) + 0.06 * (tint - 0.5)
            elif spec.pattern == "plank":
                # Horizontal boards, staggered butt joints.  Grain runs along x.
                by = v * rows
                row = math.floor(by)
                fy = by - row
                stagger = 0.5 * (row % 2)
                bx = u * cols + stagger
                col = math.floor(bx)
                fx = bx - col
                edge = _step(0.0, spec.mortar, fy) * _step(0.0, spec.mortar, 1.0 - fy)
                butt = _step(0.0, spec.mortar * 0.8, fx) * _step(0.0, spec.mortar * 0.8, 1.0 - fx)
                m = min(edge, butt)
                tint = _hash(col % cols, row % rows, spec.seed)
                grain = synth.fbm(u * 3.0 % 1.0, v * 9.0 % 1.0, 24, seed=spec.seed + 4)
                h = 0.36 + 0.55 * m + 0.045 * (grain - 0.5)
                r = grout[0] + (base[0] + 0.14 * (tint - 0.5) + 0.08 * (grain - 0.5) - grout[0]) * m
                g = grout[1] + (base[1] + 0.09 * (tint - 0.5) + 0.055 * (grain - 0.5) - grout[1]) * m
                b = grout[2] + (base[2] + 0.055 * (tint - 0.5) + 0.03 * (grain - 0.5) - grout[2]) * m
                ro = 0.50 + 0.22 * (1.0 - m) + 0.10 * (grain - 0.5)
            elif spec.pattern == "panel":
                # Large panels with a recessed joint and a little formwork grain.
                gy, gx = v * rows, u * cols
                row, col = math.floor(gy), math.floor(gx)
                fy, fx = gy - row, gx - col
                m = min(_step(0.0, spec.mortar, fx) * _step(0.0, spec.mortar, 1.0 - fx),
                        _step(0.0, spec.mortar, fy) * _step(0.0, spec.mortar, 1.0 - fy))
                grain = synth.fbm(u, v, 18, seed=spec.seed + 7)
                pore = synth.fbm(u, v, 96, seed=spec.seed + 9)
                h = 0.40 + 0.54 * m + 0.03 * (grain - 0.5) + 0.018 * (pore - 0.5)
                shade = 0.90 + 0.17 * grain
                r = (grout[0] + (base[0] - grout[0]) * m) * shade
                g = (grout[1] + (base[1] - grout[1]) * m) * shade
                b = (grout[2] + (base[2] - grout[2]) * m) * shade
                ro = 0.67 + 0.15 * (1.0 - m) + 0.06 * (grain - 0.5)
            elif spec.pattern == "stone":
                n1 = synth.fbm(u, v, 8, seed=spec.seed)
                n2 = synth.fbm(u, v, 24, seed=spec.seed + 11)
                n3 = synth.fbm(u, v, 64, seed=spec.seed + 13)
                h = 0.38 + 0.34 * n1 + 0.18 * n2 + 0.08 * n3
                shade = 0.80 + 0.35 * n1
                r = (base[0] + 0.15 * (n2 - 0.5) - 0.07 * (n3 - 0.5)) * shade
                g = (base[1] + 0.13 * (n2 - 0.5) - 0.06 * (n3 - 0.5)) * shade
                b = (base[2] + 0.11 * (n2 - 0.5) - 0.05 * (n3 - 0.5)) * shade
                ro = 0.60 + 0.25 * (n2 - 0.5) + 0.10 * (n3 - 0.5)
                m = 0.0
            else:  # noise
                n1 = synth.fbm(u, v, 6, seed=spec.seed + spec.variant)
                n2 = synth.fbm(u, v, 32, seed=spec.seed + 5)
                n3 = synth.fbm(u, v, 128, seed=spec.seed + 17)
                h = 0.42 + 0.30 * n1 + 0.20 * n2 + 0.08 * n3
                shade = 0.82 + 0.30 * n1
                r = (base[0] + 0.14 * (n2 - 0.5)) * shade
                g = (base[1] + 0.12 * (n2 - 0.5)) * shade
                b = (base[2] + 0.10 * (n2 - 0.5)) * shade
                ro = 0.58 + 0.30 * (n2 - 0.5)
                m = 0.0

            # Weathering is shared.  It is applied to the albedo only; the
            # pipeline still derives its own height and roughness from the
            # resulting source.  That is honest: this is a source-image tool.
            if spec.weathering != "none" and spec.weather_amount > 0.0:
                dust = synth.fbm(u, v, 7, seed=spec.seed + 21)
                fine = synth.fbm(u, v, 64, seed=spec.seed + 23)
                edge = max(0.0, 1.0 - m) if spec.pattern in ("brick", "tile", "plank", "panel") else 0.0
                amount = spec.weather_amount
                if spec.weathering == "dust":
                    dust_mask = max(0.0, dust - 0.45) * 1.8 * amount
                    r, g, b = (r * (1 - dust_mask) + 0.72 * dust_mask,
                               g * (1 - dust_mask) + 0.69 * dust_mask,
                               b * (1 - dust_mask) + 0.61 * dust_mask)
                elif spec.weathering == "grime":
                    grime = max(0.0, dust - 0.38) * 2.0 * amount
                    r, g, b = r * (1 - 0.45 * grime), g * (1 - 0.48 * grime), b * (1 - 0.40 * grime)
                elif spec.weathering == "moss":
                    moss = max(0.0, (1.0 - edge) * (dust - 0.30)) * 1.9 * amount
                    r = r * (1 - moss) + 0.20 * moss
                    g = g * (1 - moss) + 0.31 * moss
                    b = b * (1 - moss) + 0.14 * moss
                elif spec.weathering == "wear":
                    wear = max(0.0, edge * (fine - 0.55)) * 2.2 * amount
                    r = min(1.0, r * (1 - wear) + 0.78 * wear)
                    g = min(1.0, g * (1 - wear) + 0.74 * wear)
                    b = min(1.0, b * (1 - wear) + 0.66 * wear)
                    h = max(0.0, h - 0.12 * wear)
                # A little low-frequency variation keeps the result from looking
                # like a flat procedural swatch.
                shade = 0.92 + 0.16 * dust
                r, g, b = r * shade, g * shade, b * shade

            r, g, b = _hue_tint(spec.hue, r, g, b)
            heights[i] = max(0.0, min(1.0, h))
            reds[i] = max(0.0, min(1.0, r))
            greens[i] = max(0.0, min(1.0, g))
            blues[i] = max(0.0, min(1.0, b))
            roughs[i] = max(0.0, min(1.0, ro))
    return heights, reds, greens, blues, roughs


def _gen_flat(spec: SurfaceSpec) -> SurfaceResult:
    heights, reds, greens, blues, roughs = _gen_flat_pattern(spec)
    size = spec.size
    albedo = png.Image(size, size, 3, 8,
                       bytes(synth._to8(v) for v in
                             (x for i in range(size * size)
                              for x in (reds[i], greens[i], blues[i]))))
    height = _height_image(heights, size)
    normal = synth._normal_from_height(heights, size, strength=1.0)
    ao = synth._ao_from_height(heights, size, radius=max(3, size // 40))
    rough = png.Image(size, size, 1, 8,
                      bytes(synth._to8(v) for v in roughs))
    return SurfaceResult(spec=spec, albedo=albedo, height=height, normal=normal,
                         roughness=rough, ao=ao,
                         notes=[f"{spec.pattern} pattern is tileable by construction"])


def generate(spec: SurfaceSpec) -> SurfaceResult:
    spec.validate()
    started = time.monotonic()
    if spec.pattern == "brick":
        result = _albedo_brick(spec)
    else:
        result = _gen_flat(spec)
    result.seconds = time.monotonic() - started
    return result


def write_maps(result: SurfaceResult, folder: Path) -> dict[str, str]:
    """Write the generated maps and a small manifest.  Used by the CLI/preview."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name in ("albedo", "height", "normal", "roughness", "ao"):
        image = getattr(result, name)
        if image is None:
            continue
        path = folder / f"{name}.png"
        png.write(path, image)
        written[name] = str(path)
    meta = {
        "kind": "surface-lab",
        "spec": {
            "pattern": result.spec.pattern, "size": result.spec.size,
            "seed": result.spec.seed, "weathering": result.spec.weathering,
            "weather_amount": result.spec.weather_amount,
            "variant": result.spec.variant, "rows": result.spec.rows,
            "cols": result.spec.cols, "mortar": result.spec.mortar,
            "hue": result.spec.hue,
        },
        "notes": result.notes,
        "seconds": round(result.seconds, 3),
    }
    import json
    (folder / "surface.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    return written


def preview_data_url(result: SurfaceResult) -> str:
    """Small convenience for the CLI; the server serves a real PNG instead."""
    import base64
    import io
    # png.write needs a path, so use a temporary file rather than reimplementing
    # the encoder.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pbrforge-surface-") as tmp:
        path = Path(tmp) / "albedo.png"
        png.write(path, result.albedo)
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
