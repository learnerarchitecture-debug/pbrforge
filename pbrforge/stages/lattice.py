"""`lattice`: measure the repeat unit, so `seamless` can crop on it exactly.

The detector is deliberately a one-dimensional autocorrelation scan per axis at
a proxy resolution, not a 2D FFT. For the material this tool is built for --
brick, tile, plank, paving -- the repeat is strong along both axes and an
autocorrelation minimum finds it in a few milliseconds. An FFT would be more
general and would also be unusable in a dependency-free build at 4k.

Confidence is comparative: the best period's mean absolute difference is
compared against the typical difference at non-periodic offsets. A flat or
aperiodic image therefore reports low confidence instead of inventing a grid,
and `seamless` falls through to its offset/heal branch.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.ops import clamp01, plane, resize_plane
from ..stages.base import Stage, StageResult

__all__ = ["Lattice", "STAGE"]

PROXY_EDGE = 192
MIN_PERIOD = 4
MAX_LINES = 48
TRUST = 0.75


def _line_scores(values: list[float], width: int, height: int,
                 horizontal: bool, periods: range) -> list[float]:
    """Mean |difference| at each candidate period, sampled over 48 lines."""
    length = width if horizontal else height
    count = height if horizontal else width
    lines = count if count <= MAX_LINES else max(2, count // max(1, count // MAX_LINES))
    step = max(1, count // MAX_LINES)
    scores = []
    for period in periods:
        total = 0.0
        pairs = 0
        for line in range(0, count, step):
            if horizontal:
                base = line * width
                for x in range(0, length - period):
                    total += abs(values[base + x + period] - values[base + x])
                    pairs += 1
            else:
                for y in range(0, length - period):
                    total += abs(values[(y + period) * width + line]
                                 - values[y * width + line])
                    pairs += 1
        scores.append(total / pairs if pairs else 0.0)
    return scores


def _measure(values: list[float], width: int, height: int, horizontal: bool
             ) -> tuple[float, float, float]:
    """(period, confidence, ratio) for one axis at proxy resolution."""
    length = width if horizontal else height
    max_period = min(length // 2, 128)
    if max_period < MIN_PERIOD:
        return 1.0, 0.0, 1.0
    periods = range(MIN_PERIOD, max_period + 1)
    scores = _line_scores(values, width, height, horizontal, periods)
    if not scores:
        return 1.0, 0.0, 1.0
    ordered = sorted(scores)
    baseline = ordered[len(ordered) // 2]
    best_index = min(range(len(scores)), key=scores.__getitem__)
    best = scores[best_index]
    period = periods[best_index]
    if baseline < 1e-9:
        return float(period), 0.0, 1.0
    ratio = best / baseline
    # ratio 0.45 or better is a strong repeat; 0.72 is noise.
    confidence = clamp01((0.72 - ratio) / 0.27) * 0.98
    return float(period), confidence, ratio


class Lattice(Stage):
    name = "lattice"
    requires = ("preprocess",)
    needs = ()
    produces = ()
    params_block = "lattice"
    cacheable = True
    summary = "detect the repeat unit (autocorrelation per axis)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "lattice.enabled = false: no period measurement will be "
                          "offered to seamless")
            return res
        if "albedo" not in ctx.maps:
            raise LimitError(
                "lattice needs an albedo in the working set.\n"
                "  why      it measures the repeat on the material's own luminance\n"
                "  fix      run preprocess first")

        image = ctx.maps["albedo"]
        width, height = image.width, image.height
        proxy = image.luminance()
        scale = min(1.0, PROXY_EDGE / max(width, height))
        if scale < 1.0:
            pw = max(16, int(round(width * scale)))
            ph = max(16, int(round(height * scale)))
            values = resize_plane(proxy.plane(0), width, height, pw, ph)
        else:
            pw, ph = width, height
            values = proxy.plane(0)

        px, cx, rx = _measure(values, pw, ph, horizontal=True)
        py, cy, ry = _measure(values, pw, ph, horizontal=False)
        confidence = min(cx, cy)

        # Convert to full-resolution periods. The proxy measurement is a float;
        # a full-resolution period has to be an integer to crop on.
        period_x = max(1, int(round(px * width / pw)))
        period_y = max(1, int(round(py * height / ph)))
        min_p, max_p = int(params.max_periods[0]), int(params.max_periods[1])
        periods_ok = (min_p <= width / period_x <= max_p
                      and min_p <= height / period_y <= max_p)
        snapped = bool(params.snap and confidence >= TRUST and periods_ok)

        rect = {"x": 0, "y": 0, "w": width, "h": height}
        if snapped:
            nx = max(1, int(round(width / period_x)))
            ny = max(1, int(round(height / period_y)))
            cw = min(width, nx * period_x)
            ch = min(height, ny * period_y)
            # An exact crop that throws away more than half the photo is not a
            # win; keep the period but do not crop on it.
            if cw * ch >= 0.45 * width * height:
                rect = {"x": (width - cw) // 2, "y": (height - ch) // 2,
                        "w": cw, "h": ch}
            else:
                snapped = False

        ctx.physical["lattice"] = {
            "period_x": float(period_x),
            "period_y": float(period_y),
            "confidence": round(float(confidence), 4),
            "ratio_x": round(float(rx), 4),
            "ratio_y": round(float(ry), 4),
            "snapped": snapped,
            "rect": rect,
            "method": "autocorrelation",
            "proxy": [pw, ph],
        }
        res.metric(ctx, "lattice.period_x", float(period_x))
        res.metric(ctx, "lattice.period_y", float(period_y))
        res.metric(ctx, "lattice.confidence", float(confidence))
        kind = "exact crop available" if snapped else "period only (below trust)"
        res.detail["line"] = (f"{width / period_x:.1f} x {height / period_y:.1f} "
                              f"repeats, period {period_x}x{period_y}px, "
                              f"confidence {confidence:.2f} -- {kind}")
        res.note(ctx, f"lattice: period {period_x}x{period_y}px at confidence "
                      f"{confidence:.2f} ({kind})")
        return res


STAGE = Lattice()
