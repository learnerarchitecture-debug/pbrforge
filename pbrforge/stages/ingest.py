"""`ingest`: decode the dropped PNG into the working set's source material.

The web UI decodes whatever the user actually has (JPEG, WebP, HEIC, phone
photo) in the browser and posts a PNG, because the portable build carries no
image codecs beyond the stdlib PNG reader. That is a deliberate trade: a browser
already has a correct, hardware-accelerated decoder for every format a user is
likely to drag in, and the server stays dependency-free.

What this stage does:

  * decodes 8/16-bit greyscale, RGB, grey+alpha and RGBA PNGs;
  * separates alpha into an ``opacity`` input instead of keeping it in albedo
    (a 4-channel albedo would be packed wrong by every preset);
  * downscales anything above ``ingest.max_pixels`` with an area filter, and
    says so in a note rather than silently cropping;
  * records the source classification the manifest needs.

It does **not** apply gamma. ``Image.from_png`` normalises storage to floats and
the ``Space.SRGB`` tag records what those numbers mean; the transfer function is
applied later, once, by a stage that has a reason to.
"""

from __future__ import annotations

from pathlib import Path

from ..core import png as png_mod
from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..core.ops import resize_image, saturation_plane
from ..stages.base import Stage, StageResult

__all__ = ["Ingest", "STAGE"]


class Ingest(Stage):
    name = "ingest"
    requires = ()
    needs = ()
    produces = ()
    produces_inputs = ("albedo", "opacity")
    params_block = "ingest"
    cacheable = True
    summary = "decode the dropped image and record what it is"

    def kinds_produced(self, where: str, ctx: PipelineContext) -> tuple[str, ...]:
        if where == "inputs":
            return tuple(sorted(ctx.inputs))
        return ()

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not ctx.sources:
            raise LimitError(
                "ingest has no source image.\n"
                "  why      the job was submitted without a file\n"
                "  fix      drop an image into the UI, or pass --source to 'pbrforge make'")
        if len(ctx.sources) > 1:
            raise LimitError(
                f"ingest received {len(ctx.sources)} files in one job.\n"
                "  why      this build converts one photographed material per job; "
                "packing unrelated images into one set would misregister their maps\n"
                "  fix      submit each image separately ('pbrforge batch' does this)")

        source = Path(ctx.sources[0])
        if not source.is_file():
            raise LimitError(f"the source image does not exist: {source}")
        suffix = source.suffix.lower()
        if suffix not in (".png",):
            raise LimitError(
                f"ingest cannot decode {suffix or 'a file with no extension'}: "
                "this build's portable decoder reads PNG only.\n"
                "  why      the installer ships no JPEG/Pillow decoder; the web UI "
                "converts every dropped image to PNG in the browser before upload\n"
                "  fix      open the image in any viewer and Save As PNG, or use the "
                "web UI's upload button")

        try:
            decoded = png_mod.read(source)
        except png_mod.PngError as exc:
            raise LimitError(
                f"could not decode {source.name}.\n"
                f"  why      {exc}\n"
                f"  fix      re-save the image as a non-interlaced 8- or 16-bit PNG")

        width, height = decoded.width, decoded.height
        pixels = width * height
        if pixels > params.max_pixels:
            scale = (params.max_pixels / pixels) ** 0.5
            new_w = max(1, int(width * scale))
            new_h = max(1, int(height * scale))
            res.note(ctx, f"source is {width}x{height} ({pixels / 1e6:.1f} MP), above "
                          f"ingest.max_pixels {params.max_pixels}; downscaled to "
                          f"{new_w}x{new_h} with an area filter")
        else:
            new_w, new_h = width, height

        channels = decoded.channels
        if channels not in (1, 2, 3, 4):
            raise LimitError(f"unsupported PNG channel count {channels}")

        # Normalise to RGB; keep alpha separately, because RGB is what every
        # colour preset expects and a 4-channel albedo is a bug waiting to happen.
        image = Image.from_png(decoded, "srgb")
        if channels == 1:
            grey = image.plane(0)
            data = [v for value in grey for v in (value, value, value)]
            rgb = Image(width, height, 3, "srgb", data)
            alpha = None
        elif channels == 2:
            grey = image.plane(0)
            data = [v for value in grey for v in (value, value, value)]
            rgb = Image(width, height, 3, "srgb", data)
            alpha = image.plane(1)
        elif channels == 3:
            rgb = image
            alpha = None
        else:
            # plane() is channel-major, so rebuild pixel-major explicitly.
            source_data = image.data
            rgb_data = [0.0] * (width * height * 3)
            for i in range(width * height):
                o = i * 4
                d = i * 3
                rgb_data[d] = source_data[o]
                rgb_data[d + 1] = source_data[o + 1]
                rgb_data[d + 2] = source_data[o + 2]
            rgb = Image(width, height, 3, "srgb", rgb_data)
            alpha = image.plane(3)

        if (new_w, new_h) != (width, height):
            rgb = resize_image(rgb, new_w, new_h, wrap=False)
            if alpha is not None:
                alpha_img = Image(width, height, 1, "data", alpha)
                alpha_img = resize_image(alpha_img, new_w, new_h, wrap=False)
                alpha = alpha_img.data

        ctx.inputs["albedo"] = rgb
        if alpha is not None and (min(alpha) < 0.999):
            ctx.inputs["opacity"] = Image(new_w, new_h, 1, "data", alpha)
            res.note(ctx, "the source has an alpha channel; it was separated into an "
                          "opacity map so the albedo stays 3-channel")
        elif alpha is not None:
            res.note(ctx, "the source alpha is fully opaque; no opacity map was made")

        # ---- classification -------------------------------------------------
        luma = rgb.luminance().plane(0)
        sat = saturation_plane(rgb)
        mean_luma = sum(luma) / len(luma)
        mean_sat = sum(sat) / len(sat)
        low = sum(1 for v in luma if v < 0.08) / len(luma)
        high = sum(1 for v in luma if v > 0.92) / len(luma)

        ctx.classification = {
            "sources": [{"file": source.name, "kind": "albedo", "role": "primary"}],
            "kinds": ["albedo"] + (["opacity"] if "opacity" in ctx.inputs else []),
            "primary": source.name,
            "format": "png",
            "input_size": [width, height],
            "working_size": [new_w, new_h],
            "mean_luminance": round(mean_luma, 4),
            "mean_saturation": round(mean_sat, 4),
            "clipped_black": round(low, 4),
            "clipped_white": round(high, 4),
        }
        ctx.physical["origin"] = "photo"
        ctx.physical["source_format"] = "png"
        ctx.physical["ingest"] = {
            "width": new_w, "height": new_h,
            "source_width": width, "source_height": height,
            "channels": 3, "mean_luminance": mean_luma,
        }

        res.metric(ctx, "ingest.pixels", float(new_w * new_h))
        res.metric(ctx, "ingest.mean_luminance", float(mean_luma))
        res.metric(ctx, "ingest.mean_saturation", float(mean_sat))
        res.metric(ctx, "ingest.clipped_black", float(low))
        res.metric(ctx, "ingest.clipped_white", float(high))
        res.detail["line"] = (f"{source.name}: {width}x{height} -> {new_w}x{new_h}, "
                              f"mean luma {mean_luma:.2f}, saturation {mean_sat:.2f}")
        res.note(ctx, f"decoded {source.name} at {new_w}x{new_h} into an sRGB albedo")
        return res


STAGE = Ingest()
