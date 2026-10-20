"""File writers, and the gates that run before any bytes are produced.

Two things live here that could have lived in `stages/pack.py`, and they are here
because they are the *reason* the export layer exists as a layer:

**The colour-space gate.** `core/colour.assert_writable` is called for every
registry map, before a file is opened, for every resolution. A normal map written
as sRGB looks fine in every viewer and renders wrong in every renderer, so the
only useful moment to refuse is now.

**The container decision.** PNG for everything that can be PNG; uncompressed
TIFF where the doc names it (16-bit height, where the doc's own matrix says
"TIFF / EXR"). EXR needs a half-float encoder this build does not have, and JPEG
is banned for data maps by PRESETS.md 3 -- both are refused with the alternative
named rather than silently substituted, because a silently substituted container
is a claim about the deliverable that is not true.

Everything is written through `png.write`/`tiff.write`, which write to a `.tmp`
sibling and `replace()` it, so a crash mid-export leaves no half-written map in a
folder a renderer might scan.
"""

from __future__ import annotations

from pathlib import Path

from ..core import png as png_mod
from ..core import tiff as tiff_mod
from ..core.colour import Space, assert_writable
from ..core.errors import ColourSpaceError, LimitError
from ..core.image import Image
from .presets import PACKED_ORDERS

__all__ = ["WRITERS", "write_file"]

#: Container -> the callable that produces it. Both take the interchange type, so
#: a 16-bit value written to PNG and to TIFF is the same integer.
WRITERS = {
    "png": lambda path, image: png_mod.write(path, image),
    "tiff": lambda path, image: tiff_mod.write(path, image),
}


def write_file(path: Path, image: Image, *, kind: str, space: Space | str,
               bit_depth: int, fmt: str, where: str) -> dict[str, object]:
    """Write one map. Returns the record `ctx.physical["packed"]` stores.

    `kind` is a registry kind or a packed order name. The space gate runs for
    both: a packed ORM file has no registry entry of its own, but a packed file
    declared as colour is the same bug as a normal map declared as colour, and it
    gets the same refusal.
    """
    fmt = fmt.lower()
    if fmt not in WRITERS:
        if fmt == "exr":
            raise LimitError(
                f"{where}: refusing to write {kind} as EXR.\n"
                f"  why      EXR needs a half-float encoder (or a float one with "
                f"a bit-exact compressor) that this build does not have; writing "
                f"the file as PNG instead would change the deliverable without "
                f"saying so\n"
                f"  fix      write 16-bit TIFF: same sample depth, no encoder "
                f"needed, and every renderer in PRESETS.md 4 reads it")
        raise LimitError(
            f"{where}: refusing to write {kind} as {fmt.upper()}.\n"
            f"  why      a data map through a lossy codec gains artefacts that "
            f"look like surface detail, and PRESETS.md 3 bans exactly this for "
            f"normals, height, roughness, metallic, AO and packed maps\n"
            f"  fix      use 'png' or 'tiff' in the preset's format field")

    registry_kind = kind not in PACKED_ORDERS
    if registry_kind:
        # The hard gate. Raises before anything is opened.
        assert_writable(kind, space)
    elif not Space(space).is_data:
        raise ColourSpaceError(
            f"refusing to write the packed {kind} map as {Space(space)}.\n"
            f"  expected data (linear PNG)\n"
            f"  why      a packed file is several measurements in colour "
            f"channels. Tagging it sRGB invites the reader to apply the transfer "
            f"function to roughness and occlusion, which changes the numbers "
            f"while leaving the image plausible.\n"
            f"  fix      declare the entry's space as 'data' in the preset")

    if bit_depth not in (8, 16):
        raise LimitError(
            f"{where}: {kind} at {bit_depth}-bit is not something this build "
            f"writes.\n"
            f"  why      the quantisers are 8 and 16-bit unsigned, and a file "
            f"written at a depth nobody asked for is discovered by whoever has to "
            f"rescale it\n"
            f"  fix      set bit to 8 or 16 in the preset")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    interchange = image.to_png(bit_depth)
    WRITERS[fmt](path, interchange)
    return {
        "kind": kind,
        "file": str(path.resolve()),
        "bit_depth": bit_depth,
        "space": Space(space).value,
        "channels": image.channels,
        "width": image.width,
        "height": image.height,
        "format": fmt,
        "bytes": path.stat().st_size,
    }