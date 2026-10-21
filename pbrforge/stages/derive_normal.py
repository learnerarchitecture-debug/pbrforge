"""`derive_normal`: tangent-space normals from the height field.

The gradient comes from a wrap-around Sobel, so the normal map tiles as well as
the height it was built from. The scale is chosen from the field's own 95th
percentile gradient rather than a magic constant: a 2k scan of smooth plaster and
a 512 crop of coarse brick should both produce visible relief without one
flattening and the other turning into broken glass.

OpenGL (+Y) is the default. ``pack`` flips green once, and only when the target
preset explicitly asks for DirectX.
"""

from __future__ import annotations

import math

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import box_blur_wrap, percentile, plane, sobel_wrap, unit_normals
from ..stages.base import Stage, StageResult

__all__ = ["DeriveNormal", "STAGE"]


class DeriveNormal(Stage):
    name = "derive_normal"
    requires = ("derive_height",)
    needs = ("height",)
    produces = ("normal",)
    params_block = "normal"
    cacheable = True
    summary = "Sobel normals from the authoritative height"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "normal.enabled = false: no normal map was written")
            return res
        if "height" not in ctx.maps:
            raise LimitError(
                "derive_normal needs the height map.\n"
                "  why      the normal is the gradient of the one authoritative "
                "height field; deriving it from albedo independently would "
                "misregister every joint\n"
                "  fix      enable height, or supply an external height map")
        height = ctx.maps["height"]
        width, height_px = height.width, height.height
        values = plane(height)

        if params.smooth_before > 0.0:
            radius = max(1, int(round(1 + params.smooth_before * 6)))
            values = box_blur_wrap(values, width, height_px, radius)
            res.note(ctx, f"normal: height pre-smoothed with a {radius}px box "
                          f"(smooth_before {params.smooth_before:.2f})")

        gx, gy = sobel_wrap(values, width, height_px)
        magnitudes = [math.sqrt(gx[i] * gx[i] + gy[i] * gy[i])
                      for i in range(0, width * height_px, 3)]
        p95 = percentile(magnitudes, 0.95)
        if p95 < 1e-7:
            scale = 0.0
            res.warn(ctx, "the height field has no measurable gradient; the normal "
                          "map is flat")
        else:
            # 0.55 is the tangent of roughly 29 degrees at the 95th percentile.
            # Clamped so a nearly flat field does not amplify sensor noise into
            # a plausible-looking but invented surface.
            scale = min(96.0, max(0.0, float(params.strength) * 0.55 / p95))

        normal = unit_normals(gx, gy, width, height_px, strength=scale,
                              clamp_z=float(params.clamp_z),
                              orientation=params.orientation)
        ctx.maps["normal"] = normal

        error = normal.unit_error()
        mean_blue = normal.mean(2)
        # Mean length of the encoded XY vector. This is the honest "how steep is
        # the relief" number; the raw R/G channel means are both ~0.5 by
        # construction and say nothing.
        tangent_total = 0.0
        data = normal.data
        for i in range(width * height_px):
            nx = data[i * 3] * 2.0 - 1.0
            ny = data[i * 3 + 1] * 2.0 - 1.0
            tangent_total += (nx * nx + ny * ny) ** 0.5
        xy_mean = tangent_total / (width * height_px)
        ctx.physical["normal"] = {
            "orientation": params.orientation,
            "scale": round(scale, 4),
            "p95_gradient": round(p95, 6),
            "unit_error": round(error, 6),
            "xy_mean": round(xy_mean, 6),
        }
        res.metric(ctx, "normal.unit_error", float(error))
        res.metric(ctx, "normal.scale", float(scale))
        res.metric(ctx, "normal.mean_blue", float(mean_blue))
        res.metric(ctx, "normal.xy_mean", float(xy_mean))
        res.detail["line"] = (f"{params.orientation}, Sobel from height, "
                              f"scale {scale:.2f}, unit error {error:.4f}")
        res.note(ctx, f"normal: {params.orientation} tangent space, Sobel from "
                      f"height, gradient p95 {p95:.5f}")
        if error > 0.01:
            res.warn(ctx, "the encoded normal is not unit length; this is a bug in "
                          "the encoder, not a property of the material")
        return res


STAGE = DeriveNormal()
