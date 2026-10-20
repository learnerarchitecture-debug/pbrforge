"""Procedural, guaranteed-tileable texture synthesis. Standard library only.

Two uses:

  1. `pbrforge selftest` needs a fixture that is *known* to tile, so the seam
     metric can assert a near-perfect score. Generating it beats shipping a binary.
  2. `pbrforge noise` (see docs/FEATURES.md 2.5) — the blend layers -- dust,
     grunge, wear masks -- that make a clean tile read as photographed.

Everything here is periodic by construction: value noise wraps on an integer
lattice, and the brick layout uses modular cell indices. That is the same
discipline the real pipeline needs, and getting it wrong here would make the
seam metric test pass for the wrong reason.

Performance is honest about being pure Python: 256x256 is comfortable, 512x512
takes a few seconds. When numpy is present these routines are only used for
fixtures; the real stages use the vectorised path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .png import Image


# --------------------------------------------------------------------- hashing
def _hash2(x: int, y: int, seed: int = 0) -> float:
    """Deterministic integer hash in [0,1). Avoids `random`, which is slow and
    carries hidden global state that would break byte-identical output."""
    h = (x * 374761393 + y * 668265263 + seed * 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFFFF) / 4294967295.0


def _smooth(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def value_noise(u: float, v: float, period: int, seed: int = 0) -> float:
    """Periodic value noise. `period` must be a positive integer.

    u and v are expected in [0,1); the lattice wraps modulo `period`, which is
    what makes the result tile.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    x, y = u * period, v * period
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = _smooth(x - ix), _smooth(y - iy)
    ix0, iy0 = ix % period, iy % period
    ix1, iy1 = (ix + 1) % period, (iy + 1) % period
    a = _hash2(ix0, iy0, seed)
    b = _hash2(ix1, iy0, seed)
    c = _hash2(ix0, iy1, seed)
    d = _hash2(ix1, iy1, seed)
    return _lerp(_lerp(a, b, fx), _lerp(c, d, fx), fy)


def fbm(u: float, v: float, base_period: int = 8, octaves: int = 4, seed: int = 0) -> float:
    """Fractal sum. Each octave doubles the period, preserving periodicity."""
    total = 0.0
    amp = 0.5
    norm = 0.0
    period = base_period
    for _ in range(octaves):
        total += amp * value_noise(u, v, period, seed)
        norm += amp
        period *= 2
        amp *= 0.5
    return total / norm


# ---------------------------------------------------------------------- bricks
@dataclass(frozen=True)
class Brick:
    mortar: float      # 0 in mortar, 1 on a brick face
    brick_id: float    # stable per-brick random value, used for colour and height


def running_bond(u: float, v: float, rows: int = 6, cols: int = 12,
                 mortar_width: float = 0.055, mortar_height: float | None = None,
                 seed: int = 0) -> Brick:
    """Periodic running-bond brick layout. `cols` is brick cells per row.

    `mortar_width` and `mortar_height` are in **cell units**, so a caller that cares
    about how many pixels wide a joint actually is must convert for itself --
    `brick_material` does, which is where the anti-aliasing floor lives. The default
    height is 1.5x the width because a running-bond course joint reads wider than a
    head joint at the same nominal size.
    """
    y = v * rows
    row = math.floor(y)
    fy = y - row
    offset = (row % 2) * 0.5
    x = u * cols + offset
    col = math.floor(x)
    fx = x - col

    mw = mortar_width
    mh = mortar_width * 1.5 if mortar_height is None else mortar_height
    mx = _step(0.0, mw, fx) * _step(0.0, mw, 1.0 - fx)
    my = _step(0.0, mh, fy) * _step(0.0, mh, 1.0 - fy)
    mortar = min(mx, my)

    cc = col % cols
    rr = row % rows
    return Brick(mortar=mortar, brick_id=_hash2(cc * 7 + 1, rr * 13 + 5, seed))


def _step(edge0: float, edge1: float, x: float) -> float:
    if edge1 == edge0:
        return 1.0 if x >= edge1 else 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return _smooth(t)


# ------------------------------------------------------------------- synthesis
@dataclass
class Material:
    """A generated material set. Height is the authoritative field, per
    docs/PIPELINE.md 0 -- normal, AO and roughness are all derived from it here,
    in the same order the real pipeline uses."""

    size: int
    height: list[float]         # 0..1
    albedo: Image               # RGB 8-bit, flat-lit
    normal: Image               # RGB 8-bit, OpenGL +Y
    roughness: Image            # grey 8-bit
    ao: Image                   # grey 8-bit


