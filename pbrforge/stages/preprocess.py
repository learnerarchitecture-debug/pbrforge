"""`preprocess`: turn a photograph into a clean, evenly lit working albedo.

This is the only stage that is allowed to touch colour before the derive stages,
and every operation it performs is recorded. The defaults are deliberately
conservative: a photo that is already neutral should pass through unchanged.

Implemented here: grey-world/white-patch balance, percentile levels, content and
square crops, max-edge resize, a light denoise/de-JPEG blend, and a radial
vignette correction. Rectification is refused by name -- a homography that is
subtly wrong is worse than no rectification, and the UI does not offer corners
yet.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import (box_blur_wrap, clamp01, percentile, plane, resize_image)
from ..stages.base import Stage, StageResult

__all__ = ["Preprocess", "STAGE"]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _crop(image: Image, x0: int, y0: int, width: int, height: int) -> Image:
    if (x0, y0, width, height) == (0, 0, image.width, image.height):
        return image.copy()
    n = image.channels
    data: list[float] = []
    for y in range(y0, y0 + height):
        start = (y * image.width + x0) * n
        data.extend(image.data[start:start + width * n])
    return Image(width, height, n, image.space, data)


def _content_box(image: Image) -> tuple[int, int, int, int]:
    """Trim near-uniform borders. A wall photographed against a backdrop has one."""
    w, h = image.width, image.height
    lum = image.luminance().plane(0)
    global_mean = _mean(lum)
    variance = sum((v - global_mean) ** 2 for v in lum) / len(lum)
    std = variance ** 0.5
    if std < 1e-6:
        return 0, 0, w, h
    threshold = max(0.008, std * 0.18)

    def row_std(y: int) -> float:
        base = y * w
        mean = sum(lum[base:base + w]) / w
        return (sum((v - mean) ** 2 for v in lum[base:base + w]) / w) ** 0.5

    def col_std(x: int) -> float:
        vals = [lum[y * w + x] for y in range(h)]
        mean = sum(vals) / h
        return (sum((v - mean) ** 2 for v in vals) / h) ** 0.5

    top = 0
    limit = h // 5
    while top < limit and row_std(top) < threshold:
        top += 1
    bottom = h - 1
    while bottom > h - 1 - limit and row_std(bottom) < threshold:
        bottom -= 1
    left = 0
    limit = w // 5
    while left < limit and col_std(left) < threshold:
        left += 1
    right = w - 1
    while right > w - 1 - limit and col_std(right) < threshold:
        right -= 1
    if right - left < 16 or bottom - top < 16:
        return 0, 0, w, h
    return left, top, right - left + 1, bottom - top + 1


class Preprocess(Stage):
    name = "preprocess"
    requires = ("ingest",)
    needs = ()
    produces = ()
    params_block = "preprocess"
    cacheable = True
    summary = "white balance, levels, crop, resize and light denoise"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "preprocess.enabled = false: the decoded source is used as-is")
            return res

        if "albedo" not in ctx.inputs:
            raise LimitError(
                "preprocess has no decoded albedo to work on.\n"
                "  why      ingest did not produce one\n"
                "  fix      run the pipeline from ingest with a source image")
        if params.rectify is not None:
            raise LimitError(
                "preprocess.rectify is set, and this build does not implement "
                "rectification.\n"
                "  why      a subtly wrong homography distorts every derived map, "
                "and the UI has no corner picker to verify one\n"
                "  fix      set rectify = null and crop the photo in an image editor, "
                "or correct perspective before uploading")

        image = ctx.inputs["albedo"].copy()
        operations: list[str] = []

        # ---- crop -----------------------------------------------------------
        if params.crop == "square":
            side = min(image.width, image.height)
            x0 = (image.width - side) // 2
            y0 = (image.height - side) // 2
            image = _crop(image, x0, y0, side, side)
            operations.append(f"centred square crop {side}x{side}")
        elif params.crop == "content":
            x0, y0, cw, ch = _content_box(image)
            image = _crop(image, x0, y0, cw, ch)
            if (cw, ch) != (image.width, image.height) or x0 or y0:
                operations.append(f"content crop {cw}x{ch} at {x0},{y0}")
        elif params.crop != "none":
            raise LimitError(f"unknown preprocess.crop {params.crop!r}")

        # ---- white balance --------------------------------------------------
        if params.white_balance != "none":
            means = []
            for channel in range(3):
                values = plane(image, channel)
                if params.white_balance == "white_patch":
                    means.append(max(0.01, percentile(values, 0.95)))
                else:
                    means.append(max(0.01, _mean(values)))
            target = sum(means) / 3.0
            gains = [target / m for m in means]
            data = list(image.data)
            for i in range(image.width * image.height):
                o = i * 3
                data[o] = clamp01(data[o] * gains[0])
                data[o + 1] = clamp01(data[o + 1] * gains[1])
                data[o + 2] = clamp01(data[o + 2] * gains[2])
            image = Image(image.width, image.height, 3, "srgb", data)
            operations.append(f"{params.white_balance} balance "
                              f"(gains {gains[0]:.2f}/{gains[1]:.2f}/{gains[2]:.2f})")

        # ---- levels ---------------------------------------------------------
        low, high = float(params.levels[0]), float(params.levels[1])
        do_levels = params.auto_levels or low > 0.0 or high < 1.0
        if do_levels:
            lum = image.luminance().plane(0)
            if params.auto_levels:
                low = max(0.0, percentile(lum, 0.005))
                high = min(1.0, percentile(lum, 0.995))
            if high - low < 1e-6:
                res.warn(ctx, "auto levels found a flat image; levels were skipped")
            else:
                data = list(image.data)
                span = high - low
                for i in range(len(data)):
                    data[i] = clamp01((data[i] - low) / span)
                image = Image(image.width, image.height, 3, "srgb", data)
                operations.append(f"levels {low:.3f}..{high:.3f}")

        # ---- denoise / de-JPEG ---------------------------------------------
        amount = max(float(params.denoise), float(params.dejpeg) * 0.6)
        if amount > 0.0:
            w, h = image.width, image.height
            planes = [box_blur_wrap(plane(image, c), w, h, 1) for c in range(3)]
            data = list(image.data)
            for c in range(3):
                blurred = planes[c]
                for i in range(w * h):
                    o = i * 3 + c
                    data[o] = clamp01(data[o] * (1.0 - amount)
                                      + blurred[i] * amount)
            image = Image(w, h, 3, "srgb", data)
            operations.append(f"denoise/de-JPEG blend {amount:.2f}")

        # ---- vignette -------------------------------------------------------
        if params.vignette_correct > 0.0:
            strength = float(params.vignette_correct)
            w, h = image.width, image.height
            cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
            data = list(image.data)
            for y in range(h):
                ny = (y - cy) / max(1.0, cy)
                for x in range(w):
                    nx = (x - cx) / max(1.0, cx)
                    gain = 1.0 + strength * 0.45 * (nx * nx + ny * ny)
                    o = (y * w + x) * 3
                    data[o] = clamp01(data[o] * gain)
                    data[o + 1] = clamp01(data[o + 1] * gain)
                    data[o + 2] = clamp01(data[o + 2] * gain)
            image = Image(w, h, 3, "srgb", data)
            operations.append(f"radial vignette correction {strength:.2f}")

        # ---- resize ---------------------------------------------------------
        if params.size:
            longest = max(image.width, image.height)
            if longest > params.size:
                scale = params.size / longest
                new_w = max(1, int(round(image.width * scale)))
                new_h = max(1, int(round(image.height * scale)))
                image = resize_image(image, new_w, new_h, wrap=False)
                operations.append(f"resized to {new_w}x{new_h}")
            else:
                res.note(ctx, f"preprocess.size is {params.size} but the source's "
                              f"longest edge is {longest}; nothing was upscaled")

        ctx.maps["albedo"] = image
        if "opacity" in ctx.inputs:
            opacity = ctx.inputs["opacity"]
            if (opacity.width, opacity.height) != (image.width, image.height):
                opacity = resize_image(opacity, image.width, image.height,
                                       wrap=False)
            ctx.maps["opacity"] = opacity

        ctx.physical["preprocess"] = {"operations": operations,
                                      "size": [image.width, image.height]}
        res.metric(ctx, "preprocess.width", float(image.width))
        res.metric(ctx, "preprocess.height", float(image.height))
        res.metric(ctx, "preprocess.operations", float(len(operations)))
        res.detail["line"] = (f"{image.width}x{image.height}"
                              + (": " + "; ".join(operations) if operations else
                                 ": no corrections applied"))
        if operations:
            res.note(ctx, "preprocess: " + "; ".join(operations))
        return res


STAGE = Preprocess()
