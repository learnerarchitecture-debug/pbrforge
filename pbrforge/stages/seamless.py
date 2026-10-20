"""`seamless`: the tile cascade -- lattice snap, offset search, band heal, mirror.

PIPELINE.md 1.4. This is the only stage that moves pixels, and it does so with an
honest algorithm cascade rather than a single magic bullet. The cascade is:

1.  **lattice snap** (when a trusted lattice exists: confidence >= 0.75)
    An exact integer-period crop from the detected lattice. No blending, no
    quality loss. If it reaches `accept_score`, the cascade stops there.

2.  **offset search**
    Symmetric trims from each side, scored on the wrap edges and then at full
    resolution. When a lattice exists but is below the trust threshold, the
    winning crop size is snapped to whole detected periods -- the geometry is
    still known even when the confidence is not.

3.  **band heal** (unless the method is pinned to `offset`)
    A ramp across a `seam_band_px`-wide band at the wrap edge that cancels the
    level offset at the seam exactly. The score after it is 100 *by
    construction*, so the stage also reports the correction's peak and added
    slope against the texture's own strongest interior join, and warns when the
    ramp is steeper than the material.

4.  **mirror** (only when `method == "mirror"` is pinned)
    An exact 2x2 tile via mirroring. Refuses if a normal map is present
    (handedness flip) or if 4x the area exceeds `ingest.max_pixels`.

Methods that are not in this build are refused by name:
`spectral` needs a 2D FFT, `quilt` needs PatchMatch, `ai` needs model weights.

Every map in `ctx.maps` is moved by the same rectangle (registration rule).
Maps of different sizes are refused, never resampled.

**The heal-cancels-exactly finding:** the final heal pass cancels the wrap join
to zero, so the seam score is 100 *by construction* after any heal and cannot
judge the heal. The stage therefore also reports `seamless.heal_peak`,
`seamless.heal_added_slope` (the ramp's steepest point per pixel) and
`seamless.heal_join_reference` (the texture's own p95 interior join), and warns
when `added_slope > reference` with "100 by construction" in the text.
"""

from __future__ import annotations

from typing import Any

from ..core.context import PipelineContext
from ..core.errors import LimitError, NonTileableError
from ..core.filters import percentile
from ..core.image import Image
from ..core.synth import interior_join_reference, ratio_to_score, seam_ratio_grey
from .base import Stage, StageResult

__all__ = ["Seamless", "STAGE", "_heal_axis", "_edge_density"]

#: The sentinel value for `seam_band_px` meaning "auto".
DEFAULT_SEAM_BAND_PX = 96

#: Auto band fraction of the longest edge (PIPELINE.md 1.4).
AUTO_BAND_FRACTION = 0.04

#: Min/max band in pixels (PIPELINE.md 1.4: clamped 48-256).
BAND_MIN = 48
BAND_MAX = 256

#: Lattice confidence at which an exact crop is trusted (PIPELINE.md 1.3).
LATTICE_TRUST = 0.75

#: How many candidates the edge-cost prefilter hands to the full scorer.
SHORTLIST = 3


# --------------------------------------------------------------------- helpers
def _plane(image: Image) -> list[float]:
    """The single plane used for measuring: channel 0 or Rec.709 luma."""
    return image.plane(0) if image.channels == 1 else image.luminance().plane(0)


def _field(images: dict[str, Image]) -> Image:
    """The image used for scoring: height if present, else albedo luminance."""
    if "height" in images:
        return images["height"]
    if "albedo" in images:
        return images["albedo"].luminance()
    first = next(iter(images.values()))
    return first if first.channels == 1 else first.luminance()


def _score(image: Image) -> tuple[float, int]:
    """`(e, score)` on one image, using the one seam definition."""
    e = seam_ratio_grey(_plane(image), image.width, image.height)
    return e, ratio_to_score(e)


def _wrap_step(image: Image) -> tuple[float, float]:
    """Worst single-pixel wrap difference per axis, in image units."""
    w, h = image.width, image.height
    p = _plane(image)
    hv = max(abs(p[y * w + w - 1] - p[y * w]) for y in range(h))
    vv = max(abs(p[(h - 1) * w + x] - p[x]) for x in range(w))
    return hv, vv


