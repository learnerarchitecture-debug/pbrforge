"""`variation`: break the repeat without breaking the tile.

There are two different jobs hiding under the word "variation":

  * variation *inside* the tile, which must wrap -- handled by the macro field's
    low frequency but deliberately **not** made periodic, because a periodic
    macro layer would add a second, larger repeat;
  * variation *across* a wall, which no tiling texture can contain. That is the
    documented use of the exported `macro` map: a renderer multiplies it in at a
    scale larger than the tile.

The albedo and roughness nudges here are subtle by construction. If you can
clearly see the macro map in the albedo, the strength is wrong.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import clamp01, plane, resize_plane, value_field
from ..stages.base import Stage, StageResult

__all__ = ["Variation", "STAGE"]


class Variation(Stage):
    name = "variation"
    requires = ("derive_metal",)
    needs = ("albedo",)
    produces = ("macro",)
    params_block = "variation"
    cacheable = True
    summary = "subtle macro variation and a non-tiling breakup map"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "variation.enabled = false: the tile keeps its exact "
                          "repeat and no macro map was written")
            return res
        if "albedo" not in ctx.maps:
            raise LimitError(
                "variation needs an albedo.\n"
                "  why      the macro field is applied to the base colour and "
                "roughness, and it must register with both\n"
                "  fix      run preprocess first")

        albedo = ctx.maps["albedo"]
        width, height = albedo.width, albedo.height
        mw = max(12, min(48, width // 10 or 12))
        mh = max(12, min(48, height // 10 or 12))
        field = value_field(mw, mh, seed=int(ctx.params.seed) + 101, octaves=3)
        macro = resize_plane(field, mw, mh, width, height)

        strength = float(params.macro_strength)
        hue = float(params.macro_hue_shift)
        data = list(albedo.data)
        tints = (-0.5, 0.2, 0.6)
        for i in range(width * height):
            centred = (macro[i] - 0.5) * 2.0
            gain = 1.0 + centred * strength * 0.18
            o = i * 3
            if params.preserve_rgb:
                data[o] = clamp01(data[o] * gain)
                data[o + 1] = clamp01(data[o + 1] * gain)
                data[o + 2] = clamp01(data[o + 2] * gain)
            else:
                data[o] = clamp01(data[o] * gain * (1.0 + centred * hue * tints[0]))
                data[o + 1] = clamp01(data[o + 1] * gain * (1.0 + centred * hue * tints[1]))
                data[o + 2] = clamp01(data[o + 2] * gain * (1.0 + centred * hue * tints[2]))
        ctx.maps["albedo"] = Image(width, height, 3, "srgb", data)

        rough = ctx.maps.get("roughness")
        if rough is not None and params.roughness_nudge > 0.0:
            mode = (ctx.physical.get("roughness") or {}).get("output_mode", "roughness")
            sign = -1.0 if mode == "glossiness" else 1.0
            values = plane(rough)
            nudge = float(params.roughness_nudge) * 0.10 * sign
            values = [clamp01(v + (macro[i] - 0.5) * 2.0 * nudge)
                      for i, v in enumerate(values)]
            ctx.maps["roughness"] = Image(width, height, 1, "data", values)

        if params.output_macro_map:
            ctx.maps["macro"] = Image(width, height, 1, "data", macro)

        mean = sum(macro) / len(macro)
        variance = sum((v - mean) ** 2 for v in macro) / len(macro)
        std = variance ** 0.5
        ctx.physical["variation"] = {
            "macro_std": round(std, 4),
            "macro_strength": strength,
            "tileable": False,
            "note": "macro is intentionally non-tiling; use it at a scale larger "
                    "than the tile",
        }
        res.metric(ctx, "variation.macro_std", float(std))
        res.metric(ctx, "variation.macro_strength", strength)
        res.detail["line"] = (f"macro field {mw}x{mh} -> {width}x{height}, "
                              f"std {std:.3f}, albedo nudge +/-"
                              f"{strength * 18.0:.1f}%")
        res.note(ctx, f"variation: non-tiling macro field at strength {strength:.2f}; "
                      f"albedo brightness nudged by up to +/-{strength * 18.0:.1f}%")
        return res


STAGE = Variation()
