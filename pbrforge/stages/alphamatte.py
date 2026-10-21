"""`alphamatte`: keep a real alpha channel, or say there is nothing to matte.

A photograph of a wall has no subject to cut out. The old scaffold treated this
as a failure; it is not -- it is the normal case. The stage therefore:

  * passes a decoded opacity channel through (already separated by `ingest`);
  * does **not** invent a matte from luminance, because that turns every dark
    mortar line into a hole;
  * refuses the ML mode by name rather than silently falling back, matching the
    parameter validator.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import box_blur_wrap, clamp01, plane
from ..stages.base import Stage, StageResult

__all__ = ["AlphaMatte", "STAGE"]


class AlphaMatte(Stage):
    name = "alphamatte"
    requires = ("variation",)
    needs = ()
    produces = ("opacity",)
    params_block = "matte"
    cacheable = True
    summary = "pass through a real alpha channel; never invent one"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "matte.enabled = false: any decoded alpha is kept "
                          "untouched")
            return res

        opacity = ctx.maps.get("opacity")
        if opacity is None:
            res.note(ctx, "alphamatte: the source has no alpha channel, so no "
                          "opacity map was written (this is the normal case for "
                          "a photographed material)")
            res.metric(ctx, "matte.mean_opacity", 1.0)
            res.detail["line"] = "no source alpha; no opacity map"
            return res

        values = plane(opacity)
        if params.refinement > 0:
            # Feathering is a real operation, but not the one the parameter
            # names. Run a light blur and say so; the user can see the result.
            radius = max(1, int(params.refinement))
            blurred = box_blur_wrap(values, opacity.width, opacity.height, radius)
            values = [clamp01(v) for v in blurred]
            res.note(ctx, f"alphamatte: source alpha feathered with a {radius}px "
                          f"box; this build does not implement the closed-form "
                          f"refiner, so the raw alpha is the evidence")
        else:
            res.note(ctx, "alphamatte: decoded alpha passed through unchanged")

        ctx.maps["opacity"] = Image(opacity.width, opacity.height, 1, "data",
                                    values)
        mean = sum(values) / len(values)
        ctx.physical["matte"] = {
            "mode": params.mode,
            "mean": round(mean, 4),
            "refinement": int(params.refinement),
            "invented": False,
        }
        res.metric(ctx, "matte.mean_opacity", float(mean))
        res.detail["line"] = (f"source alpha, mean {mean:.3f}"
                              + (f", feathered {params.refinement}px"
                                 if params.refinement else ", unchanged"))
        return res


STAGE = AlphaMatte()
