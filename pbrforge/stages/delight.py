"""`delight`: divide out the lighting a photo was taken under.

A photograph of a wall contains two things multiplied together: the material's
albedo and the illumination (a window on one side, a flash hotspot, a vignette).
The height/normal/roughness stages want the material, not the room. This stage
estimates a low-frequency illumination field, divides it out in linear light,
and mixes the correction by ``delight.strength``.

Two constraints keep it honest:

  * the correction happens in linear light, never in sRGB values -- dividing
    gamma-encoded numbers bends the colour;
  * the global mean is restored afterwards, so "delight" cannot double as a
    brightness slider. Any residual low-frequency variation is reported, not
    hidden.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import (box_blur_wrap, clamp01, luminance_plane, plane,
                        resize_plane)
from ..stages.base import Stage, StageResult

__all__ = ["Delight", "STAGE"]

PROXY_EDGE = 64


class Delight(Stage):
    name = "delight"
    requires = ("seamless",)
    needs = ()
    produces = ()
    params_block = "delight"
    cacheable = True
    summary = "flatten the lighting, keep the material"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "delight.enabled = false: the photo's lighting is kept")
            return res
        if "albedo" not in ctx.maps:
            raise LimitError(
                "delight needs an albedo to correct.\n"
                "  why      there is nothing to divide the illumination out of\n"
                "  fix      run preprocess first")
        if params.strength <= 0.0:
            res.note(ctx, "delight.strength = 0: the illumination field was measured "
                          "but not applied")
            return res

        image = ctx.maps["albedo"]
        w, h = image.width, image.height
        linear = image.to_linear()
        lum = luminance_plane(linear)
        mean = sum(lum) / len(lum) if lum else 0.0
        if mean < 1e-6:
            res.warn(ctx, "delight found a black image; nothing was corrected")
            return res

        scale = min(1.0, PROXY_EDGE / max(w, h))
        lw = max(8, int(round(w * scale)))
        lh = max(8, int(round(h * scale)))
        low = resize_plane(lum, w, h, lw, lh)
        low = box_blur_wrap(low, lw, lh, max(1, min(lw, lh) // 12))
        low_mean = sum(low) / len(low)
        if low_mean < 1e-6:
            res.warn(ctx, "delight's illumination estimate was black; nothing was "
                          "corrected")
            return res
        shading = resize_plane(low, lw, lh, w, h)

        strength = float(params.strength)
        source = linear.data
        corrected = [0.0] * len(source)
        shading_values = [0.0] * (w * h)
        for i in range(w * h):
            field = shading[i] / low_mean
            if field < 0.2:
                field = 0.2
            elif field > 5.0:
                field = 5.0
            shading_values[i] = field
            factor = (1.0 / field) ** strength
            o = i * 3
            corrected[o] = source[o] * factor
            corrected[o + 1] = source[o + 1] * factor
            corrected[o + 2] = source[o + 2] * factor

        # Restore the mean: a delight stage that also changes exposure makes every
        # downstream threshold (albedo QC, roughness remap) drift with the light.
        new_mean = sum(corrected) / len(corrected) if corrected else 0.0
        if new_mean > 1e-9:
            gain = (sum(source) / len(source)) / new_mean
            corrected = [v * gain for v in corrected]
        out = Image(w, h, 3, "linear", corrected).to_srgb()
        ctx.maps["albedo"] = out

        if params.export_shading:
            # A normalised illumination field: 0.5 is the mean. It is a working
            # layer, not a surface property, so pack will not ship it.
            ctx.maps["shading"] = Image(
                w, h, 1, "data",
                [clamp01(v * 0.5) for v in shading_values])
        residual = max(abs(v - 1.0) for v in shading_values)
        ctx.physical["delight"] = {
            "strength": strength,
            "residual": round(residual, 4),
            "shading_exported": bool(params.export_shading),
        }
        res.metric(ctx, "delight.strength", strength)
        res.metric(ctx, "delight.residual", float(residual))
        res.detail["line"] = (f"illumination flattened at strength {strength:.2f}; "
                              f"worst residual {residual:.2f}x, mean restored")
        res.note(ctx, f"delight: low-frequency illumination divided out at strength "
                      f"{strength:.2f} (worst residual {residual:.2f}x)")
        if residual > 1.8:
            res.warn(ctx, "the illumination field varies by more than 1.8x across the "
                          "photo. The correction is strong; a photo with less mixed "
                          "lighting will produce a more trustworthy height map")
        return res


STAGE = Delight()
