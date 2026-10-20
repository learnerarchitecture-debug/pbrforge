"""`pack`: turn the map set into the files one renderer expects.

PIPELINE.md 1.14. This stage writes and decides; it derives nothing. Everything it
emits is a function of `ctx.maps` and the preset, which is why it is the one stage
whose output `qc` audits file by file.

**The layout, and why it is written inside `ctx.work`.** The export tree is

    <out>/maps/                  the masters: one file per kind, at the largest
                                 requested resolution -- the truth, un-packed
    <out>/export/<preset>/       the packaged set: what the renderer actually
                                 loads, packed and named per the preset
    <out>/material.json          provenance, scale, the qc verdict once it exists

which is ARCHITECTURE.md 1's library layout with the material folder standing in
for `<out>`. `material.json` and every `outputs` path in it are relative to that
folder, so the whole thing can be moved to another machine or renamed without
rewriting anything. That is also why the stage writes into `ctx.work` and the
pipeline driver *publishes* afterwards (by renaming, when it can): a stage that
wrote straight into the library would leave a half-finished material behind when a
later stage failed, and there would be no way to tell.

**What it refuses rather than approximates.** A packed order whose roughness
channel has no source map (a fabricated 0.5 is a number, not a measurement); two
preset entries that resolve to the same filename (one of them would silently win);
two resolutions through a template with no `{res}` token (the second would
overwrite the first); a glossiness conversion for a preset that did not name a
curve (PRESETS.md 4.2 says the legacy curve is not a plain inversion, and this
build will not invent the formula). Each of those is a case where carrying on
would produce a file that looks like a deliverable.

**What it does not do.** It does not decide the packing order, the bit depth or
the colour space: those are the preset's, and the preset's own loader refuses
values the registry contradicts. It does not resample maps into registration --
`needs` are read as one grid, and a mismatch is refused, because silently
resampling one map to match another is exactly the class of bug the registration
rule in `seamless` exists to prevent. And it does not renormalise anything except
the normal map, which the doc requires by name.
"""

from __future__ import annotations

import math
import shutil
from typing import Any, Callable

from .. import __version__
from ..core.colour import Space, spec_for
from ..core.context import PipelineContext
from ..core.errors import LimitError
from ..core.image import Image
from ..export import handoff, manifest, naming, presets
from ..export.presets import PACKED_ORDERS, Preset, PresetMap
from ..export.writers import write_file
from .base import Stage, StageResult

__all__ = ["Pack", "STAGE"]

#: Kinds that exist to measure or to build other maps, not to be shipped. `macro`
#: is a scale factor for `variation` and `shading` is the illumination `delight`
#: divided out; neither is a surface property, so writing them into a renderer's
#: folder would be noise with a plausible name.
_SUPPORT_KINDS = ("shading", "macro")

#: The order masters are written in: the maps a renderer is most likely to want
#: first, so `maps/` reads sensibly in a file browser.
_MASTER_ORDER = ("albedo", "normal", "height", "roughness", "metallic", "ao",
                 "cavity", "curvature", "opacity", "emissive", "glossiness")

#: What a missing packed channel is filled with, and the sentence that says so.
#: Only for values that *have* an honest default: a material with no metallic map
#: is a dielectric (0), and a material with no occlusion map is not occluded (1).
#: Roughness is deliberately absent from this table: 0.5 is not a default, it is a
#: guess, and the stage refuses instead.
_PACKED_FILL = {"metallic": 0.0, "ao": 1.0, "zero": 0.0}