def _crop(image: Image, x0: int, y0: int, w: int, h: int) -> Image:
    """Crop a rectangle, returning a new Image with the same space."""
    ch = image.channels
    src_w = image.width
    data: list[float] = []
    for y in range(h):
        start = ((y0 + y) * src_w + x0) * ch
        data.extend(image.data[start:start + w * ch])
    return Image(w, h, ch, image.space, data)


def _move_all(ctx: PipelineContext, x0: int, y0: int, w: int, h: int) -> None:
    """Crop every map in `ctx.maps` by the same rectangle (registration rule)."""
    for kind, img in list(ctx.maps.items()):
        ctx.maps[kind] = _crop(img, x0, y0, w, h)


def _rect(x0: int, y0: int, w: int, h: int) -> tuple[int, int, int, int]:
    """The crop rectangle, as `(x, y, w, h)` -- the form consumers index."""
    return (x0, y0, w, h)


def _edge_density(image: Image, x0: int, y0: int, w: int, h: int) -> float:
    """Mean |gradient| inside a window: every adjacent pair, both axes, no stride.

    Used only to reject crop windows that cut through material. Two things are
    deliberate:

      - **No sampling stride.** A stride-2 scan misses a one-pixel-wide edge
        entirely -- the same failure `preprocess.dejpeg` had to fix -- and a
        filter whose whole job is to notice narrow structure cannot afford it.
      - **It is compared against a reference from the same image.** Density is a
        mean of |gradient|; comparing a window against the whole image it came
        from is meaningful, comparing across resolutions would not be.
    """
    p = _plane(image)
    src_w = image.width
    total = 0.0
    count = 0
    for y in range(y0, y0 + h):
        row = y * src_w
        line = p[row + x0:row + x0 + w]
        for a, b in zip(line, line[1:]):
            total += abs(b - a)
            count += 1
        if y + 1 < image.height:
            down = p[row + src_w + x0:row + src_w + x0 + w]
            for a, b in zip(line, down):
                total += abs(b - a)
                count += 1
    return total / count if count else 0.0


