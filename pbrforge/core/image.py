"""The processing image: colour-space-typed pixels, and a tiling-safe resample.

`core/png.py` holds the *interchange* type -- bytes, channels and a bit depth, dumb
on purpose. This is the *processing* type: floats in 0..1, plus a `Space` that says
what those floats mean. Two rules are enforced here and nowhere else, because both
of them are silent when broken:

**1. A data map never receives gamma.** `to_linear()` and `to_srgb()` raise on a
`Space.DATA` image. The rationale is in `colour.py`; the point of enforcing it in
the type rather than in a convention is that the failure mode is a plausible-looking
image.

**2. Every resample of a tileable image wraps.** A resample that samples *outside*
the image has to decide what is there. If it clamps or extrapolates, the pixels it
invents at the left edge are not the pixels that live at the right edge, so the
tile stops matching itself by exactly the width of the kernel -- a seam reappears
after the seam stage has already run and reported success. The symptom is
"it looks seamless in the preview but the exported 2k version has a line down it",
and it is why `wrap` defaults to True here and why
`test_resample_preserves_tileability` exists.

Both rules are cheap to enforce at the type and expensive to retrofit, which is
why this module was written before any stage that would call it.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field

from . import png
from .colour import (
    SRGB8_TO_LINEAR,
    Space,
    linear_to_srgb,
    luma,
    srgb_to_linear,
    to_linear_values,
    to_srgb_values,
)
from .errors import ColourSpaceError, LimitError

#: Lanczos-3 is the default because it is the sharpest kernel that does not ring
#: visibly at 8-bit. It *does* overshoot on hard edges (that is what makes it
#: sharp), so values are clamped at quantisation time rather than here -- the
#: arithmetic stays honest and the storage step is where the range really ends.
KERNEL_LANCZOS3 = "lanczos3"
KERNEL_TRIANGLE = "triangle"

_MAX_TAPS = 32

#: Operation budget for the pure-Python resample. Above this it refuses and names
#: `pip install numpy`, which is the same philosophy as `ingest.max_pixels`: a clean
#: refusal beats either an OOM or four silent minutes. Measured, not guessed --
#: `selftest` prints the observed rate and this number is set from it.
PURE_PYTHON_OPS_LIMIT = 60_000_000


def _lanczos3(x: float) -> float:
    """sinc(x) * sinc(x/3), zero outside +-3."""
    if x < 0.0:
        x = -x
    if x < 1e-12:
        return 1.0
    if x >= 3.0:
        return 0.0
    px = math.pi * x
    return (math.sin(px) / px) * (math.sin(px / 3.0) / (px / 3.0))


def _triangle(x: float) -> float:
    """Tent filter. Monotone, so no overshoot at all, and cheaper than Lanczos."""
    x = -x if x < 0.0 else x
    return 1.0 - x if x < 1.0 else 0.0


_KERNELS = {KERNEL_LANCZOS3: (_lanczos3, 3.0), KERNEL_TRIANGLE: (_triangle, 1.0)}


def _weights(in_size: int, out_size: int, kernel: str, wrap: bool) -> list[list[tuple[int, float]]]:
    """Precomputed, normalised tap lists -- one per output position.

    Precomputing matters for more than speed: it means the weights for a given
    output pixel are **normalised once**, and an un-normalised stretched kernel is
    the bug that makes downscaling change an image's mean brightness. A resample
    that shifts the mean would also break the delight stage's mean-luminance
    guarantee, so it is asserted in `selftest` as an identity rather than trusted.

    `filter_scale` is the anti-aliasing term: downscaling by 4 widens the kernel by
    4 so that the samples it averages cover the same *input* area one output pixel
    represents. Without it, a 4k -> 1k resample aliases structure into moire, and
    the texture looks like it has a fine grid in it that the source never had.
    """
    if in_size < 1 or out_size < 1:
        raise LimitError(f"cannot resample {in_size} px to {out_size} px")
    fn, support = _KERNELS[kernel]
    scale = in_size / out_size
    filter_scale = scale if scale > 1.0 else 1.0
    half = int(math.ceil(support * filter_scale))
    taps = min(_MAX_TAPS, half * 2)

    plan: list[list[tuple[int, float]]] = []
    for i in range(out_size):
        # +0.5 aligns pixel *centres*, so that a resample to the same size is the
        # identity rather than a half-pixel shift. Getting this wrong is a
        # half-pixel misalignment between an albedo and its derived normal map,
        # which is small enough to look like a modelling error.
        centre = (i + 0.5) * scale - 0.5
        base = math.floor(centre)
        row: list[tuple[int, float]] = []
        total = 0.0
        for j in range(taps):
            offset = j - (taps // 2) + 1
            src = base + offset
            w = fn((centre - src) / filter_scale)
            if w == 0.0:
                continue
            if wrap:
                idx = src % in_size
            else:
                idx = 0 if src < 0 else (in_size - 1 if src >= in_size else src)
            if total == 0.0 and not row:
                pass
            row.append((idx, w))
            total += w
        if total == 0.0:
            # Degenerate only if every tap was zero, which cannot happen for a
            # kernel with support >= 1, but dividing by it is worse than falling
            # back to nearest-neighbour.
            row = [(int(centre) % in_size if wrap
                    else max(0, min(in_size - 1, int(centre))), 1.0)]
        else:
            row = [(idx, w / total) for idx, w in row]
        plan.append(row)
    return plan


@dataclass
class Image:
    """Row-major float pixels in 0..1 nominal, tagged with what they mean.

    `data` may hold values outside 0..1 (a Lanczos overshoot, a contrast expansion
    mid-stage). That is deliberate: clamping in the maths would hide the overshoot
    from a later stage that might want to see it, and the range only truly ends at
    storage. `to_png()` is where clamping happens.
    """

    width: int
    height: int
    channels: int
    space: Space = Space.DATA
    data: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.space = Space(self.space)
        if self.width < 1 or self.height < 1:
            raise LimitError(f"image must have a positive size, got "
                             f"{self.width}x{self.height}")
        if self.channels not in (1, 2, 3, 4):
            raise LimitError(f"unsupported channel count {self.channels} "
                             f"(1 grey, 2 grey+alpha, 3 rgb, 4 rgba)")
        expected = self.width * self.height * self.channels
        if len(self.data) != expected:
            raise LimitError(
                f"pixel buffer holds {len(self.data)} values, expected {expected} "
                f"for {self.width}x{self.height}x{self.channels}"
            )

    # ---------------------------------------------------------------- construct

    @classmethod
    def filled(cls, width: int, height: int, channels: int, space: Space | str,
               value: float = 0.0) -> Image:
        return cls(width, height, channels, Space(space),
                   [value] * (width * height * channels))

    @classmethod
    def from_png(cls, image: png.Image, space: Space | str) -> Image:
        """Normalise an interchange image to floats in 0..1. **No gamma here.**

        The space is *asserted by the caller*, not inferred, and the transfer
        function is deliberately not applied. Ingest is the stage that decides what
        a file is; this only converts the storage encoding to floats. Keeping those
        two jobs separate is what stops "we decoded the PNG and it got gamma" from
        being possible.
        """
        space = Space(space)
        if image.bit_depth == 16:
            raw = struct.unpack(f">{image.width * image.height * image.channels}H", image.data)
            inv = 1.0 / 65535.0
            values = [v * inv for v in raw]
        else:
            # The 8-bit sRGB case is the common one, so it takes the exact
            # lookup table rather than 16M divisions.
            values = ([SRGB8_TO_LINEAR[b] for b in image.data]
                      if space is Space.LINEAR
                      else [b / 255.0 for b in image.data])
        return cls(image.width, image.height, image.channels, space, values)

    def to_png(self, bit_depth: int = 8) -> png.Image:
        """Quantise and clamp. The only place the 0..1 range is enforced."""
        if bit_depth not in (8, 16):
            raise LimitError(f"cannot write {bit_depth}-bit PNG")
        maxv = 255 if bit_depth == 8 else 65535
        vals = []
        for v in self.data:
            if v <= 0.0:
                vals.append(0)
            elif v >= 1.0:
                vals.append(maxv)
            else:
                vals.append(int(v * maxv + 0.5))
        if bit_depth == 16:
            data = struct.pack(f">{len(vals)}H", *vals)
        else:
            data = bytes(vals)
        return png.Image(self.width, self.height, self.channels, bit_depth, data)

    def copy(self) -> Image:
        return Image(self.width, self.height, self.channels, self.space, list(self.data))

    # ------------------------------------------------------------------ reading

    def offset(self, x: int, y: int) -> int:
        """Flat index of (x, y), wrapping. Wrap on *read* is what makes every
        neighbourhood operation tiling-safe for free -- Sobel, blur, box sums."""
        return ((y % self.height) * self.width + (x % self.width)) * self.channels

    def pixel(self, x: int, y: int) -> tuple[float, ...]:
        """One pixel, wrapping. Not called per-pixel in hot loops."""
        o = self.offset(x, y)
        return tuple(self.data[o:o + self.channels])

    def channel(self, x: int, y: int, c: int = 0) -> float:
        return self.data[self.offset(x, y) + c]

    def plane(self, c: int = 0) -> list[float]:
        """One channel as a flat `width * height` list, no copy for grey."""
        if self.channels == 1 and c == 0:
            return self.data
        out = [0.0] * (self.width * self.height)
        n = self.channels
        for i in range(len(out)):
            out[i] = self.data[i * n + c]
        return out

    def mean(self, c: int = 0) -> float:
        """Mean of one channel. The albedo-plausibility and delight checks use it."""
        plane = self.plane(c)
        return sum(plane) / len(plane) if plane else 0.0

    def luminance(self) -> Image:
        """BT.709 luma as a 1-channel image, same size and space.

        This is the height estimate's input, and note it is a *weighted* sum: for
        a coloured surface the arithmetic mean of RGB is not its luminance, and
        using the mean misplaces relief on saturated materials.
        """
        if self.channels < 3:
            return self.copy()
        out = [0.0] * (self.width * self.height)
        n = self.channels
        d = self.data
        for i in range(len(out)):
            o = i * n
            out[i] = luma(d[o], d[o + 1], d[o + 2])
        return Image(self.width, self.height, 1, self.space, out)

    # ------------------------------------------------------- colour conversions

    def to_linear(self) -> Image:
        """sRGB -> linear light. Refuses outright for a data map."""
        self._refuse_gamma("to_linear")
        if self.space is Space.LINEAR:
            return self.copy()
        out = self.copy()
        to_linear_values(out.data)
        out.space = Space.LINEAR
        return out

    def to_srgb(self) -> Image:
        """Linear light -> sRGB. Refuses outright for a data map."""
        self._refuse_gamma("to_srgb")
        if self.space is Space.SRGB:
            return self.copy()
        out = self.copy()
        to_srgb_values(out.data)
        out.space = Space.SRGB
        return out

    def _refuse_gamma(self, method: str) -> None:
        if self.space.is_data:
            raise ColourSpaceError(
                f"refusing {method}() on a data map.\n"
                f"  what     this image is tagged '{self.space}', which means its "
                f"values are measurements, not colour\n"
                f"  why      the transfer function would change the numbers and "
                f"nothing would look wrong. On a roughness map it moves the "
                f"midtones; on a normal map it unsticks the vectors from unit "
                f"length; on a height map it reshapes the relief.\n"
                f"  fix      if the pixels really are colour, build the image with "
                f"space='srgb'; if they are measurements, pass them through unchanged"
            )

    # ------------------------------------------------------------------ resample

    def resample(self, width: int, height: int, *, wrap: bool = True,
                 kernel: str = KERNEL_LANCZOS3,
                 renormalise: bool = False) -> Image:
        """Resize with wrap-around (circular) kernel padding by default.

        `wrap=True` is the whole point: it is what keeps a tileable image tileable.
        A caller asking for `wrap=False` is resampling something that is *not* a
        tile -- a macro variation map, an atlas cell, a reference thumbnail -- and
        gets edge clamping.

        `renormalise=True` applies the normal-map rule from PIPELINE.md 1.7: a
        bilinear or Lanczos resample shortens the vectors, and a shortened normal
        map renders as a desaturated, weak surface. It is opt-in because it is only
        meaningful for a 3-channel tangent-space normal, and guessing that from
        `space == data` would corrupt an ORM-packed map.
        """
        if width == self.width and height == self.height:
            out = self.copy()
        else:
            if kernel not in _KERNELS:
                raise LimitError(f"unknown kernel '{kernel}' "
                                 f"(known: {', '.join(sorted(_KERNELS))})")

            scale_x = self.width / width
            scale_y = self.height / height
            support_half = int(math.ceil(_KERNELS[kernel][1] * max(1.0, scale_x)))
            ops = width * height * self.channels * (2 * support_half + 1) * 6
            if ops > PURE_PYTHON_OPS_LIMIT:
                raise LimitError(
                    f"refusing a {self.width}x{self.height} -> {width}x{height} "
                    f"resample: about {ops / 1e6:.0f}M operations in pure Python.\n"
                    f"  fix      pip install numpy (roughly 100x faster here)\n"
                    f"  or       resample a smaller crop first"
                )

            plan_x = _weights(self.width, width, kernel, wrap)
            plan_y = _weights(self.height, height, kernel, wrap)
            n = self.channels
            src = self.data

            # Horizontal pass first: intermediate is width_out x height_in.
            mid = [0.0] * (width * self.height * n)
            for y in range(self.height):
                row_off = y * self.width * n
                mid_off = y * width * n
                for ox in range(width):
                    acc = [0.0] * n
                    for idx, w in plan_x[ox]:
                        o = row_off + idx * n
                        for c in range(n):
                            acc[c] += src[o + c] * w
                    mo = mid_off + ox * n
                    for c in range(n):
                        mid[mo + c] = acc[c]

            out_data = [0.0] * (width * height * n)
            for oy in range(height):
                out_off = oy * width * n
                for ox in range(width):
                    acc = [0.0] * n
                    col = ox * n
                    for idx, w in plan_y[oy]:
                        o = idx * width * n + col
                        for c in range(n):
                            acc[c] += mid[o + c] * w
                    for c in range(n):
                        out_data[out_off + col + c] = acc[c]

            out = Image(width, height, n, self.space, out_data)

        if renormalise:
            out.renormalise()
        return out

    # ------------------------------------------------------------------- normals

    def renormalise(self) -> Image:
        """Unit-length the RGB triples of a tangent-space normal map, in place.

        Only valid for a 3-channel data map. Encoded normals live in 0..1 with
        0.5 as zero, so the decode is `2v - 1` and the encode is `v/2 + 0.5`.

        Worth being precise about why this is not automatic: applied to an
        ORM/ORM-style packed map (AO in R, roughness in G, metallic in B) this
        would destroy the data completely, and the result would still look like a
        plausible image. It is the same class of mistake as a stray gamma.
        """
        if not self.space.is_data:
            raise ColourSpaceError(
                f"refusing to renormalise a {self.space} image: "
                f"normalising light values is meaningless"
            )
        if self.channels != 3:
            raise ColourSpaceError(
                f"refusing to renormalise a {self.channels}-channel map.\n"
                f"  why      a tangent-space normal map has exactly 3 channels, and "
                f"a packed map (AO/roughness/metallic) also has 3 -- normalising the "
                f"second would destroy it while still looking plausible"
            )
        d = self.data
        for i in range(0, len(d), 3):
            x = d[i] * 2.0 - 1.0
            y = d[i + 1] * 2.0 - 1.0
            z = d[i + 2] * 2.0 - 1.0
            length = math.sqrt(x * x + y * y + z * z)
            if length < 1e-9:
                # A zero vector has no direction. Leaving the encoded (0.5,0.5,0.5)
                # alone is the only non-arbitrary choice, and it is worth a comment
                # because "divide by zero" would be the natural slip.
                continue
            inv = 1.0 / length
            d[i] = x * inv * 0.5 + 0.5
            d[i + 1] = y * inv * 0.5 + 0.5
            d[i + 2] = z * inv * 0.5 + 0.5
        return self

    def unit_error(self) -> float:
        """Mean |1 - |n|| over a normal map's vectors. ~0 is correct.

        8-bit encoding alone costs about 0.005 -- that is quantisation, not a bug --
        so a threshold below that is testing the bit depth rather than the maths.
        """
        d = self.data
        total = 0.0
        count = 0
        for i in range(0, len(d), 3):
            x = d[i] * 2.0 - 1.0
            y = d[i + 1] * 2.0 - 1.0
            z = d[i + 2] * 2.0 - 1.0
            total += abs(1.0 - math.sqrt(x * x + y * y + z * z))
            count += 1
        return total / count if count else 0.0

    # --------------------------------------------------------------------- hash

    def content_hash(self, bits: int = 16) -> str:
        """A stable identity for the cache. Deterministic across platforms.

        Values are quantised *before* hashing, at 16 bits by default. Hashing raw
        floats would make every run a cache miss, because a float that differs in
        its 17th digit is not a different image but hashes as one. Quantising to
        the precision the pipeline actually stores means the hash answers the only
        question the cache cares about: would writing this produce the same bytes?

        Cost is real in pure Python -- 3M `struct` values for a 4k RGB map -- so
        artifacts that already exist as files are hashed from their bytes by
        `core/cache.py` instead. This is for in-memory comparisons and tests.
        """
        if bits not in (8, 16):
            raise LimitError("content_hash supports 8 or 16 bits")
        maxv = 255 if bits == 8 else 65535
        ints = []
        for v in self.data:
            if v <= 0.0:
                ints.append(0)
            elif v >= 1.0:
                ints.append(maxv)
            else:
                ints.append(int(v * maxv + 0.5))
        h = hashlib.sha256()
        h.update(f"{self.width}x{self.height}x{self.channels}:{self.space}:{bits}\n"
                 .encode("ascii"))
        h.update(struct.pack(f">{len(ints)}H", *ints) if bits == 16 else bytes(ints))
        return h.hexdigest()

    def __repr__(self) -> str:
        return (f"Image({self.width}x{self.height}, {self.channels}ch, "
                f"{self.space})")


def srgb_from_linear_value(v: float) -> float:
    """Single-value convenience, mirrored from `colour`, for callers that already
    have a scalar and should not have to import two modules."""
    return linear_to_srgb(v)


def linear_from_srgb_value(v: float) -> float:
    """Single-value convenience, mirrored from `colour`."""
    return srgb_to_linear(v)