def _curve(curve: dict[str, Any] | None, where: str) -> Callable[[float], float]:
    """The glossiness conversion. Named forms only -- see PRESETS.md 4.2."""
    if not curve:
        raise LimitError(
            f"{where}: the preset asks for glossiness output but names no "
            f"glossiness_curve.\n"
            f"  why      PRESETS.md 4.2 states that the legacy glossiness input "
            f"does not take a plain inversion of roughness, and leaves the curve "
            f"to the preset. Inverting the map by hand would ship a value the "
            f"renderer was not told about\n"
            f"  fix      add a glossiness_curve to the preset's roughness entry "
            f"(forms: " + ", ".join(presets.GLOSSINESS_FORMS) + "), or set the "
            f"export's roughness mode to 'roughness'")
    form = curve.get("form")
    if form == "invert":
        return lambda v: 1.0 - v
    if form == "invert_power":
        power = float(curve.get("exponent", 1.0))
        return lambda v: (1.0 - v) ** power
    if form == "invert_sqrt":
        return lambda v: 1.0 - math.sqrt(v if v > 0.0 else 0.0)
    # presets.loads already refuses unknown forms, so reaching here means a
    # hand-built PresetMap. Same refusal, same words.
    raise LimitError(f"{where}: unknown glossiness_curve form {form!r}. fix use "
                     f"one of: {', '.join(presets.GLOSSINESS_FORMS)}")


def _map_plane(image: Image, fn: Callable[[float], float]) -> Image:
    """Apply `fn` to channel 0, leaving the other channels alone."""
    data = list(image.data)
    data[0::image.channels] = [fn(v) for v in data[0::image.channels]]
    return Image(image.width, image.height, image.channels, image.space, data)


def _clamp(image: Image, low: float, high: float) -> Image:
    """Clamp every channel. Used for the preset's `clamp` range."""
    data = [low if v < low else (high if v > high else v) for v in image.data]
    return Image(image.width, image.height, image.channels, image.space, data)


def _flip_green(image: Image) -> Image:
    """-Y from +Y: negate the green channel of a tangent-space normal.

    `1 - g` rather than `-g`, because a tangent-space normal's channels are stored
    as (n + 1) / 2 in 0..1. This is the only normal-map transform this build
    performs, and it is done because the preset *named* a convention: the value is
    in the file, not left as a note.
    """
    data = list(image.data)
    data[1::3] = [1.0 - v for v in data[1::3]]
    return Image(image.width, image.height, image.channels, image.space, data)


def _scaled(w: int, h: int, res: int) -> tuple[int, int]:
    """The target size for a longest-edge resolution, aspect preserved."""
    longest = max(w, h)
    if longest == res:
        return w, h
    k = res / longest
    return max(1, int(round(w * k))), max(1, int(round(h * k)))


def _resample(image: Image, size: tuple[int, int], *, normal: bool = False) -> Image:
    """Resample with wrap, so tiling survives, and renormalise if it is a normal.

    `wrap=True` is the whole reason this stage can resize a tileable map at all:
    a clamp-to-edge resample pulls the far edge into the near edge and un-tiles
    the texture at exactly the line the seam work fixed.
    """
    if (image.width, image.height) == size:
        return image
    return image.resample(size[0], size[1], wrap=True, renormalise=normal)


