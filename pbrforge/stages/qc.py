"""`qc`: the audit that runs after packing, and is allowed to say "no".

A pipeline that marks its own homework is worse than no QC. This stage reads the
delivered maps, recomputes the seam metric, measures clipping and distribution
health, writes ``qc_report.json`` next to the manifest, and updates the manifest's
verdict. It cannot repair anything -- by this point the files exist -- and that is
the point.

The thresholds are parameters (``qc.*``), so an unusual material can loosen them
deliberately instead of quietly editing the report.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.ops import luminance_plane, plane
from ..core.synth import ratio_to_score, seam_ratio_grey
from ..export import manifest as manifest_mod
from ..stages.base import Stage, StageResult

__all__ = ["QC", "STAGE"]


def _distribution(values: list[float]) -> tuple[float, float, float]:
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    return mean, std, min(values)


def _entropy(values: list[float], bins: int = 32) -> float:
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int(value * bins)))
        counts[index] += 1
    total = len(values)
    entropy = 0.0
    for count in counts:
        if count:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


class QC(Stage):
    name = "qc"
    requires = ("pack",)
    needs = ()
    produces = ()
    params_block = "qc"
    cacheable = False
    summary = "audit the delivered maps and write the verdict"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "qc.enabled = false: no verdict was computed")
            return res
        if "albedo" not in ctx.maps:
            raise LimitError(
                "qc has no albedo to audit.\n"
                "  why      the pipeline produced no delivered base colour\n"
                "  fix      check the failed stage in the run report")

        measured: dict[str, float] = {}
        reasons: list[str] = []

        # ---- seam ----------------------------------------------------------
        height = ctx.maps.get("height")
        if height is not None:
            seam_values = plane(height)
            seam_width, seam_height = height.width, height.height
        else:
            albedo = ctx.maps["albedo"]
            seam_values = luminance_plane(albedo)
            seam_width, seam_height = albedo.width, albedo.height
        ratio = seam_ratio_grey(seam_values, seam_width, seam_height)
        seam_score = ratio_to_score(ratio)
        measured["seam_ratio"] = round(float(ratio), 4)
        measured["seam_score"] = float(seam_score)

        # ---- albedo --------------------------------------------------------
        albedo = ctx.maps["albedo"]
        luma = luminance_plane(albedo)
        mean_luma, std_luma, min_luma = _distribution(luma)
        black = sum(1 for v in luma if v < 0.01) / len(luma)
        white = sum(1 for v in luma if v > 0.99) / len(luma)
        measured["albedo_mean"] = round(mean_luma, 4)
        measured["albedo_std"] = round(std_luma, 4)
        measured["albedo_clip_black"] = round(black, 6)
        measured["albedo_clip_white"] = round(white, 6)

        # ---- normal --------------------------------------------------------
        normal = ctx.maps.get("normal")
        if normal is not None:
            measured["normal_unit_error"] = round(normal.unit_error(), 6)
            measured["normal_blue_mean"] = round(normal.mean(2), 4)
            tangent_total = 0.0
            data = normal.data
            for i in range(normal.width * normal.height):
                nx = data[i * 3] * 2.0 - 1.0
                ny = data[i * 3 + 1] * 2.0 - 1.0
                tangent_total += (nx * nx + ny * ny) ** 0.5
            measured["normal_xy_mean"] = round(
                tangent_total / (normal.width * normal.height), 4)

        # ---- roughness -----------------------------------------------------
        rough = ctx.maps.get("roughness")
        if rough is not None:
            rough_values = plane(rough)
            if (ctx.physical.get("roughness") or {}).get("output_mode") == "glossiness":
                rough_values = [1.0 - v for v in rough_values]
            r_mean, r_std, r_min = _distribution(rough_values)
            measured["rough_mean"] = round(r_mean, 4)
            measured["rough_std"] = round(r_std, 4)
            measured["rough_min"] = round(r_min, 4)
            measured["rough_entropy"] = round(_entropy(rough_values), 4)

        # ---- verdict -------------------------------------------------------
        if seam_score < params.seam_fail:
            reasons.append(f"seam score {seam_score} is below the fail floor "
                           f"{params.seam_fail}")
        elif seam_score < params.seam_warn:
            reasons.append(f"seam score {seam_score} is below the warn threshold "
                           f"{params.seam_warn}")
        if not (params.albedo_mean_fail[0] <= mean_luma <= params.albedo_mean_fail[1]):
            reasons.append(f"albedo mean {mean_luma:.2f} is outside the expected "
                           f"{params.albedo_mean_fail[0]:.2f}.."
                           f"{params.albedo_mean_fail[1]:.2f}")
        if black > params.clip_black_warn:
            reasons.append(f"{black * 100:.2f}% of albedo pixels are crushed black "
                           f"(warn above {params.clip_black_warn * 100:.2f}%)")
        if white > params.clip_white_warn:
            reasons.append(f"{white * 100:.3f}% of albedo pixels are clipped white "
                           f"(warn above {params.clip_white_warn * 100:.3f}%)")
        if normal is not None:
            if measured["normal_unit_error"] > params.normal_unit_max:
                reasons.append(f"normal unit error "
                               f"{measured['normal_unit_error']:.4f} is above "
                               f"{params.normal_unit_max}")
            if measured["normal_blue_mean"] < params.normal_blue_min:
                reasons.append(f"normal mean blue "
                               f"{measured['normal_blue_mean']:.3f} is below "
                               f"{params.normal_blue_min}; the map may be in the "
                               f"wrong colour space or orientation")
            if measured["normal_xy_mean"] > params.normal_xy_max:
                reasons.append(f"normal XY mean {measured['normal_xy_mean']:.3f} "
                               f"is above {params.normal_xy_max}; the relief is "
                               f"very steep")
        if rough is not None:
            if measured["rough_std"] < params.rough_std_warn:
                reasons.append(f"roughness std {measured['rough_std']:.4f} is below "
                               f"{params.rough_std_warn}; the map is nearly uniform")
            if not (params.rough_mean_warn[0] <= measured["rough_mean"]
                    <= params.rough_mean_warn[1]):
                reasons.append(f"roughness mean {measured['rough_mean']:.2f} is "
                               f"outside {params.rough_mean_warn[0]:.2f}.."
                               f"{params.rough_mean_warn[1]:.2f}")

        fail_floor = (seam_score < params.seam_fail
                      or not (params.albedo_mean_fail[0] <= mean_luma
                              <= params.albedo_mean_fail[1]))
        verdict = "fail" if fail_floor else ("warn" if reasons else "pass")

        thresholds = {
            "seam_warn": params.seam_warn, "seam_fail": params.seam_fail,
            "albedo_mean_fail": list(params.albedo_mean_fail),
            "clip_black_warn": params.clip_black_warn,
            "clip_white_warn": params.clip_white_warn,
            "normal_blue_min": params.normal_blue_min,
            "normal_xy_max": params.normal_xy_max,
            "normal_unit_max": params.normal_unit_max,
            "rough_std_warn": params.rough_std_warn,
            "rough_mean_warn": list(params.rough_mean_warn),
        }
        report = {
            "verdict": verdict,
            "reasons": reasons,
            "measured": measured,
            "thresholds": thresholds,
            "stage": "qc",
            "version": ctx.physical.get("version", ""),
        }

        export_root = Path(ctx.work) / "export"
        if not export_root.is_dir():
            export_root = Path(ctx.work)
        report_path = export_root / "qc_report.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8", newline="\n")
        manifest_path = export_root / "material.json"
        ctx.physical["qc"] = {
            "verdict": verdict,
            "reasons": reasons,
            "report": "qc_report.json",
            "thresholds": thresholds,
            "measured": measured,
        }
        updated = manifest_mod.update_qc(manifest_path, ctx)
        if not updated:
            res.warn(ctx, "qc wrote its report but could not update material.json "
                          "(the manifest was missing). The verdict is in "
                          "qc_report.json")

        ctx.notes.append(f"qc: {verdict.upper()}" +
                         (" -- " + "; ".join(reasons) if reasons else ""))
        res.metric(ctx, "qc.seam_score", float(seam_score))
        res.metric(ctx, "qc.seam_ratio", float(ratio))
        res.metric(ctx, "qc.albedo_mean", float(mean_luma))
        res.metric(ctx, "qc.verdict_pass", 0.0 if verdict == "fail" else 1.0)
        if "rough_entropy" in measured:
            res.metric(ctx, "qc.rough_entropy", float(measured["rough_entropy"]))
        res.detail["line"] = (f"{verdict.upper()}, seam {seam_score}/100, "
                              f"albedo {mean_luma:.2f}"
                              + (f" -- {reasons[0]}" if reasons else ""))
        res.note(ctx, f"qc verdict: {verdict}" +
                      (f" ({len(reasons)} reason(s))" if reasons else ""))
        return res


STAGE = QC()