def brick_material(size: int = 256, *, rows: int = 6, cols: int = 12,
                   seed: int = 1, mortar_width: float = 0.055,
                   mortar_rgb: tuple[float, float, float] = (0.70, 0.68, 0.63),
                   brick_rgb: tuple[float, float, float] = (0.60, 0.29, 0.235)) -> Material:
    """Generate a tileable brick material at `size` x `size`.

    `mortar_width` is in cell units, as in `running_bond`, and is a *request*: the
    generator floors it so a joint is never thinner than one pixel, because a
    sub-pixel smoothstep band cannot be sampled without its depth depending on the
    phase. See the anti-aliasing note in the body.
    """
    if size < 16:
        raise ValueError("size must be at least 16")

    heights = [0.0] * (size * size)
    rgb = bytearray(size * size * 3)
    rough = bytearray(size * size)
    inv = 1.0 / size

    # Anti-aliasing, in two parts.
    #
    # The mortar profile is a smoothstep band, and an unfiltered procedural
    # generator cannot sample a band narrower than a pixel reliably: whether a joint
    # reads full depth or half depth depends on where that band falls relative to
    # the pixel grid. The joint at fx = 0 is always sampled at its centre, so it is
    # always full depth, and interior joints are not -- which makes the boundary
    # joint deeper than the interior ones. That is a seam in a pattern that has none
    # by construction: this fixture measured e = 1.43 on the seam metric at size 64
    # (score 70/100) while measuring a perfect 1.00 at size 128.
    #
    #   part 1: never let a band be thinner than a pixel
    #   part 2: average four sub-samples per pixel, so joint depth stops depending
    #           on the phase at all
    #
    # Only the mortar term is supersampled. It is the one feature here that is
    # narrow at the pixel scale; the fbm terms are periodic functions sampled at
    # roughly one sample per feature, and cost far more per call.
    mortar_x = max(mortar_width, 1.0 / (size / cols))
    mortar_y = max(mortar_width * 1.5, 1.0 / (size / rows))
    sub_offsets = (0.25, 0.75)
    sub_weight = 1.0 / (len(sub_offsets) ** 2)

    for y in range(size):
        v = y * inv
        row_off = y * size
        for x in range(size):
            u = x * inv
            br = running_bond(u, v, rows, cols, mortar_x, mortar_y, seed=seed)
            tint = br.brick_id
            m = 0.0
            for sy in sub_offsets:
                for sx in sub_offsets:
                    m += running_bond(u + sx * inv, v + sy * inv, rows, cols,
                                      mortar_x, mortar_y, seed=seed).mortar
            m *= sub_weight

            mottle = fbm(u, v, 8, seed=seed)
            grain = fbm(u, v, 64, seed=seed + 1)
            mortar_grain = fbm(u, v, 96, seed=seed + 2)

            # ---- height: bricks proud, mortar recessed, plus surface grain ----
            h = 0.42 + 0.55 * m
            h += 0.045 * (mottle - 0.5) * 2.0
            h += 0.030 * (grain - 0.5) * 2.0 * (0.4 + 0.6 * m)
            h += 0.055 * (mortar_grain - 0.5) * 2.0 * (1.0 - m)
            h += 0.020 * (tint - 0.5) * 2.0
            h = 0.0 if h < 0.0 else (1.0 if h > 1.0 else h)
            heights[row_off + x] = h

            # ---- albedo, flat lit ----
            tv = (tint - 0.5) * 2.0
            br_r = brick_rgb[0] + 0.16 * tv
            br_g = brick_rgb[1] + 0.075 * tv
            br_b = brick_rgb[2] + 0.055 * tv
            shade = (0.86 + 0.26 * mottle) * (0.93 + 0.14 * grain)
            r = _lerp(mortar_rgb[0], br_r, m) * shade + 0.035 * tv * m
            g = _lerp(mortar_rgb[1], br_g, m) * shade
            b = _lerp(mortar_rgb[2], br_b, m) * shade - 0.022 * tv * m
            o = (row_off + x) * 3
            rgb[o] = _to8(r)
            rgb[o + 1] = _to8(g)
            rgb[o + 2] = _to8(b)

            # ---- roughness: mortar rougher, bricks vary ----
            ro = 0.68 + 0.20 * (1.0 - m) + 0.10 * (grain - 0.5) * 2.0 + 0.06 * tv
            rough[row_off + x] = _to8(ro)

    ao = _ao_from_height(heights, size, radius=max(2, size // 26))
    normal = _normal_from_height(heights, size, strength=1.0)

    return Material(
        size=size,
        height=heights,
        albedo=Image(size, size, 3, 8, bytes(rgb)),
        normal=normal,
        roughness=Image(size, size, 1, 8, bytes(rough)),
        ao=ao,
    )


def _to8(v: float) -> int:
    if v <= 0.0:
        return 0
    if v >= 1.0:
        return 255
    return int(v * 255.0 + 0.5)


def _normal_from_height(height: list[float], size: int, strength: float = 1.0) -> Image:
    """Sobel with wrap-around indices.

    Wrap is not a convenience here: a non-wrapping Sobel on a tileable height
    field produces a normal map that is *itself* non-tileable, which is one of the
    bugs docs/DEVELOPMENT.md lists as a property test.
    """
    out = bytearray(size * size * 3)
    s = strength * 5.0

    def at(x: int, y: int) -> float:
        return height[(y % size) * size + (x % size)]

    for y in range(size):
        row = y * size
        for x in range(size):
            tl, t, tr = at(x - 1, y - 1), at(x, y - 1), at(x + 1, y - 1)
            l, r = at(x - 1, y), at(x + 1, y)
            bl, b, br = at(x - 1, y + 1), at(x, y + 1), at(x + 1, y + 1)
            dx = (tr + 2 * r + br) - (tl + 2 * l + bl)
            dy = (bl + 2 * b + br) - (tl + 2 * t + tr)
            nx, ny, nz = -dx * s, -dy * s, 1.0
            inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
            o = (row + x) * 3
            out[o] = _to8(nx * inv * 0.5 + 0.5)
            out[o + 1] = _to8(ny * inv * 0.5 + 0.5)
            out[o + 2] = _to8(nz * inv * 0.5 + 0.5)
    return Image(size, size, 3, 8, bytes(out))


def _ao_from_height(height: list[float], size: int, radius: int = 8) -> Image:
    """Occlusion = local height below a blurred neighbourhood.

    The blur uses a summed-area table over a 2x2 tiling of the image, so the
    window wraps correctly and the cost is O(n^2) rather than O(n^2 * r^2).
    A naive Python blur at radius 8 on 256x256 is roughly 2.7M operations and
    takes seconds; this is about 200k.
    """
    w = size * 2
    sat = [0.0] * ((w + 1) * (w + 1))
    for y in range(w):
        row_sum = 0.0
        sat_row = (y + 1) * (w + 1)
        sat_prev = y * (w + 1)
        for x in range(w):
            row_sum += height[(y % size) * size + (x % size)]
            sat[sat_row + x + 1] = sat[sat_prev + x + 1] + row_sum

    out = bytearray(size * size)

    def box(cx: int, cy: int) -> float:
        x0, y0 = cx - radius, cy - radius
        x1, y1 = cx + radius + 1, cy + radius + 1
        x0 = 0 if x0 < 0 else x0
        y0 = 0 if y0 < 0 else y0
        x1 = w if x1 > w else x1
        y1 = w if y1 > w else y1
        a = sat[y0 * (w + 1) + x0]
        b = sat[y0 * (w + 1) + x1]
        c = sat[y1 * (w + 1) + x0]
        d = sat[y1 * (w + 1) + x1]
        area = max(1, (x1 - x0) * (y1 - y0))
        return (d - b - c + a) / area

    for y in range(size):
        row = y * size
        for x in range(size):
            near = box(x, y)
            far = box(x, y)          # same window; kept explicit for the second radius below
            h = height[row + x]
            occ = max(0.0, near - h) * 4.2 + max(0.0, far - h) * 2.4
            a = 1.0 - occ * 0.9
            out[row + x] = _to8(0.0 if a < 0.0 else (1.0 if a > 1.0 else a))
    return Image(size, size, 1, 8, bytes(out))


# ---------------------------------------------------------------- seam metric
def _join_profile(values: list[int] | list[float], width: int, height: int,
                  horizontal: bool) -> tuple[list[float], float]:
    """(interior join strengths, wrap join strength) for one axis.

    A "join" is the adjacency between neighbouring pixels. For the horizontal
    axis, `interior[k]` is the mean over all rows of |I[y,k+1] - I[y,k]|, for
    k = 0..size-2, and the wrap value is the mean over all rows of
    |I[y,0] - I[y,size-1]|.

    Both are the same kind of quantity measured the same way, which is the whole
    point: the wrap is one more member of the family, so it can be compared
    against the others directly.
    """
    interior: list[float] = []
    if horizontal:
        for k in range(width - 1):
            total = 0.0
            for line in range(height):
                base = line * width
                total += abs(float(values[base + k + 1]) - float(values[base + k]))
            interior.append(total / max(1, height))
        total = 0.0
        for line in range(height):
            base = line * width
            total += abs(float(values[base + width - 1]) - float(values[base]))
        return interior, total / max(1, height)

    for k in range(height - 1):
        total = 0.0
        for line in range(width):
            total += abs(float(values[(k + 1) * width + line])
                         - float(values[k * width + line]))
        interior.append(total / max(1, width))
    total = 0.0
    for line in range(width):
        total += abs(float(values[(height - 1) * width + line])
                     - float(values[line]))
    return interior, total / max(1, width)


def seam_ratio_grey(values: list[int] | list[float], width: int,
                    height: int | None = None) -> float:
    """How much worse is the wrap join than the joins this texture already has?

    This is the definition of the seam metric, and it took two attempts to get
    right. The first version compared the wrap join against the *mean* of every
    interior join. That is a units mismatch, and it fails on exactly the textures
    this tool exists for: a brick wall has a dozen sharp mortar joints and
    hundreds of smooth pixels between them, so the mean is diluted to almost
    nothing and a perfectly tileable brick field scored 3.8x "too rough" -- a
    false positive on a flawless texture.

    The corrected question is comparative and like-for-like:

        is the wrap join worse than the joins the texture already contains?

    `e` is `wrap / p95(interior)`, taking the worse of the two axes:

        e = 1.0   the wrap is no worse than the texture's own strong joins.
                  There is nothing to see when it tiles. Perfect.
        e = 2.0   the wrap is twice as strong as the strongest join in the
                  texture, so it is the single most prominent line in the tile.
        e = 4.0   glaring.

    The 95th percentile rather than the maximum, so one stray aliased pixel in
    the interior cannot inflate the reference and hide a real seam. `e` is never
    below 1: a join cannot be better than seamless.

    Known limitation, stated rather than hidden: this measures the WRAP only. A
    texture spliced from two mismatched halves is broken in the interior and
    scores 1.0 here. That is a different defect with a different check; see
    docs/QC_METRICS.md 4 (interior audit).
    """
    if height is None:
        height = width
    worst = 1.0
    for horizontal in (True, False):
        interior, wrap = _join_profile(values, width, height, horizontal)
        if not interior:
            continue
        interior.sort()
        reference = interior[min(len(interior) - 1, int(len(interior) * 0.95))]
        if reference < 1e-6:
            continue          # a perfectly flat axis has no joins to compare against
        worst = max(worst, wrap / reference)
    return worst


def interior_join_reference(values: list[int] | list[float], width: int,
                            height: int | None = None) -> float:
    """The p95 interior join the wrap is compared against, worst axis.

    This is the denominator of `seam_ratio_grey`, exposed so `seamless` can say
    whether its heal ramp is steeper than the strongest join the texture already
    contained. Measured before the heal, never after it.
    """
    if height is None:
        height = width
    worst = 0.0
    for horizontal in (True, False):
        interior, _wrap = _join_profile(values, width, height, horizontal)
        if not interior:
            continue
        interior.sort()
        reference = interior[min(len(interior) - 1,
                                 int(len(interior) * 0.95))]
        worst = max(worst, reference)
    return worst


def ratio_to_score(e: float) -> int:
    """Map `e` to 0..100, monotonically.

        100 / e, clamped.

    So 100 means the wrap is no worse than the texture's own worst join, 50 means
    twice as strong, 25 means four times. Deliberately no magic constants: the
    number is explainable in one sentence, which matters because it is the
    headline figure in the UI and users will (rightly) ask what it means.
    """
    if e <= 1.0:
        return 100
    if e >= 100.0:
        return 1
    return int(round(100.0 / e))
