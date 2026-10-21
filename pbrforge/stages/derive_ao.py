"""`derive_ao`: ambient occlusion and cavity from the height field.

Occlusion is measured the only way a single height field can support: how far a
pixel sits below its neighbourhood at several radii. Concave joints and pits go
dark; broad flat faces stay white. Doing this at a proxy resolution is not a
shortcut for speed alone -- it also suppresses single-pixel noise that would
otherwise read as tiny pits everywhere.

The delivered AO is 8-bit DATA, 1 channel. Cavity and curvature are optional
working maps: they are useful in a shader and are never packed as if they were
base colour.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import box_blur_wrap, clamp01, plane, resize_plane
from ..stages.base import Stage, StageResult

__all__ = ["DeriveAO", "STAGE"]

PROXY_EDGE = 384


class DeriveAO(Stage):
    name = "derive_ao"
    requires = ("derive_normal",)
    needs = ("height",)
    produces = ("ao",)
    params_block = "ao"
    cacheable = True
    summary = "multi-scale occlusion and cavity from height"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "ao.enabled = false: no occlusion map was written")
            return res
        if "height" not in ctx.maps:
            raise LimitError(
                "derive_ao needs the height map.\n"
                "  why      occlusion is a property of the one height field\n"
                "  fix      enable height, or supply an external height map")

        image = ctx.maps["height"]
        width, height = image.width, image.height
        scale = min(1.0, PROXY_EDGE / max(width, height))
        pw = max(16, int(round(width * scale)))
        ph = max(16, int(round(height * scale)))
        values = resize_plane(plane(image), width, height, pw, ph)

        radius = max(1, int(round(params.radius_px * pw / max(1, width))))
        # Occlusion is a *local* concavity. A brick that is simply darker than
        # its neighbour is not a pit, and using the raw height made every dark
        # brick read as one. Removing the low-frequency component before the
        # multi-scale comparison keeps real mortar joints and discards that
        # whole-brick brightness difference.
        baseline = box_blur_wrap(values, pw, ph, max(2, radius * 4))
        centred = [values[i] - baseline[i] for i in range(len(values))]
        near = box_blur_wrap(centred, pw, ph, max(1, radius))
        mid = box_blur_wrap(centred, pw, ph, max(2, radius * 2))
        far = box_blur_wrap(centred, pw, ph, max(3, radius * 4))

        strength = float(params.strength)
        ao_small = [0.0] * (pw * ph)
        cavity_small = [0.0] * (pw * ph)
        for i in range(pw * ph):
            h = centred[i]
            occ = (max(0.0, near[i] - h) * 9.0
                   + max(0.0, mid[i] - h) * 4.0
                   + max(0.0, far[i] - h) * 2.0)
            ao = clamp01(1.0 - strength * occ)
            ao_small[i] = ao
            cavity_small[i] = clamp01(1.0 - occ)

        ao = resize_plane(ao_small, pw, ph, width, height)
        if params.invert:
            ao = [1.0 - v for v in ao]
        ctx.maps["ao"] = Image(width, height, 1, "data", ao)

        if params.export_cavity:
            cavity = resize_plane(cavity_small, pw, ph, width, height)
            ctx.maps["cavity"] = Image(width, height, 1, "data", cavity)

        if params.export_curvature:
            # 5-point Laplacian on the proxy, then 0.5-centred and gained. The
            # gain is the parameter because curvature's useful range depends on
            # the source's contrast.
            curvature = [0.0] * (pw * ph)
            gain = float(params.curvature_gain)
            for y in range(ph):
                up = ((y - 1) % ph) * pw
                row = y * pw
                down = ((y + 1) % ph) * pw
                for x in range(pw):
                    left = (x - 1) % pw
                    right = (x + 1) % pw
                    lap = (values[up + x] + values[down + x]
                           + values[row + left] + values[row + right]
                           - 4.0 * values[row + x])
                    curvature[row + x] = clamp01(0.5 + lap * gain)
            curvature = resize_plane(curvature, pw, ph, width, height)
            ctx.maps["curvature"] = Image(width, height, 1, "data", curvature)

        mean = sum(ao) / len(ao)
        low = min(ao)
        ctx.physical["ao"] = {
            "strength": strength, "radius_px": int(params.radius_px),
            "multiscale": bool(params.multiscale),
            "mean": round(mean, 4), "min": round(low, 4),
            "proxy": [pw, ph],
        }
        res.metric(ctx, "ao.mean", float(mean))
        res.metric(ctx, "ao.min", float(low))
        res.metric(ctx, "ao.radius_px", float(params.radius_px))
        res.detail["line"] = (f"multi-scale AO at {params.radius_px}px radius, "
                              f"mean {mean:.3f}, min {low:.3f}")
        res.note(ctx, f"ao: {params.radius_px}px radius, strength "
                      f"{strength:.2f}, proxy {pw}x{ph}")
        if mean > 0.97:
            res.warn(ctx, "the AO map is almost entirely white: the material has "
                          "little measurable concavity at this radius")
        return res


STAGE = DeriveAO()