def _grid(maps: dict[str, Image], ctx: PipelineContext) -> tuple[int, int]:
    """The one pixel grid every map is on, or a refusal naming the two sizes."""
    sizes = {kind: (img.width, img.height) for kind, img in maps.items()}
    distinct = sorted(set(sizes.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{k} {v[0]}x{v[1]}" for k, v in sorted(sizes.items()))
        raise LimitError(
            f"the map set is on more than one grid: {detail}.\n"
            f"  why      packing writes one file per map, so a map on a different "
            f"grid would be wrong in the export at every pixel after the first, "
            f"and resampling it here would silently misregister every map that "
            f"carries position\n"
            f"  fix      re-run the stages that disagree, or crop the odd map out "
            f"of the set; `qc` can push a deliberate resample")
    if not distinct:
        raise LimitError(
            "there are no maps to pack.\n"
            "  why      pack writes files from ctx.maps, and the set is empty\n"
            "  fix      run the pipeline from ingest")
    return distinct[0]


def _planned(preset: Preset, order: str, params: Any) -> tuple[list[PresetMap], list[str]]:
    """Resolve the preset's map list against the effective channel order.

    `pack.channel_order` is an override of the preset's own packing, not of the
    preset's naming: a kind that disappears from the list because it moved into a
    packed channel keeps the name the preset gave it, and a packed file that the
    override asks for but the preset does not describe gets the conventional name.
    """
    notes: list[str] = []
    entries = [m for m in preset.maps if not m.packed]

    if order == "separate":
        for entry in [m for m in preset.maps if m.packed]:
            notes.append(
                f"pack.channel_order = 'separate' overrode the preset's "
                f"{entry.key} packing, so its channels are written as their own "
                f"files")
            for channel in PACKED_ORDERS[entry.key].values():
                if channel in ("zero", "smoothness"):
                    continue
                if any(m.key == channel for m in entries):
                    continue
                entries.append(PresetMap(
                    key=channel, file="{material}_{map}_{res}.png",
                    space=spec_for(channel).space.value,
                    bit=spec_for(channel).bit_depth, format="png"))
        return entries, notes

    channels = PACKED_ORDERS[order]
    components = {v for v in channels.values() if v not in ("zero", "smoothness")}
    kept: list[PresetMap] = []
    for entry in entries:
        if entry.key in components:
            notes.append(f"pack.channel_order = '{order}' moved the {entry.key} "
                         f"map into the packed {order} file")
            continue
        kept.append(entry)
    wanted = next((m for m in preset.maps if m.key == order), None)
    if wanted is None:
        wanted = PresetMap(key=order, file="{material}_" + order + "_{res}.png",
                           space="data", bit=8, format="png",
                           notes="written because pack.channel_order asked for "
                                 "this packing, not because the preset describes it")
        notes.append(f"pack.channel_order = '{order}' asked for a packing this "
                     f"preset does not describe; the packed file is named "
                     f"{wanted.file}")
    kept.append(wanted)
    return kept, notes


class Pack(Stage):
    name = "pack"
    requires = ("upscale",)
    needs = ("albedo",)
    produces = ()
    params_block = "pack"
    cacheable = False          # it writes files: a warm run must still write them
    summary = "write the renderer-ready files and the manifest"

    # ---------------------------------------------------------------- helpers
    def _resolution_plan(self, params: Any, preset: Preset,
                         entries: list[PresetMap]) -> tuple[int, ...]:
        resolutions = tuple(sorted({int(r) for r in params.resolutions}, reverse=True))
        if len(resolutions) > 1:
            for entry in entries:
                if "{res}" not in entry.file:
                    raise LimitError(
                        f"resolutions {list(resolutions)} would write "
                        f"{entry.file!r} twice (it has no {{res}} token).\n"
                        f"  why      the second resolution would overwrite the "
                        f"first, and the export would report every file written\n"
                        f"  fix      ask for one resolution, or put {{res}} in the "
                        f"template")
        return resolutions

    def _masters(self, ctx: PipelineContext, res: StageResult, *, out_root: Any,
                 size: tuple[int, int], resolution: int,
                 params: Any) -> list[dict[str, Any]]:
        """Write `maps/`: one PNG per kind, at the largest requested resolution."""
        records: list[dict[str, Any]] = []
        skipped: list[str] = []
        for kind in _MASTER_ORDER + tuple(sorted(set(ctx.maps) - set(_MASTER_ORDER))):
            if kind not in ctx.maps:
                continue
            if kind in _SUPPORT_KINDS:
                skipped.append(kind)
                continue
            spec = spec_for(kind)
            image = _resample(ctx.maps[kind], _scaled(*size, resolution),
                              normal=(kind == "normal"))
            name = naming.render("{material}_{map}_{res}", "the masters template",
                                 material=params.material_name or
                                 ctx.params.material,
                                 map=kind, res=naming.res_token(resolution))
            record = write_file(out_root / "maps" / f"{name}.png", image,
                                kind=kind, space=spec.space,
                                bit_depth=16 if spec.needs_16 else 8,
                                fmt="png", where=f"the {kind} master")
            records.append(record)
        if skipped:
            res.note(ctx, f"not written to maps/: {', '.join(skipped)}. They are "
                          f"measurements or working layers rather than surface "
                          f"properties, so no renderer has a slot for them")
        return records

    def _packed(self, ctx: PipelineContext, entry: PresetMap, res: StageResult,
                sources: dict[str, Image], size: tuple[int, int],
                where: str) -> tuple[Image, list[str]]:
        """Build a packed-channel image from its sources, or refuse."""
        notes: list[str] = []
        channels = PACKED_ORDERS[entry.key]
        width, height = size
        data = [0.0] * (width * height * len(channels))
        roughness = sources.get("roughness")
        for index, (slot, source) in enumerate(channels.items()):
            if source == "smoothness":
                if roughness is None:
                    raise LimitError(
                        f"{where}: the {entry.key} file needs a roughness map for "
                        f"its smoothness channel and the map set has none.\n"
                        f"  why      smoothness is 1 - roughness (PRESETS.md 4.6) "
                        f"and there is nothing to subtract from; a filled 0.5 "
                        f"would be a made-up number in a channel a renderer reads "
                        f"as a surface property\n"
                        f"  fix      run derive_rough (the pipeline order does "
                        f"this), or export with a different channel_order")
                plane = [1.0 - v for v in roughness.plane(0)]
            elif source == "zero":
                plane = [0.0] * (width * height)
            else:
                image = sources.get(source)
                if image is None:
                    if source not in _PACKED_FILL:
                        raise LimitError(
                            f"{where}: the {entry.key} file needs a {source} map "
                            f"and the map set has none.\n"
                            f"  why      this build fills only channels whose "
                            f"absence *has* a default -- no metallic is a "
                            f"dielectric, no occlusion is no occlusion -- and "
                            f"{source} is a measurement, so a filled value would "
                            f"be invented\n"
                            f"  fix      supply {source}, run the stage that "
                            f"derives it, or export with a different "
                            f"channel_order")
                    plane = [_PACKED_FILL[source]] * (width * height)
                    notes.append(
                        f"the {source} channel of {entry.key} is filled with "
                        f"{_PACKED_FILL[source]:g}: no {source} map exists in "
                        f"this set, and {source} has an honest default")
                else:
                    plane = image.plane(0)
            data[index::len(channels)] = plane
        return Image(width, height, len(channels), Space.DATA, data), notes

    # ------------------------------------------------------------------- run
    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()
        res = StageResult(self.name)
        preset = presets.load(params.preset)

        for warning in preset.warnings:
            res.warn(ctx, warning)
        if not preset.verified_against_renderer:
            res.note(ctx, f"preset '{preset.name}' is marked "
                          f"verified_against_renderer: false: its packing and "
                          f"naming follow the cookbook, and nothing in this build "
                          f"has loaded the result in {preset.label}")

        order = params.channel_order or preset.channel_order
        entries, plan_notes = _planned(preset, order, params)
        for note in plan_notes:
            res.note(ctx, note)
        resolutions = self._resolution_plan(params, preset, entries)
        for resolution in resolutions:
            if preset.max_resolution and resolution > preset.max_resolution:
                pass       # reported as a warning by the caller, not refused

        # ---- the orientation and convention this export actually contains ----
        source_orientation = str(getattr(ctx.params.normal, "orientation", "opengl"))
        normal_entry = preset.map_for("normal")
        target_orientation = (params.normal_orientation
                              or (normal_entry.orientation if normal_entry else None)
                              or source_orientation)
        flip_green = (target_orientation != source_orientation
                      and "normal" in ctx.maps)

        roughness_entry = preset.map_for("roughness")
        source_mode = str(ctx.physical.get("roughness_mode", "roughness"))
        mode = params.roughness_mode or (roughness_entry.mode
                                        if roughness_entry else None) or "roughness"
        packed_mode = order in PACKED_ORDERS
        if packed_mode and mode == "glossiness":
            res.warn(ctx, f"roughness mode 'glossiness' cannot apply to the "
                          f"{order} packing: the channel's meaning is fixed by "
                          f"the order (roughness, or smoothness as 1 - "
                          f"roughness), so the file carries roughness")
            mode = "roughness"
        if mode == "glossiness" and roughness_entry and not roughness_entry.packed:
            curve = _curve(roughness_entry.glossiness_curve,
                           f"preset '{preset.name}' roughness entry")
        else:
            curve = None
        if source_mode != mode:
            res.note(ctx, f"the map set records roughness_mode '{source_mode}' and "
                          f"this export writes '{mode}': the pixels in ctx.maps "
                          f"are roughness-valued (derive_rough converts nothing), "
                          f"so the conversion is applied here, once")

        # ---- one clean tree, so a re-run cannot inherit a stale file ----------
        out_root = ctx.work / "export"
        if out_root.exists():
            shutil.rmtree(out_root)
        pack_dir = out_root / "export" / preset.name
        pack_dir.mkdir(parents=True, exist_ok=True)

        size = _grid(ctx.maps, ctx)
        largest = max(size)
        for resolution in resolutions:
            if resolution > largest * 1.05:
                res.warn(ctx, f"the export writes {resolution} px from a "
                              f"{largest} px map: that is interpolation, not "
                              f"detail recovery (the upscale stage is a gate in "
                              f"this build and adds nothing). Bake at the "
                              f"resolution you intend to ship, or accept a "
                              f"softer file than the number suggests")
                break
        master_res = resolutions[0]
        res.note(ctx, f"writing masters to maps/ at {master_res} px and the "
                      f"packaged set to export/{preset.name}/ at "
                      f"{', '.join(str(r) for r in resolutions)} px under "
                      f"{out_root}")

        masters = self._masters(ctx, res, out_root=out_root, size=size,
                                resolution=master_res, params=params)

        # ---- the packaged set -------------------------------------------------
        written: list[dict[str, Any]] = []
        seen_paths: dict[str, str] = {}
        missing: dict[str, list[str]] = {}
        for resolution in resolutions:
            target = _scaled(*size, resolution)
            for entry in entries:
                where = f"{preset.name}/{entry.key}@{resolution}"
                # every source this entry needs, resampled once per resolution
                sources: dict[str, Image] = {}
                for kind in entry.source_kinds:
                    image = ctx.maps.get(kind)
                    if image is None:
                        missing.setdefault(str(resolution), []).append(
                            f"{entry.key}:{kind}")
                        continue
                    sources[kind] = _resample(image, target, normal=(kind == "normal"))
                if entry.key not in PACKED_ORDERS and entry.key not in sources:
                    continue

                if entry.key in PACKED_ORDERS:
                    image, fill_notes = self._packed(ctx, entry, res, sources,
                                                     target, where)
                    for note in fill_notes:
                        res.note(ctx, note)
                else:
                    image = sources[entry.key]
                    if entry.key == "normal" and flip_green:
                        image = _flip_green(_resample(image, target, normal=True))
                if entry.clamp is not None:
                    image = _clamp(image, entry.clamp[0], entry.clamp[1])
                if entry.key == "roughness" and curve is not None:
                    image = _map_plane(image, curve)

                name = naming.render(entry.file, f"preset {preset.name} "
                                     f"{entry.key}", material=(
                                         params.material_name or ctx.params.material),
                                     map=entry.key,
                                     res=naming.res_token(resolution),
                                     version=params.version or None,
                                     renderer=preset.target_renderer,
                                     category=params.category or None,
                                     scale=(f"{ctx.params.size_mm[0]:g}mm"
                                            if ctx.params.size_mm else None))
                path = pack_dir / name
                key = str(path).lower()
                if key in seen_paths:
                    raise LimitError(
                        f"the preset writes {name} twice ({seen_paths[key]} and "
                        f"{entry.key}).\n"
                        f"  why      the second write would replace the first and "
                        f"the run would still report both files written\n"
                        f"  fix      give the two maps different file templates in "
                        f"the preset")
                seen_paths[key] = entry.key
                record = write_file(path, image, kind=entry.key,
                                    space=entry.space, bit_depth=entry.bit,
                                    fmt=entry.format, where=where)
                record["slot"] = entry.slot
                record["resolution"] = resolution
                written.append(record)

        for resolution, items in sorted(missing.items()):
            res.note(ctx, f"not written at {resolution} px because the map set "
                          f"has no such map: {', '.join(sorted(set(items)))}")

        # ---- the record -------------------------------------------------------
        total_bytes = sum(int(w.get("bytes", 0)) for w in written)
        ctx.physical["packed"] = written
        ctx.physical["export_dir"] = str(out_root)
        ctx.physical["packed_dir"] = str(pack_dir)
        ctx.physical["masters_dir"] = str(out_root / "maps")
        ctx.physical["preset"] = preset.name
        ctx.physical["preset_label"] = preset.label
        ctx.physical["channel_order"] = order
        ctx.physical["normal_orientation"] = target_orientation
        ctx.physical["normal_source_orientation"] = source_orientation
        ctx.physical["normal_flipped"] = bool(flip_green)
        ctx.physical["roughness_mode"] = mode
        ctx.physical["resolutions"] = list(resolutions)
        ctx.artifacts["export_dir"] = out_root
        ctx.artifacts["packed_dir"] = pack_dir
        ctx.artifacts["masters_dir"] = out_root / "maps"

        size_mm = tuple(ctx.params.size_mm) if ctx.params.size_mm else None
        texel_source = "user" if size_mm else "assumed"
        if size_mm is None:
            res.note(ctx, "no size_mm for this material: the manifest records the "
                          "tile's pixel size and says the texel density is "
                          "unknown, rather than inventing a millimetre")
        payload = manifest.build(
            ctx, preset, out_root=out_root, entries=written, masters=masters,
            orientation=target_orientation, roughness_mode=mode,
            resolutions=resolutions, texel_source=texel_source)
        if params.write_sidecar:
            path = manifest.write(out_root / "material.json", payload)
            ctx.artifacts["material_json"] = path
        if params.write_handoff:
            unpacked = [k for k in ctx.maps
                        if k not in {e["kind"] for e in written} and k not in _SUPPORT_KINDS]
            path = handoff.write(
                pack_dir / handoff.filename(preset), preset, written,
                material=params.material_name or ctx.params.material,
                version=__version__, size_mm=size_mm,
                px_per_mm=payload["physical"]["px_per_mm"],
                orientation=target_orientation,
                unpacked=unpacked,
                extra_notes=[f"masters (un-packed, one file per map): "
                             f"{manifest.relpath(out_root / 'maps', out_root)}/"])
            ctx.artifacts["handoff"] = path

        res.metric(ctx, "pack.files", float(len(written)))
        res.metric(ctx, "pack.masters", float(len(masters)))
        res.metric(ctx, "pack.bytes", float(total_bytes))
        res.metric(ctx, "pack.resolutions", float(len(resolutions)))
        res.detail["line"] = (
            f"{preset.name}: {len(written)} packaged file(s) + {len(masters)} "
            f"master(s) at {', '.join(str(r) for r in resolutions)} px, "
            f"{order} packing, normal {target_orientation}"
            + (", green flipped" if flip_green else "")
            + f" -- {total_bytes / 1e6:.1f} MB")
        return res


STAGE = Pack()