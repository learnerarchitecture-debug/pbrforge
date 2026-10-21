"""Small, wrap-aware image operations used by the deriving stages.

Everything here is pure Python and deliberately simple. The pipeline has to run
inside the portable single-file build, which carries no numpy/Pillow/OpenCV, so
these are the primitives the stages actually use:

  * ``box_blur_wrap`` -- separable box blur with circular padding. Circular is
    not a detail: a blur that clamps at the edge pulls the far side of the
    texture into the near side and creates a seam *after* ``seamless`` has
    already certified the tile.
  * ``resize_plane`` / ``resize_image`` -- area-average downscale and bilinear
    upscale, both wrap-capable. Area averaging is the only honest downscale for
    a texture: point sampling it aliases fine mortar lines into moire.
  * ``sobel_wrap`` -- the derivative pair the normal stage needs, with wrap.
  * ``normalize_plane`` -- percentile stretch, so one stray specular pixel
    cannot decide the contrast of a whole height map.

No function here mutates its input. That is what lets the stages treat the
source as immutable and makes the cache key honest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .image import Image

__all__ = [
    "plane", "clamp01", "smoothstep", "box_blur_wrap", "resize_plane",
    "resize_image", "sobel_wrap", "percentile", "normalize_plane",
    "luminance_plane", "saturation_plane", "value_field", "unit_normals",
]


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def smoothstep(value: float) -> float:
    value = clamp01(value)
    return value * value * (3.0 - 2.0 * value)


def plane(image: Image, channel: int = 0) -> list[float]:
    """One channel as a flat plane. No copy for a single-channel image."""
    if image.channels == 1 and channel == 0:
        return image.data
    return image.plane(channel)


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile. Same definition as core/filters.py."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if q <= 0.0:
        return ordered[0]
    if q >= 1.0:
        return ordered[-1]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(ordered):
        return ordered[lo]
    return ordered[lo] + frac * (ordered[lo + 1] - ordered[lo])


def normalize_plane(values: list[float], low: float = 0.005,
                    high: float = 0.995) -> list[float]:
    """Percentile stretch to 0..1. A flat plane becomes a constant 0.5."""
    if not values:
        return []
    lo = percentile(values, low)
    hi = percentile(values, high)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    inv = 1.0 / (hi - lo)
    return [clamp01((v - lo) * inv) for v in values]


def _blur_rows(src: list[float], w: int, h: int, radius: int) -> list[float]:
    if radius <= 0:
        return list(src)
    radius = min(radius, max(1, w - 1))
    window = radius * 2 + 1
    out = [0.0] * (w * h)
    inv = 1.0 / window
    for y in range(h):
        row = src[y * w:(y + 1) * w]
        extended = row[-radius:] + row + row[:radius]
        total = sum(extended[:window])
        base = y * w
        for x in range(w):
            out[base + x] = total * inv
            if x + 1 < w:
                total += extended[x + window] - extended[x]
    return out


def _blur_cols(src: list[float], w: int, h: int, radius: int) -> list[float]:
    if radius <= 0:
        return list(src)
    radius = min(radius, max(1, h - 1))
    window = radius * 2 + 1
    inv = 1.0 / window
    out = [0.0] * (w * h)
    columns = [0.0] * w
    for dy in range(-radius, radius + 1):
        base = (dy % h) * w
        for x in range(w):
            columns[x] += src[base + x]
    for y in range(h):
        base = y * w
        for x in range(w):
            out[base + x] = columns[x] * inv
        add = ((y + radius + 1) % h) * w
        sub = ((y - radius) % h) * w
        for x in range(w):
            columns[x] += src[add + x] - src[sub + x]
    return out


def box_blur_wrap(values: list[float], width: int, height: int,
                  radius: int) -> list[float]:
    """Separable box blur with wrap-around. O(width * height)."""
    if radius <= 0 or width < 2 or height < 2:
        return list(values)
    return _blur_cols(_blur_rows(values, width, height, radius),
                      width, height, radius)


def _sample_bilinear(values: list[float], w: int, h: int,
                     x: float, y: float, wrap: bool) -> float:
    if wrap:
        x0 = math.floor(x)
        y0 = math.floor(y)
        fx = x - x0
        fy = y - y0
        x1 = (x0 + 1) % w
        y1 = (y0 + 1) % h
        x0 %= w
        y0 %= h
    else:
        x0 = max(0, min(w - 1, math.floor(x)))
        y0 = max(0, min(h - 1, math.floor(y)))
        fx = clamp01(x - x0)
        fy = clamp01(y - y0)
        x1 = min(w - 1, x0 + 1)
        y1 = min(h - 1, y0 + 1)
    a = values[y0 * w + x0]
    b = values[y0 * w + x1]
    c = values[y1 * w + x0]
    d = values[y1 * w + x1]
    top = a + (b - a) * fx
    bottom = c + (d - c) * fx
    return top + (bottom - top) * fy


def resize_plane(values: list[float], width: int, height: int,
                 new_width: int, new_height: int, *, wrap: bool = True) -> list[float]:
    """Area-average when shrinking, bilinear when growing.

    Area averaging is slower than nearest but it is the difference between a
    resized brick wall and a resized brick wall with a fine grid of lies on it.
    """
    if new_width < 1 or new_height < 1:
        raise ValueError("target size must be positive")
    if width == new_width and height == new_height:
        return list(values)

    shrinking = new_width * new_height < width * height
    out = [0.0] * (new_width * new_height)

    if shrinking:
        for oy in range(new_height):
            sy0 = (oy * height) // new_height
            sy1 = max(sy0 + 1, ((oy + 1) * height) // new_height)
            for ox in range(new_width):
                sx0 = (ox * width) // new_width
                sx1 = max(sx0 + 1, ((ox + 1) * width) // new_width)
                total = 0.0
                count = 0
                for sy in range(sy0, sy1):
                    row = (sy % height) * width if wrap else min(height - 1, sy) * width
                    for sx in range(sx0, sx1):
                        index = row + (sx % width if wrap else min(width - 1, sx))
                        total += values[index]
                        count += 1
                out[oy * new_width + ox] = total / count if count else 0.0
        return out

    for oy in range(new_height):
        sy = (oy + 0.5) * height / new_height - 0.5
        for ox in range(new_width):
            sx = (ox + 0.5) * width / new_width - 0.5
            out[oy * new_width + ox] = _sample_bilinear(values, width, height,
                                                        sx, sy, wrap)
    return out


def resize_image(image: Image, new_width: int, new_height: int, *,
                 wrap: bool = True, renormalise: bool = False) -> Image:
    """Resize every channel. Renormalise only for a tangent-space normal."""
    if (image.width, image.height) == (new_width, new_height):
        return image.copy()
    data: list[float] = []
    for channel in range(image.channels):
        data.extend(resize_plane(plane(image, channel), image.width, image.height,
                                 new_width, new_height, wrap=wrap))
    # The planes above are channel-major; rebuild pixel-major.
    n = image.channels
    if n == 1:
        out = Image(new_width, new_height, 1, image.space, data)
    else:
        out_data = [0.0] * (new_width * new_height * n)
        stride = new_width * new_height
        for channel in range(n):
            offset = channel * stride
            for i in range(stride):
                out_data[i * n + channel] = data[offset + i]
        out = Image(new_width, new_height, n, image.space, out_data)
    if renormalise and out.channels == 3:
        out.renormalise()
    return out


def sobel_wrap(values: list[float], width: int, height: int
               ) -> tuple[list[float], list[float]]:
    """Sobel derivatives /8 with wrap-around indices.

    Returns (dx, dy), both in the same layout as ``values``. ``dy`` is positive
    downwards in image coordinates, which is why ``derive_normal`` negates it
    before encoding an OpenGL +Y normal.
    """
    gx = [0.0] * (width * height)
    gy = [0.0] * (width * height)
    if width < 3 or height < 3:
        return gx, gy

    for y in range(height):
        up = ((y - 1) % height) * width
        row = y * width
        down = ((y + 1) % height) * width
        for x in range(width):
            xm = (x - 1) % width
            xp = (x + 1) % width
            tl = values[up + xm]
            tc = values[up + x]
            tr = values[up + xp]
            ml = values[row + xm]
            mr = values[row + xp]
            bl = values[down + xm]
            bc = values[down + x]
            br = values[down + xp]
            gx[row + x] = ((tr + 2.0 * mr + br)
                           - (tl + 2.0 * ml + bl)) / 8.0
            gy[row + x] = ((bl + 2.0 * bc + br)
                           - (tl + 2.0 * tc + tr)) / 8.0
    return gx, gy


def luminance_plane(image: Image) -> list[float]:
    """Rec.709 luma in the image's own values. One pass, no intermediate Image."""
    data = image.data
    n = image.channels
    out = [0.0] * (image.width * image.height)
    if n < 3:
        for i in range(len(out)):
            out[i] = data[i * n]
        return out
    for i in range(len(out)):
        o = i * n
        out[i] = 0.2126 * data[o] + 0.7152 * data[o + 1] + 0.0722 * data[o + 2]
    return out


