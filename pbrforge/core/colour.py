"""Colour spaces, and the guards that keep data maps out of them.

The rule this module exists to enforce, in one line:

    a data map is not colour, and gamma is a colour operation

A normal, roughness, AO, metallic, height or opacity map holds *measurements*. An
sRGB transfer function is for three things only -- albedo, emissive, and a preview
of either -- and running it over a measurement is not a small error. It is silent,
because the result still looks like an image:

  - **roughness**: midtones move, so the whole surface reads glossier than the
    artist set and the preset's `base` looks wrong. Nothing points at the gamma.
  - **normal**: the vectors stop being unit length and the map loses its blue
    dominance; the surface renders soft, and slightly dark.
  - **height**: relief changes shape, and a 16-bit height has no reason to exist if
    its range is about to be warped.

The classic real-world version of this bug is a normal map that looks slightly
washed out and is blamed on the renderer. It is nearly always a transfer function.

Exactness matters here too. The sRGB curve is **piecewise**, with a linear segment
near black, and not `c ** 2.2`. The approximation is wrong by up to 4% in the
lowest two stops -- which is where a roughness map's subtle variation lives, and
where an inverted shadow decision is made. This module uses the IEC 61966-2-1
formulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import ColourSpaceError

# ------------------------------------------------------------------ the spaces


class Space(str, Enum):
    """What the numbers in an image *mean*.

    A `str` subclass so the value round-trips through JSON, a sidecar or a config
    file unchanged (`"data"`, not `Space.DATA`), while still being a closed set
    that a typo cannot silently satisfy.

    Three spaces, and the third is the one that matters:

      SRGB    gamma-encoded colour -- albedo, emissive. What a screenshot looks like.
      LINEAR  linear-light colour -- colour after the transfer function is undone,
              i.e. what the shading maths expects.
      DATA    not colour at all. A measurement: height, normal, roughness, AO,
              metallic, opacity, cavity, curvature.
    """

    SRGB = "srgb"
    LINEAR = "linear"
    DATA = "data"

    # Explicit, because a str-mixin enum inherits Enum.__str__ on 3.11+ and would
    # print "Space.DATA" -- which would leak into sidecars and error messages.
    __str__ = str.__str__

    @property
    def is_colour(self) -> bool:
        """True for SRGB and LINEAR. False for DATA."""
        return self is not Space.DATA

    @property
    def is_data(self) -> bool:
        return self is Space.DATA


# --------------------------------------------------------- the transfer function

# IEC 61966-2-1. The linear segment is not a rounding detail: it exists because a
# pure power curve has an infinite slope at zero, which amplifies noise in exactly
# the darkest part of a texture.
_SRGB_KNEE = 0.04045          # encode side threshold (linear-light value)
_SRGB_KNEE_SLOPE = 12.92
_SRGB_OFFSET = 0.055
_SRGB_GAMMA = 2.4

_LINEAR_KNEE = 0.0031308      # decode side threshold (encoded value)


def srgb_to_linear(c: float) -> float:
    """Encoded -> linear light. One channel, 0..1 nominal, values outside allowed."""
    if c <= _SRGB_KNEE:
        return c / _SRGB_KNEE_SLOPE
    return ((c + _SRGB_OFFSET) / (1.0 + _SRGB_OFFSET)) ** _SRGB_GAMMA


def linear_to_srgb(c: float) -> float:
    """Linear light -> encoded. The exact inverse of `srgb_to_linear`."""
    if c <= _LINEAR_KNEE:
        return c * _SRGB_KNEE_SLOPE
    return (1.0 + _SRGB_OFFSET) * (c ** (1.0 / _SRGB_GAMMA)) - _SRGB_OFFSET


# An 8-bit source only has 256 distinct values, so the conversion can be a lookup
# table rather than 16M calls to `pow`. This is the difference between a levels
# adjustment taking 0.2 s and taking 20 s, and it is exact rather than approximate.
SRGB8_TO_LINEAR: tuple[float, ...] = tuple(srgb_to_linear(i / 255.0) for i in range(256))

# ITU-R BT.709 luma weights, which are the correct ones for sRGB primaries.
LUMA_709 = (0.2126, 0.7152, 0.0722)


def luma(r: float, g: float, b: float) -> float:
    """Perceptual luminance of (nominally linear) RGB.

    Used for the height estimate and for the delight stage. Note the weights
    differ from BT.601 (0.299/0.587/0.114) by up to 20% in the red and blue
    channels; using 601 on 709 primaries skews a red brick's derived height
    against a grey mortar's, which shows up as relief that does not match colour.
    """
    return r * LUMA_709[0] + g * LUMA_709[1] + b * LUMA_709[2]


def to_linear_values(values: list[float]) -> None:
    """In-place `srgb_to_linear`. In place because a 4k RGB map is 50M floats and
    copying it to return a new list is 400 MB of churn for no benefit."""
    for i, v in enumerate(values):
        values[i] = srgb_to_linear(v)


def to_srgb_values(values: list[float]) -> None:
    """In-place `linear_to_srgb`."""
    for i, v in enumerate(values):
        values[i] = linear_to_srgb(v)


# ------------------------------------------------------- the map-space registry


@dataclass(frozen=True)
class MapSpec:
    """What one exported map is, per the table in docs/ARCHITECTURE.md 6."""

    space: Space
    bit_depth: int
    written_as: str
    #: True when a 16-bit file has a real reason to exist. 8-bit height bands
    #: visibly on a large flat surface, so this is not a preference.
    needs_16: bool = False
    #: Allowed channel counts. The registry refuses a map whose channel count
    #: would make a downstream stage misinterpret it; a 1-channel normal is a
    #: height map with a misleading name.
    channels: tuple[int, ...] = (1,)


# The registry is the single place that knows a map's colour space. Everything
# else asks it. `pack` cannot invent an encoding for a map it has not registered,
# which is the point.
MAP_SPECS: dict[str, MapSpec] = {
    "albedo":    MapSpec(Space.SRGB, 8,  "sRGB PNG", channels=(3, 4)),
    "emissive":  MapSpec(Space.SRGB, 8,  "sRGB PNG", channels=(3, 4)),
    "height":    MapSpec(Space.DATA, 16, "16-bit linear PNG, TIFF or EXR",
                         needs_16=True, channels=(1,)),
    "normal":    MapSpec(Space.DATA, 16, "linear PNG (16 offline, 8 realtime)",
                         channels=(3,)),
    "roughness": MapSpec(Space.DATA, 8,  "linear PNG", channels=(1,)),
    "metallic":  MapSpec(Space.DATA, 8,  "linear PNG", channels=(1,)),
    "ao":        MapSpec(Space.DATA, 8,  "linear PNG", channels=(1,)),
    "cavity":    MapSpec(Space.DATA, 8,  "linear PNG", channels=(1,)),
    "curvature": MapSpec(Space.DATA, 8,  "linear PNG", channels=(1,)),
    "opacity":   MapSpec(Space.DATA, 8,  "linear PNG, usually with alpha",
                         channels=(1, 2, 4)),
    # Legacy renderers ask for glossiness instead of roughness. Still a data map,
    # still never gamma -- it is a different measurement of the same property.
    "glossiness": MapSpec(Space.DATA, 8, "linear PNG", channels=(1,)),
    # Delight's residual and the macro-scale lighting field. Both can be grey or
    # colour, because a residual highlight is coloured and a macro map may be a
    # coloured bounce field.
    "shading":   MapSpec(Space.DATA, 8,  "linear PNG", channels=(1, 3)),
    "macro":     MapSpec(Space.DATA, 8,  "linear PNG", channels=(1, 3)),
}


def spec_for(map_kind: str) -> MapSpec:
    """(`MapSpec`) or `ColourSpaceError` naming the registered kinds."""
    try:
        return MAP_SPECS[map_kind]
    except KeyError:
        known = ", ".join(sorted(MAP_SPECS))
        raise ColourSpaceError(
            f"'{map_kind}' is not a registered map kind, so its colour space is "
            f"unknown and it cannot be written safely.\n"
            f"  known    {known}"
        ) from None


def assert_writable(map_kind: str, space: Space | str, *, bit_depth: int | None = None) -> None:
    """The export gate. Raises `ColourSpaceError` before any file exists.

    This is the hard gate from docs/PIPELINE.md 1.14: "pack refuses to write a
    `data` map into an sRGB-tagged PNG". It is an error rather than a warning
    because the only reason to write a normal map as sRGB is a bug, and a warning
    that scrolls past is how a wrong normal map reaches a renderer.

    Bit depth is deliberately *not* gated. Writing a 16-bit height as 8-bit is a
    quality loss the user is allowed to choose (realtime targets do it on purpose);
    `ingest` warns about the reverse case, and that is the right place for it.
    """
    spec = spec_for(map_kind)
    space = Space(space)

    if space is spec.space:
        return

    if spec.space.is_data and space.is_colour:
        raise ColourSpaceError(
            f"refusing to write the {map_kind} map as {space}.\n"
            f"  expected {spec.space} ({spec.written_as})\n"
            f"  why      a {map_kind} map is a measurement, not colour. Tagging it "
            f"{space} invites the reader to apply the sRGB transfer function, which "
            f"changes the numbers silently -- the image still looks plausible.\n"
            f"  fix      keep the pixels as they are and write them untagged"
        )

    raise ColourSpaceError(
        f"refusing to write the {map_kind} map as {space}.\n"
        f"  expected {spec.space} ({spec.written_as})\n"
        f"  why      {map_kind} is colour, and the renderer's shading maths expects "
        f"linear light. Writing {space} pixels as {spec.space} double-applies the "
        f"transfer function, which shows up as a washed-out or crushed surface.\n"
        f"  fix      convert with Image.to_{spec.space.value}() first"
    )