def _heal_axis(image: Image, band: int, horizontal: bool = True,
               smooth: bool = True) -> tuple[Image, float]:
    """Cancel the wrap join on one axis exactly. Returns `(new_image, peak)`.

    The correction is applied to the far band only (the right column band for a
    horizontal pass, the bottom row band for a vertical one) so the other edge
    stays bit-identical. Its value at the band's inner end is zero and at the
    seam is exactly minus the measured edge difference, which makes the wrap
    join zero -- the reason the post-heal score is 100 by construction.

    `smooth=False` uses a linear ramp; `smooth=True` a smoothstep ramp, whose
    slope at the seam is zero, so the correction joins the untouched interior
    with no slope kink at the seam side.
    """
    w, h, ch = image.width, image.height, image.channels
    n = h if horizontal else w
    band = max(2, min(int(band), n // 2))
    p = _plane(image)

    if horizontal:
        diffs = [p[y * w + w - 1] - p[y * w] for y in range(h)]
    else:
        diffs = [p[(h - 1) * w + x] - p[x] for x in range(w)]
    peak = max((abs(d) for d in diffs), default=0.0)

    data = list(image.data)
    span = band - 1
    for i in range(n):
        d = diffs[i]
        if abs(d) < 1e-15:
            continue
        for k in range(band):
            u = k / span
            t = (u * u * (3.0 - 2.0 * u)) if smooth else u
            corr = -d * t
            if horizontal:
                base = (i * w + (w - band + k)) * ch
            else:
                base = ((h - band + k) * w + i) * ch
            for c in range(ch):
                data[base + c] += corr
    return Image(w, h, ch, image.space, data), peak


def _mirror_tile(image: Image) -> Image:
    """An exact 2x2 mirror tile: reflect horizontally, then vertically."""
    w, h, ch = image.width, image.height, image.channels
    half_rows: list[float] = []
    for y in range(h):
        row = image.data[y * w * ch:(y + 1) * w * ch]
        half_rows.extend(row)
        for x in range(w - 1, -1, -1):
            half_rows.extend(row[x * ch:(x + 1) * ch])
    stride = 2 * w * ch
    out: list[float] = []
    for y in range(h):
        out.extend(half_rows[y * stride:(y + 1) * stride])
    for y in range(h - 1, -1, -1):
        out.extend(half_rows[y * stride:(y + 1) * stride])
    return Image(2 * w, 2 * h, ch, image.space, out)


class Seamless(Stage):
    name = "seamless"
    requires = ("lattice",)
    needs = ()
    produces = ()             # rewrites maps in place, produces no new kinds
    params_block = "seamless"
    cacheable = True
    summary = "make the tile set seamless (lattice/offset/heal/mirror)"

    # ---------------------------------------------------------------- geometry
    def _band(self, block: Any, w: int, h: int) -> int:
        """The heal band, in pixels.

        PIPELINE.md 1.4: 4% of the longest edge, clamped 48-256, unless the user
        set `seam_band_px` explicitly. The default value 96 is the sentinel for
        "auto", which is why an explicit 96 cannot be distinguished from it --
        documented rather than hidden.
        """
        raw = int(block.seam_band_px)
        if raw == DEFAULT_SEAM_BAND_PX:
            raw = int(round(AUTO_BAND_FRACTION * max(w, h)))
            raw = max(BAND_MIN, min(raw, BAND_MAX))
        return max(2, min(raw, max(2, min(w, h) // 2)))

    def _offset_search(self, field: Image, block: Any,
                       snap_x: int | None, snap_y: int | None
                       ) -> tuple[int, int, int, int]:
        """Pick the symmetric crop whose wrap edges agree best.

        Cost is the mean absolute wrap difference on both axes -- O(w+h) per
        candidate, so the search is cheap and needs no resampled proxy (which
        would blur the very edge structure it is judging). The shortlist is
        rescored in full by the caller.
        """
        w, h = field.width, field.height
        p = _plane(field)
        sw = int(block.search_window)
        max_x = min(sw, (w - 1) // 2)
        max_y = min(sw, (h - 1) // 2)
        step = max(1, sw // 8)
        xs = sorted({0, max_x} | set(range(0, max_x + 1, step)))
        ys = sorted({0, max_y} | set(range(0, max_y + 1, step)))

        scored: list[tuple[float, int, int]] = []
        for oy in ys:
            bottom = h - 1 - oy
            for ox in xs:
                right = w - 1 - ox
                hcost = sum(abs(p[y * w + right] - p[y * w + ox])
                            for y in range(oy, bottom + 1))
                vcost = sum(abs(p[bottom * w + x] - p[oy * w + x])
                            for x in range(ox, right + 1))
                rows = max(1, bottom - oy + 1)
                cols = max(1, right - ox + 1)
                scored.append((hcost / rows + vcost / cols, ox, oy))
        scored.sort(key=lambda t: t[0])

        best: tuple[int, int, int, int, int] | None = None
        for _, ox, oy in scored[:SHORTLIST]:
            cw, ch = w - 2 * ox, h - 2 * oy
            if snap_x:
                cw = max(snap_x, (cw // snap_x) * snap_x)
            if snap_y:
                ch = max(snap_y, (ch // snap_y) * snap_y)
            if cw < 4 or ch < 4:
                continue
            e, s = _score(_crop(field, ox, oy, cw, ch))
            # Prefer the plain rectangle when scores tie: a smaller crop that
            # scores the same is not better.
            if best is None or s > best[0]:
                best = (s, ox, oy, cw, ch)
        if best is None:
            return 0, 0, w, h
        _, ox, oy, cw, ch = best
        return ox, oy, cw, ch

    # ------------------------------------------------------------------ mirror
    def _mirror(self, ctx: PipelineContext, block: Any,
                res: StageResult) -> StageResult:
        if "normal" in ctx.maps:
            raise LimitError(
                "seamless.method = 'mirror' is refused while a normal map is "
                "present.\n"
                "  why      mirroring a tangent-space normal reverses the "
                "surface's handedness: the red channel has to be negated and "
                "the blue kept, which is not a simple pixel operation\n"
                "  fix      use a different method, or remove the normal map "
                "if mirroring is the only way to tile this material")
        w = next(iter(ctx.maps.values())).width
        h = next(iter(ctx.maps.values())).height
        max_pixels = getattr(getattr(ctx.params, "ingest", None), "max_pixels", None)
        if max_pixels and 4 * w * h > max_pixels:
            raise LimitError(
                f"mirror would produce a {2 * w}x{2 * h} tile ({4 * w * h} px), "
                f"which exceeds ingest.max_pixels ({max_pixels}).\n"
                f"  why      the 2x2 mirror tile is 4x the area of the source\n"
                f"  fix      increase max_pixels, or mirror a smaller source "
                f"crop")

        for kind, img in list(ctx.maps.items()):
            ctx.maps[kind] = _mirror_tile(img)
        out = ctx.maps.get("height") or next(iter(ctx.maps.values()))
        _, score = _score(out)

        res.warn(ctx, "mirror: produced a 2x2 exact tile with symmetry lines; "
                      "the material will show visible mirror symmetry")
        res.note(ctx, f"mirror: {2 * w}x{2 * h} exact tile, score {score}, "
                      f"symmetry lines disclosed")
        res.metric(ctx, "seamless.score_heal", float(score))
        res.metric(ctx, "seamless.mirror", 1.0)
        ctx.physical["seamless"] = {
            "method": "mirror", "rect": None,
            "score_before": score, "score_after": score,
        }
        res.detail["line"] = (f"mirror: {2 * w}x{2 * h} exact tile, "
                              f"score {score}, symmetry lines disclosed")
        return res

    # -------------------------------------------------------------------- main
    def kinds_produced(self, where: str, ctx: PipelineContext) -> tuple[str, ...]:
        """A rewriting stage: the answer is what is in the set after the run."""
        if where == "inputs":
            return ()
        return tuple(sorted(ctx.maps))

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)

        if not params.enabled:
            res.note(ctx, "seamless.enabled = false: skipping the cascade")
            return res
        if not ctx.maps:
            raise LimitError(
                "seamless needs at least one map in the working set.\n"
                "  why      there is nothing to tile\n"
                "  fix      run ingest first, or supply a map")

        # All maps must be the same size -- the registration rule.
        sizes = {(img.width, img.height) for img in ctx.maps.values()}
        if len(sizes) > 1:
            listed = ", ".join(f"{k} {v.width}x{v.height}"
                               for k, v in sorted(ctx.maps.items()))
            raise LimitError(
                f"all maps must be the same size for seamless ({listed}).\n"
                f"  why      the cascade moves every map by the same rectangle; "
                f"resampling would misregister them\n"
                f"  fix      re-run the stage that produced the odd size, or "
                f"crop the odd map out")

        w = next(iter(ctx.maps.values())).width
        h = next(iter(ctx.maps.values())).height

        # -------- methods that are not in this build refuse by name ----------
        if params.method == "spectral":
            raise LimitError(
                "seamless.method = 'spectral' is not in this build.\n"
                "  why      spectral tiling needs a 2D FFT of every map and a "
                "synthesis pass over the power spectrum; in pure Python that is "
                "minutes per map for a result the lattice and heal branches "
                "already reach on man-made material\n"
                "  fix      use method 'auto' (lattice snap, then offset crop, "
                "then band heal), which is exact for periodic material")
        if params.method == "quilt":
            raise LimitError(
                "seamless.method = 'quilt' is not in this build.\n"
                "  why      graph-cut quilting needs PatchMatch, which is not "
                "implemented here; a naive quilt would paste visible seams and "
                "report success\n"
                "  fix      use method 'auto', or crops that repeat and let the "
                "offset search find the period")
        if params.method == "ai":
            raise LimitError(
                "seamless.method = 'ai' is not in this build.\n"
                "  why      the AI refinement layer needs model weights that "
                "are not shipped, and it is off by default by design (see "
                "docs/AI_FEATURES.md)\n"
                "  fix      use method 'auto'; AI is a refinement layer, not a "
                "substitute for the deterministic cascade")

        # -------- mirror is pinned, so it is the whole method -----------------
        if params.method == "mirror":
            return self._mirror(ctx, params, res)

        lattice = ctx.physical.get("lattice") if params.detect_repeats else None
        confidence = float(lattice.get("confidence", 0.0)) if lattice else 0.0
        trusted = bool(lattice) and confidence >= LATTICE_TRUST and bool(
            lattice.get("snapped"))

        # -------- pinned lattice ----------------------------------------------
        if params.method == "lattice":
            if not trusted:
                raise LimitError(
                    "seamless.method = 'lattice' was pinned, but no trusted "
                    "lattice was detected for this material.\n"
                    "  why      an exact crop needs a repeat unit and a "
                    f"confidence of at least {LATTICE_TRUST}; the detection "
                    f"gave {confidence:.2f}\n"
                    "  fix      use method 'auto' so the offset and heal "
                    "branches can run, or pin 'offset' to see the best window "
                    "without healing")
            x0, y0, cw, ch = self._lattice_rect(lattice, w, h)
            _move_all(ctx, x0, y0, cw, ch)
            _, score = _score(_field(ctx.maps))
            res.metric(ctx, "seamless.score_lattice", float(score))
            if score < params.accept_score:
                raise NonTileableError(
                    f"the pinned lattice crop scored {score}, below "
                    f"seamless.accept_score {params.accept_score}.\n"
                    f"  why      the detected period does not divide the crop "
                    f"exactly in this material\n"
                    f"  fix      use method 'auto' so the heal can cancel the "
                    f"remaining join, or lower accept_score")
            self._finish_lattice(ctx, res, x0, y0, cw, ch, score, w, h)
            return res

        # -------- trusted lattice in auto mode --------------------------------
        if trusted:
            x0, y0, cw, ch = self._lattice_rect(lattice, w, h)
            _move_all(ctx, x0, y0, cw, ch)
            _, score = _score(_field(ctx.maps))
            res.metric(ctx, "seamless.score_lattice", float(score))
            if score >= params.accept_score:
                self._finish_lattice(ctx, res, x0, y0, cw, ch, score, w, h)
                return res
            res.note(ctx, f"lattice snap scored {score}, below "
                          f"seamless.accept_score {params.accept_score}; "
                          f"continuing to the offset search")
        elif lattice:
            res.note(ctx, f"lattice confidence {confidence:.2f} is below the "
                          f"{LATTICE_TRUST} needed for an exact crop; using its "
                          f"periods only to snap the crop size")

        snap_x = snap_y = None
        if lattice:
            px = lattice.get("period_x")
            py = lattice.get("period_y")
            snap_x = max(2, int(round(float(px)))) if px else None
            snap_y = max(2, int(round(float(py)))) if py else None

        # -------- already tiles? ----------------------------------------------
        field = _field(ctx.maps)
        e0, score0 = _score(field)
        res.metric(ctx, "seamless.score_before", float(score0))
        if score0 >= params.accept_score:
            res.note(ctx, f"the map set already tiles at score {score0} "
                          f"(e {e0:.2f}); no crop, no blend, nothing changed")
            ctx.physical["seamless"] = {
                "method": "none", "rect": None,
                "score_before": score0, "score_after": score0,
            }
            res.detail["line"] = (f"already seamless: score {score0} "
                                  f"(e {e0:.2f}), nothing changed")
            return res

        # -------- offset search ----------------------------------------------
        ox, oy, cw, ch = self._offset_search(field, params, snap_x, snap_y)
        _, score_off = _score(_crop(_field(ctx.maps), ox, oy, cw, ch))
        res.metric(ctx, "seamless.score_offset", float(score_off))
        res.metric(ctx, "seamless.crop_loss",
                   float(max(0.0, 1.0 - (cw * ch) / (w * h))))

        if score_off >= params.accept_score:
            method = "offset"
            res.note(ctx, f"offset crop: {cw}x{ch} at {ox},{oy}, "
                          f"score {score_off} -- already exact, so the heal "
                          f"pass has nothing to cancel")
        else:
            method = "heal"

        if params.method == "offset":
            if method == "offset":
                _move_all(ctx, ox, oy, cw, ch)
                ctx.physical["seamless"] = {
                    "method": "offset", "rect": _rect(ox, oy, cw, ch),
                    "score_before": score0, "score_after": score_off,
                    "band": None,
                }
                res.detail["line"] = (f"offset: crop {cw}x{ch} at {ox},{oy}, "
                                      f"score {score_off}")
                return res
            raise NonTileableError(
                f"no window reached seamless.accept_score "
                f"{params.accept_score}; the best offset crop scored "
                f"{score_off}.\n"
                f"  why      method 'offset' was pinned, so the band heal did "
                f"not run; no window can fix a brightness gradient because "
                f"every window carries the same one\n"
                f"  fix      use method 'auto' to let the band heal cancel the "
                f"join, or fix baked lighting first: delight.strength is the "
                f"control for a source with a lighting gradient")

        # -------- band heal ---------------------------------------------------
        _move_all(ctx, ox, oy, cw, ch)
        field = _field(ctx.maps)
        reference = interior_join_reference(_plane(field), field.width,
                                            field.height)
        band = self._band(params, cw, ch)

        peak = 0.0
        slope = 0.0
        for kind, img in list(ctx.maps.items()):
            healed, p1 = _heal_axis(img, band, horizontal=True, smooth=True)
            healed, p2 = _heal_axis(healed, band, horizontal=False, smooth=True)
            p = max(p1, p2)
            if p > peak:
                peak = p
            slope = max(slope, p / max(1, band - 1))
            ctx.maps[kind] = healed

        _, score_heal = _score(_field(ctx.maps))
        res.metric(ctx, "seamless.score_heal", float(score_heal))
        res.metric(ctx, "seamless.heal_peak", float(peak))
        res.metric(ctx, "seamless.heal_added_slope", float(slope))
        res.metric(ctx, "seamless.heal_join_reference", float(reference))

        if slope > reference:
            res.warn(ctx, f"the band heal cancelled a mismatch of up to "
                          f"{peak:.4f} by adding a ramp whose steepest point is "
                          f"{slope:.5f} per pixel -- steeper than the strongest "
                          f"join the texture already contains "
                          f"({reference:.5f}). The score after the heal is 100 "
                          f"by construction, so it cannot judge this.")

        if method == "offset":
            res.note(ctx, f"heal pass on the exact offset crop found no join "
                          f"to cancel (band {band}px); score stays {score_heal}")
        else:
            res.note(ctx, f"heal: offset crop {cw}x{ch} at {ox},{oy}, healed "
                          f"band {band}px -- score {score_heal} "
                          f"(was {score_off})")
        ctx.physical["seamless"] = {
            "method": method, "rect": _rect(ox, oy, cw, ch), "band": band,
            "score_before": score0, "score_offset": score_off,
            "score_after": score_heal, "heal_peak": peak,
            "heal_added_slope": slope, "heal_join_reference": reference,
        }
        res.detail["line"] = (f"{method}: offset crop {cw}x{ch} at {ox},{oy}, "
                              f"healed band {band}px -- score {score_heal} "
                              f"(was {score_off})")

        if score_heal < params.accept_score:
            raise NonTileableError(
                f"the band heal produced score {score_heal}, below "
                f"seamless.accept_score {params.accept_score}.\n"
                f"  why      the remaining mismatch is structural, not a level "
                f"offset, so a ramp cannot cancel it\n"
                f"  fix      crop the source to a region that repeats, or use "
                f"mirror for noise-like material; delight.strength is the "
                f"control if the join is a lighting gradient")
        return res

    # ------------------------------------------------------------------ pieces
    @staticmethod
    def _lattice_rect(lattice: dict[str, Any], w: int,
                      h: int) -> tuple[int, int, int, int]:
        raw = lattice.get("rect")
        if isinstance(raw, dict):
            x0 = int(raw["x"]); y0 = int(raw["y"])
            cw = int(raw["w"]); ch = int(raw["h"])
        elif raw is not None:
            x0, y0, cw, ch = (int(v) for v in raw)
        else:
            x0, y0, cw, ch = 0, 0, w, h
        x0 = max(0, min(x0, w - 2))
        y0 = max(0, min(y0, h - 2))
        cw = max(2, min(cw, w - x0))
        ch = max(2, min(ch, h - y0))
        return x0, y0, cw, ch

    def _finish_lattice(self, ctx: PipelineContext, res: StageResult,
                        x0: int, y0: int, cw: int, ch: int,
                        score: int, w0: int, h0: int) -> None:
        res.note(ctx, f"lattice snap: exact crop to {cw}x{ch} at {x0},{y0} "
                      f"-- score {score} clears the bar, so the cascade "
                      f"stops here")
        res.metric(ctx, "seamless.crop_loss",
                   float(max(0.0, 1.0 - (cw * ch) / (w0 * h0))))
        ctx.physical["seamless"] = {
            "method": "lattice", "rect": _rect(x0, y0, cw, ch),
            "score_before": score, "score_after": score,
        }
        res.detail["line"] = (f"lattice snap: {cw}x{ch} at {x0},{y0}, "
                              f"score {score}")


STAGE = Seamless()
