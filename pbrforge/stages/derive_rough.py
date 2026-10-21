"""`derive_rough`: estimate micro-roughness from relief, cavities and highlights.

A single photo cannot measure roughness the way a gonioreflectometer can. What it
*can* show is the evidence that correlates with roughness:

  * micro-relief: a rough surface has high-frequency height detail;
  * cavities: joints and pits hold dust and scatter light, so they read rougher;
  * specular highlights: a polished or wet spot is smoother than its surroundings.

The stage exposes those as separate algorithms and a `hybrid` default, then
remaps the result into a useful range. It never claims the output is measured --
the manifest records the algorithm, and QC audits the distribution.
"""

from __future__ import annotations

import math

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import (box_blur_wrap, clamp01, luminance_plane, percentile,
                        plane, resize_plane, saturation_plane, value_field)
from ..stages.base import Stage, StageResult

__all__ = ["DeriveRough", "STAGE"]


class DeriveRough(Stage):
    name = "derive_rough"
    requires = ("derive_ao",)
    needs = ("height",)
    produces = ("roughness",)
    params_block = "rough"
    cacheable = True
    summary = "roughness from micro-relief, cavity and specular evidence"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "rough.enabled = false: no roughness map was written")
            return res
        if "height" not in ctx.maps or "albedo" not in ctx.maps:
            raise LimitError(
                "derive_rough needs height and albedo.\n"
                "  why      micro-relief comes from height and highlights from "
                "the albedo; a constant would be a guess, not a derivation\n"
                "  fix      enable height, or disable rough")

        height_image = ctx.maps["height"]
        width, height = height_image.width, height_image.height
        h = plane(height_image)
        detail_norm = [0.5] * (width * height)
        if params.algorithm in ("height", "hybrid"):
            blurred = box_blur_wrap(h, width, height, 1)
            detail = [abs(h[i] - blurred[i]) for i in range(len(h))]
            high = percentile([detail[i] for i in range(0, len(detail), 5)], 0.98)
            if high > 1e-7:
                detail_norm = [clamp01(v / high) for v in detail]
            else:
                res.warn(ctx, "the height field has no micro-detail; the roughness "
                              "variation from relief is flat")

        ao = None
        if params.algorithm in ("cavity", "hybrid"):
            ao_image = ctx.maps.get("ao")
            if ao_image is not None:
                ao = plane(ao_image)
            else:
                res.note(ctx, "rough: AO is absent, so cavity influence is skipped")

        highlight = [0.0] * (width * height)
        if params.algorithm in ("specular", "hybrid"):
            albedo = ctx.maps["albedo"]
            luma = luminance_plane(albedo)
            sat = saturation_plane(albedo)
            for i in range(width * height):
                if luma[i] > 0.68 and sat[i] < 0.22:
                    highlight[i] = clamp01((luma[i] - 0.68) / 0.32) * \
                                   clamp01((0.22 - sat[i]) / 0.22)

        macro = [0.0] * (width * height)
        if params.macro_variation > 0.0:
            mw = max(8, min(32, width // 8 or 8))
            mh = max(8, min(32, height // 8 or 8))
            field = value_field(mw, mh, seed=int(params_seed(ctx)) + 17, octaves=3)
            macro = resize_plane(field, mw, mh, width, height)

        base = float(params.base)
        out = [0.0] * (width * height)
        for i in range(width * height):
            value = base
            if params.algorithm in ("height", "hybrid"):
                value += (detail_norm[i] - 0.5) * params.variation
            if ao is not None:
                value += (1.0 - ao[i]) * params.cavity_influence * 0.35
            if params.algorithm in ("specular", "hybrid"):
                value -= highlight[i] * params.specular_influence * 0.35
            if params.macro_variation > 0.0:
                value += (macro[i] - 0.5) * params.macro_variation
            out[i] = value

        # Remap the measured distribution, not a guessed one. A flat image (or
        # algorithm 'flat') stays flat rather than being stretched into noise.
        ordered = [out[i] for i in range(0, len(out), 3)]
        low = percentile(ordered, 0.02)
        high = percentile(ordered, 0.98)
        target_low, target_high = params.remap
        if high - low < 1e-6:
            out = [clamp01(target_low + (target_high - target_low) * 0.5)
                   for _ in out]
        else:
            span = high - low
            out = [clamp01(target_low + (v - low) / span * (target_high - target_low))
                   for v in out]

        low_clamp, high_clamp = params.clamp
        out = [max(low_clamp, min(high_clamp, v)) for v in out]
        mode = params.output_mode
        if mode == "glossiness":
            out = [1.0 - v for v in out]

        ctx.maps["roughness"] = Image(width, height, 1, "data", out)
        mean = sum(out) / len(out)
        variance = sum((v - mean) ** 2 for v in out) / len(out)
        std = variance ** 0.5
        ctx.physical["roughness"] = {
            "algorithm": params.algorithm,
            "output_mode": mode,
            "mean": round(mean, 4),
            "std": round(std, 4),
            "base": base,
            "remap": list(params.remap),
        }
        ctx.physical["roughness_mode"] = mode
        res.metric(ctx, "rough.mean", float(mean))
        res.metric(ctx, "rough.std", float(std))
        res.metric(ctx, "rough.base", base)
        res.detail["line"] = (f"{params.algorithm} -> {mode}, base {base:.2f}, "
                              f"mean {mean:.3f}, std {std:.3f}")
        res.note(ctx, f"roughness: {params.algorithm} algorithm, {mode} output, "
                      f"clamped {low_clamp:.2f}..{high_clamp:.2f}")
        if std < 0.015:
            res.warn(ctx, "the roughness map is almost uniform (std below 0.015). "
                          "That is honest for a flat material, but if the source "
                          "shows visible wear the height detail is too weak")
        return res


def params_seed(ctx: PipelineContext) -> int:
    return int(getattr(ctx.params, "seed", 1234))


STAGE = DeriveRough()
