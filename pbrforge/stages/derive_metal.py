"""`derive_metal`: decide the metallic channel, and refuse to guess.

A single photograph under unknown illumination does **not** contain enough
information to separate a metal from a dielectric with confidence. The old
version of this pipeline pretended otherwise; this one does not.

  * ``zero`` / ``one`` are explicit user decisions and are honoured;
  * ``masked`` reads the mask the user painted, and is the only large-area
    method that is actually evidence;
  * ``classify`` returns 0 and reports confidence 0, because coating every photo
    with an invented metal mask is exactly the kind of plausible-looking lie the
    rest of this project refuses.

The UI exposes this as an Auto / Dielectric / Metallic choice so the decision is
visible rather than buried in a defaults file.
"""

from __future__ import annotations

from pathlib import Path

from ..core import png as png_mod
from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import clamp01, luminance_plane, plane, resize_plane
from ..stages.base import Stage, StageResult

__all__ = ["DeriveMetal", "STAGE"]


def _read_mask(path: Path, width: int, height: int) -> list[float]:
    try:
        mask = png_mod.read(path)
    except png_mod.PngError as exc:
        raise LimitError(
            f"metal.mask could not be read: {path}.\n"
            f"  why      {exc}\n"
            f"  fix      use a non-interlaced greyscale PNG; 0 = dielectric, "
            f"1 = metal")
    image = Image.from_png(mask, "data")
    if image.channels == 1:
        values = plane(image)
    else:
        values = luminance_plane(image)
    if (image.width, image.height) != (width, height):
        values = resize_plane(values, image.width, image.height, width, height)
    return [clamp01(v) for v in values]


class DeriveMetal(Stage):
    name = "derive_metal"
    requires = ("derive_rough",)
    needs = ()
    produces = ("metallic",)
    params_block = "metal"
    cacheable = True
    summary = "metallic channel: explicit decision or painted mask"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "metal.enabled = false: no metallic map was written")
            return res
        if "albedo" not in ctx.maps:
            raise LimitError(
                "derive_metal needs the albedo's dimensions.\n"
                "  why      the metallic map has to register with the base colour\n"
                "  fix      run preprocess first")
        width = ctx.maps["albedo"].width
        height = ctx.maps["albedo"].height

        confidence = 0.0
        note = ""
        if params.mode == "masked":
            raw = params.mask or ""
            path = Path(raw)
            if not path.is_absolute():
                path = Path(ctx.work) / path
            if not path.is_file():
                raise LimitError(
                    f"metal.mask does not exist: {path}.\n"
                    f"  why      mode 'masked' needs the painted mask file\n"
                    f"  fix      pass an absolute path, or place the mask in the "
                    f"job folder")
            values = _read_mask(path, width, height)
            confidence = 1.0
            note = f"metallic: painted mask {path.name}"
        elif params.mode == "one":
            values = [1.0] * (width * height)
            confidence = 1.0
            note = "metallic: forced to 1.0 by the user"
        elif params.mode == "zero":
            values = [0.0] * (width * height)
            confidence = 1.0
            note = "metallic: forced to 0.0 by the user"
        else:
            # classify: no evidence, no invention.
            values = [0.0] * (width * height)
            confidence = 0.0
            note = ("metallic: no reliable metal/dielectric evidence in a single "
                    "photo; the map is 0.0 (dielectric). Paint a mask or choose "
                    "Metallic explicitly if the material is metal")
            res.warn(ctx, note)

        ctx.maps["metallic"] = Image(width, height, 1, "data", values)
        mean = sum(values) / len(values)
        ctx.physical["metal"] = {
            "mode": params.mode,
            "mean": round(mean, 4),
            "confidence": round(confidence, 4),
            "explicit": params.mode in ("zero", "one", "masked"),
        }
        res.metric(ctx, "metal.mean", float(mean))
        res.metric(ctx, "metal.confidence", float(confidence))
        res.detail["line"] = (f"{params.mode}, mean {mean:.3f}, "
                              f"confidence {confidence:.2f}")
        res.note(ctx, note)
        return res


STAGE = DeriveMetal()
