"""`derive_height`: the one authoritative relief field.

Everything positional downstream -- normal, AO, cavity, roughness, displacement
-- is derived from this map. That is the rule the whole pipeline is organised
around: a normal map and an AO map derived independently from the same photo
will disagree at every mortar joint, and the render looks wrong in a way that is
hard to attribute.

The height estimate is luminance after delight, percentile-stretched so one
specular pixel cannot flatten the rest, optionally sharpened (``detail_boost``)
and smoothed (``smooth``). It is a *height field*, not a measurement in
millimetres: ``amplitude_mm`` is recorded for displacement and the manifest, and
the pixel values are 0..1 with mid-level 0.5.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import (box_blur_wrap, clamp01, luminance_plane,
                        normalize_plane, plane, resize_image, resize_plane)
from ..stages.base import Stage, StageResult

__all__ = ["DeriveHeight", "STAGE"]


def _cropped_source_luminance(ctx: PipelineContext, width: int,
                              height: int) -> list[float] | None:
    """The original albedo's luminance, cropped to the rectangle seamless chose.

    ``ctx.inputs`` is never moved by seamless -- only ``ctx.maps`` is. Using the
    untouched original after a crop would shift the relief relative to the
    delivered albedo by exactly the crop offset, so the crop is replayed here.
    """
    source = ctx.inputs.get("albedo")
    if source is None:
        return None
    rect = (ctx.physical.get("seamless") or {}).get("rect")
    if rect:
        if isinstance(rect, dict):
            x0, y0 = int(rect["x"]), int(rect["y"])
            cw, ch = int(rect["w"]), int(rect["h"])
        else:
            x0, y0, cw, ch = (int(v) for v in rect)
        if (x0, y0, cw, ch) != (0, 0, source.width, source.height):
            n = source.channels
            data: list[float] = []
            for y in range(y0, min(y0 + ch, source.height)):
                start = (y * source.width + x0) * n
                data.extend(source.data[start:start + cw * n])
            source = Image(cw, ch, n, source.space, data)
    if (source.width, source.height) != (width, height):
        source = resize_image(source, width, height, wrap=True)
    return luminance_plane(source)


class DeriveHeight(Stage):
    name = "derive_height"
    requires = ("delight",)
    needs = ()
    produces = ("height",)
    params_block = "height"
    cacheable = True
    summary = "build the authoritative height field"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "height.enabled = false: no relief map, so normal, AO and "
                          "the height-based roughness path will be skipped")
            return res

        if params.source == "external":
            supplied = ctx.maps.get("height") or ctx.inputs.get("height")
            if supplied is None:
                raise LimitError(
                    "height.source = 'external' but no height map was supplied.\n"
                    "  why      the parameter names a map that is not in the job\n"
                    "  fix      add a 16-bit greyscale height PNG, or use "
                    "height.source = 'delight'")
            if (supplied.width, supplied.height) != (
                    ctx.maps["albedo"].width, ctx.maps["albedo"].height):
                supplied = resize_image(supplied, ctx.maps["albedo"].width,
                                        ctx.maps["albedo"].height, wrap=True)
            ctx.maps["height"] = supplied
            res.note(ctx, "height: using the supplied external height map")
            res.detail["line"] = "supplied external height map"
            return res

        if "albedo" not in ctx.maps:
            raise LimitError(
                "derive_height needs an albedo.\n"
                "  why      the height estimate is the albedo's luminance\n"
                "  fix      run preprocess first")

        image = ctx.maps["albedo"]
        width, height = image.width, image.height
        if params.source == "original":
            values = _cropped_source_luminance(ctx, width, height)
            if values is None:
                values = luminance_plane(image)
                res.note(ctx, "height.source = 'original' but no decoded input "
                              "remained; the working albedo was used instead")
        else:
            values = luminance_plane(image)

        values = normalize_plane(values, 0.005, 0.995)
        if params.invert:
            values = [1.0 - v for v in values]
        if params.detail_boost > 0.0:
            blurred = box_blur_wrap(values, width, height, 2)
            amount = float(params.detail_boost)
            values = [clamp01(values[i] + amount * (values[i] - blurred[i]))
                      for i in range(len(values))]

        if params.smooth > 0.0:
            radius = max(1, int(round(1 + params.smooth * 8)))
            blurred = box_blur_wrap(values, width, height, radius)
            mix = float(params.smooth)
            values = [values[i] * (1.0 - mix) + blurred[i] * mix
                      for i in range(len(values))]

        if abs(float(params.midlevel) - 0.5) > 1e-9:
            shift = float(params.midlevel) - 0.5
            values = [clamp01(v + shift) for v in values]

        ctx.maps["height"] = Image(width, height, 1, "data", values)
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5
        low = min(values)
        high = max(values)
        ctx.physical["amplitude_mm"] = float(params.amplitude_mm)
        ctx.physical["midlevel"] = float(params.midlevel)
        ctx.physical["height"] = {
            "source": params.source,
            "inverted": bool(params.invert),
            "mean": round(mean, 4), "std": round(std, 4),
            "min": round(low, 4), "max": round(high, 4),
            "amplitude_mm": float(params.amplitude_mm),
        }
        res.metric(ctx, "height.mean", float(mean))
        res.metric(ctx, "height.std", float(std))
        res.metric(ctx, "height.range", float(high - low))
        res.detail["line"] = (f"{width}x{height} height from {params.source}"
                              f"{' (inverted)' if params.invert else ''}, "
                              f"mean {mean:.3f}, range {low:.3f}..{high:.3f}")
        res.note(ctx, f"height: {width}x{height} from {params.source}"
                      f"{', polarity inverted' if params.invert else ''}, "
                      f"amplitude {params.amplitude_mm:g} mm")
        if std < 0.015:
            res.warn(ctx, "the height field is almost flat (std below 0.015). Normal "
                          "and AO will be nearly featureless; the photo may be too "
                          "evenly lit or too low-resolution")
        return res


STAGE = DeriveHeight()