def saturation_plane(image: Image) -> list[float]:
    """HSV-style saturation: (max - min) / max, 0 for black."""
    data = image.data
    n = image.channels
    out = [0.0] * (image.width * image.height)
    if n < 3:
        return out
    for i in range(len(out)):
        o = i * n
        r, g, b = data[o], data[o + 1], data[o + 2]
        high = max(r, g, b)
        low = min(r, g, b)
        out[i] = 0.0 if high <= 1e-9 else (high - low) / high
    return out


def value_field(width: int, height: int, *, seed: int = 0,
                octaves: int = 4) -> list[float]:
    """A smooth, deliberately non-tiling macro field in 0..1.

    Used for the ``macro`` variation layer, whose whole job is to break up a
    repeat at a scale larger than the tile. It is *not* periodic on purpose:
    making it periodic would make the repeat louder, not quieter.
    """
    out = [0.0] * (width * height)
    inv_w = 1.0 / max(1, width - 1)
    inv_h = 1.0 / max(1, height - 1)
    total_amp = 0.0
    waves: list[tuple[float, float, float, float]] = []
    state = (seed * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
    for octave in range(max(1, octaves)):
        state = (state * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        angle = ((state >> 33) / 4294967295.0) * math.tau
        state = (state * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        phase = ((state >> 33) / 4294967295.0) * math.tau
        frequency = 1.0 + octave * 1.7
        amplitude = 0.5 ** octave
        waves.append((math.cos(angle) * frequency,
                      math.sin(angle) * frequency, phase, amplitude))
        total_amp += amplitude
    inv_amp = 1.0 / total_amp if total_amp else 1.0
    for y in range(height):
        v = y * inv_h
        base = y * width
        for x in range(width):
            u = x * inv_w
            value = 0.0
            for fx, fy, phase, amplitude in waves:
                value += amplitude * math.sin(math.tau * (fx * u + fy * v) + phase)
            out[base + x] = clamp01(0.5 + 0.5 * value * inv_amp)
    return out


def unit_normals(gx: list[float], gy: list[float], width: int, height: int, *,
                 strength: float = 1.0, clamp_z: float = 0.0,
                 orientation: str = "opengl") -> Image:
    """Encode a tangent-space normal from height derivatives.

    ``gx``/``gy`` are per-pixel derivatives of the height field in 0..1. A raw
    gradient on a 0..1 height is tiny, so ``strength`` is applied after the
    caller has chosen a scale that makes the relief visible. The output is a
    3-channel DATA image in 0..1; OpenGL is +Y, DirectX flips green.
    """
    data = [0.0] * (width * height * 3)
    directx = orientation == "directx"
    for i in range(width * height):
        nx = -gx[i] * strength
        ny = -gy[i] * strength
        nz = 1.0
        if clamp_z > 0.0 and nz < clamp_z:
            nz = clamp_z
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-12:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            inv = 1.0 / length
            nx *= inv
            ny *= inv
            nz *= inv
        green = -ny if directx else ny
        o = i * 3
        data[o] = nx * 0.5 + 0.5
        data[o + 1] = green * 0.5 + 0.5
        data[o + 2] = nz * 0.5 + 0.5
    return Image(width, height, 3, "data", data)
