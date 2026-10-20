"""Renderer presets: the packing + naming + format profile for one target.

PRESETS.md 1 is the schema. This module is its enforcement point, and it takes
the doc's rule seriously: *an unknown token is an error at load time, not a silent
literal*. The same reasoning covers every other field here, because a preset is
the one place where a mistake is invisible until it reaches a renderer:

  - A `key` that is not a registry kind and not a packed order would be written
    from whatever map happened to share the name; refused.
  - A `space` that disagrees with the registry (`albedo` as `data`, say) is the
    exact failure `core/colour.assert_writable` exists to prevent, so it is caught
    here, before a single file exists.
  - A `format` this build cannot write (`exr`, `jpeg`) is refused at load with the
    alternative named, rather than failing halfway through an export.
  - `verified: false` on the `default` preset is refused: the doc says such a
    preset cannot be the default export, and a rule with no enforcement point is
    a comment.

Two things deliberately *warn* instead of refusing, because the user is allowed
to choose them and only the renderer knows better: writing a 16-bit map at 8-bit,
and a preset that names an external validator this build does not run. Both are
recorded on the loaded preset and surfaced by `pack` as stage warnings, which is
where a user will actually see them.

The path is a security boundary as well as a convenience: `pack.preset` arrives
from a parameter block, so the name is matched against a strict pattern before it
is joined to a directory. `../..` is not a preset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.colour import Space, spec_for
from ..core.errors import ColourSpaceError, LimitError, PbrForgeError
from . import naming

__all__ = [
    "PACKED_ORDERS", "Preset", "PresetError", "PresetMap", "available", "load",
    "loads", "packed_channels", "preset_dir",
]

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

#: Channel orders that are *not* four separate files. Each channel is either a
#: registry kind, `zero` (an unused channel, filled so it is never noise), or
#: `smoothness`.
#:
#: `smoothness` gets its own name rather than a curve because PRESETS.md 4.6
#: spells it out: Unity's Mask Map alpha is `1 - Roughness`. When the doc states a
#: formula, the code implements that formula; the curve machinery below exists for
#: the case the doc explicitly declines to define (legacy glossiness).
PACKED_ORDERS: dict[str, dict[str, str]] = {
    # Occlusion / Roughness / Metallic -- the archviz convention.
    "orm": {"r": "ao", "g": "roughness", "b": "metallic"},
    # Metallic / Roughness / AO -- the same three in a different order.
    "mra": {"r": "metallic", "g": "roughness", "b": "ao"},
    # Unity HDRP/URP Mask Map: R metallic, G occlusion, B detail, A smoothness.
    # The detail channel is filled with 0 (no detail mask exists in this build)
    # and that is reported, not hidden.
    "maskmap": {"r": "metallic", "g": "ao", "b": "zero", "a": "smoothness"},
    # glTF 2.0 metallicRoughness: G roughness, B metallic, R unused by the spec.
    # Occlusion is a separate texture in glTF, so a glTF preset lists "ao" beside
    # this one rather than packing it in.
    "gltf": {"r": "zero", "g": "roughness", "b": "metallic"},
}

#: Named channel transforms, as opposed to registry kinds.
CHANNEL_SOURCES = ("zero", "smoothness")

#: The curve forms the glossiness conversion understands. PRESETS.md 4.2/4.3
#: insist that legacy glossiness is "not a plain inversion" and leave the formula
#: to the preset -- so the preset must name one of these, and the form is checked
#: rather than guessed at.
GLOSSINESS_FORMS: dict[str, str] = {
    "invert": "(1 - roughness) -- the plain inversion, correct only when a "
              "preset's author has checked that their renderer wants it",
    "invert_power": "(1 - roughness) ** exponent",
    "invert_sqrt": "1 - sqrt(roughness)",
}

_TOP_KEYS = ("name", "label", "target_renderer", "renderer_versions_tested",
             "verified", "verified_against_renderer", "default", "maps",
             "material_defaults", "handoff", "validator", "notes",
             "max_resolution")

_MAP_KEYS = ("key", "file", "space", "bit", "format", "slot", "notes",
             "orientation", "mode", "clamp", "unit", "default_amplitude_mm",
             "strength_default", "glossiness_curve")

_SNIPPETS = ("maxscript", "blender", "gltf", "unity", "unreal", "text")

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")

_FORMATS = ("png", "tiff")


class PresetError(PbrForgeError):
    """A preset file this build will not load."""


@dataclass(frozen=True)
class PresetMap:
    """One output file in a preset."""

    key: str
    file: str
    space: str
    bit: int
    format: str
    slot: str = ""
    notes: str = ""
    orientation: str | None = None
    mode: str | None = None
    clamp: tuple[float, float] | None = None
    unit: str = ""
    default_amplitude_mm: float | None = None
    strength_default: float | None = None
    glossiness_curve: dict[str, Any] | None = None

    @property
    def packed(self) -> bool:
        """True when this file carries several kinds in its channels."""
        return self.key in PACKED_ORDERS

    @property
    def channels(self) -> int:
        if self.packed:
            return len(PACKED_ORDERS[self.key])
        try:
            spec = spec_for(self.key)
        except ColourSpaceError:
            return 1
        return max(spec.channels)

    @property
    def source_kinds(self) -> tuple[str, ...]:
        """The registry kinds this file needs. `zero` needs nothing.

        `smoothness` needs `roughness`: it is `1 - roughness` (PRESETS.md 4.6), so
        the channel's source is a real map rather than a constant. Leaving it out
        of this list would make a Mask Map export refuse for want of a map that
        was there all along.
        """
        if not self.packed:
            return (self.key,)
        kinds: set[str] = set()
        for value in PACKED_ORDERS[self.key].values():
            if value == "smoothness":
                kinds.add("roughness")
            elif value != "zero":
                kinds.add(value)
        return tuple(sorted(kinds))


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    target_renderer: str
    versions: tuple[str, ...]
    verified: bool
    verified_against_renderer: bool
    default: bool
    maps: tuple[PresetMap, ...]
    material_defaults: dict[str, Any] = field(default_factory=dict)
    handoff: dict[str, Any] = field(default_factory=dict)
    validator: str | None = None
    notes: tuple[str, ...] = ()
    #: Things the user is allowed to ask for that the doc warns about. `pack`
    #: turns these into stage warnings, so they appear in the run report rather
    #: than in a log nobody reads.
    warnings: tuple[str, ...] = ()
    #: A resolution above which this target stops being the right choice, from
    #: the cookbook's own numbers (Enscape 4096, Lumion 2048). `pack` warns above
    #: it rather than refusing: the user may have a reason, and the doc's numbers
    #: are about framerate, not about what the filesystem can hold.
    max_resolution: int | None = None
    path: Path | None = None

    def map_for(self, key: str) -> PresetMap | None:
        for entry in self.maps:
            if entry.key == key:
                return entry
        return None

    def kinds(self) -> tuple[str, ...]:
        """Every registry kind this preset needs from the map set."""
        wanted: list[str] = []
        for entry in self.maps:
            for kind in entry.source_kinds:
                if kind not in wanted:
                    wanted.append(kind)
        return tuple(wanted)

    @property
    def channel_order(self) -> str:
        """The order this preset packs to, or `separate` when it packs nothing."""
        for entry in self.maps:
            if entry.packed:
                return entry.key
        return "separate"


def preset_dir() -> Path:
    return PRESETS_DIR


def available() -> tuple[str, ...]:
    """The preset names shipped with this build, sorted."""
    if not PRESETS_DIR.is_dir():
        return ()
    return tuple(sorted(p.stem for p in PRESETS_DIR.glob("*.json")))


def _refuse(where: str, what: str, why: str, fix: str) -> LimitError:
    return LimitError(f"{where}: {what}.\n  why      {why}\n  fix      {fix}")


def _check_keys(payload: dict[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = [k for k in payload if k not in allowed]
    if unknown:
        raise _refuse(
            where, f"unknown field(s) {', '.join(sorted(unknown))}",
            "a preset is read by machines, and a misspelled field is silently "
            "ignored -- which is how a preset that 'sets the bit depth' exports "
            "8-bit files",
            f"use one of: {', '.join(allowed)}")


def _text(payload: dict[str, Any], key: str, where: str, *, required: bool = True,
          default: Any = "") -> Any:
    value = payload.get(key, default)
    if required and (value is None or value == ""):
        raise _refuse(where, f"missing field {key!r}",
                      "every field in a preset changes what a renderer sees",
                      f"set {key!r} in the preset file")
    return value


def _bool(payload: dict[str, Any], key: str, where: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise _refuse(where, f"{key} is {value!r}, not true or false",
                      "a JSON string like \"false\" is truthy, so the flag would "
                      "read as the opposite of what it says",
                      f"write {key}: {'true' if not value else 'false'} without quotes")
    return value


def _map_entry(raw: dict[str, Any], where: str) -> PresetMap:
    if not isinstance(raw, dict):
        raise _refuse(where, f"a map entry is {type(raw).__name__}, not an object",
                      "the schema is a list of objects", "see PRESETS.md 1")
    _check_keys(raw, _MAP_KEYS, where)
    key = str(_text(raw, "key", where))
    if key not in PACKED_ORDERS:
        try:
            spec_for(key)
        except ColourSpaceError as exc:
            raise _refuse(
                where, f"unknown map key {key!r}",
                "the key is the registry kind; a key that is neither a kind nor "
                "one of the packed orders would be filled from whatever map "
                "happened to be left over",
                f"{exc.args[0].splitlines()[0]}, or one of the packed orders: "
                f"{', '.join(sorted(PACKED_ORDERS))}") from None

    file_template = str(_text(raw, "file", where))
    naming.check(file_template, f"{where} ({key})")

    space = str(_text(raw, "space", where))
    try:
        Space(space)
    except ValueError:
        raise _refuse(where, f"{key}.space is {space!r}",
                      "the space decides whether a reader applies the sRGB "
                      "transfer function to the numbers",
                      "use 'srgb' for albedo and emissive, 'data' for everything "
                      "else (PRESETS.md 3)") from None
    if key in PACKED_ORDERS:
        if not Space(space).is_data:
            raise _refuse(
                where, f"the packed file {key!r} is declared {space!r}",
                "a packed ORM/Mask/MR file is three or four measurements in "
                "colour channels; nothing about it is colour",
                "declare it 'data'")
    else:
        spec = spec_for(key)
        if Space(space) is not spec.space:
            raise _refuse(
                where, f"{key} is declared {space!r} but the registry says "
                f"{spec.space.value!r}",
                f"PRESETS.md 3: {key} is {spec.written_as}, and the mismatch "
                f"survives export because the pixels look plausible in either "
                f"reading",
                f"declare it {spec.space.value!r}")

    bit = raw.get("bit", 8)
    if bit not in (8, 16):
        raise _refuse(where, f"{key}.bit is {bit!r}",
                      "the writers emit 8 or 16-bit unsigned samples",
                      "use 8 or 16")

    fmt = str(_text(raw, "format", where)).lower()
    if fmt not in _FORMATS:
        if fmt == "exr":
            raise _refuse(
                where, f"{key} asks for EXR",
                "an EXR writer needs a half-float encoder, and this build ships "
                "PNG and uncompressed TIFF -- the two containers every renderer "
                "in the cookbook reads",
                f"write the {key} map as 16-bit TIFF, which carries the same "
                f"sample depth")
        if fmt == "jpeg":
            raise _refuse(
                where, f"{key} asks for JPEG",
                "PRESETS.md 3 bans JPEG for normals, height, roughness, metallic, "
                "AO and packed maps outright, and this build has no JPEG encoder "
                "at all -- an 8-bit PNG is smaller than the artefact budget of "
                "the maps that would be allowed",
                "use 'png'")
        raise _refuse(where, f"{key}.format is {fmt!r}", "the writers emit PNG "
                      "and TIFF", f"use one of: {', '.join(_FORMATS)}")

    orientation = raw.get("orientation")
    if orientation is not None:
        orientation = str(orientation)
        if key != "normal":
            raise _refuse(where, f"{key}.orientation = {orientation!r}",
                          "tangent-space handedness is a normal-map property; on "
                          "any other map the field is noise that reads as a "
                          "feature",
                          "remove it, or set it on the normal entry")
        if orientation not in ("opengl", "directx"):
            raise _refuse(where, f"normal.orientation is {orientation!r}",
                          "the two conventions differ by the sign of the green "
                          "channel, and a third spelling would be acted on as "
                          "neither",
                          "use 'opengl' or 'directx'")

    mode = raw.get("mode")
    if mode is not None:
        mode = str(mode)
        if key not in ("roughness", "glossiness"):
            raise _refuse(where, f"{key}.mode = {mode!r}",
                          "mode names the roughness convention, and only the "
                          "roughness map has one",
                          "remove it")
        if mode not in ("roughness", "glossiness"):
            raise _refuse(where, f"{key}.mode is {mode!r}",
                          "a renderer reads one convention; anything else would "
                          "be written as roughness by default and be wrong in a "
                          "way that only shows under a light",
                          "use 'roughness' or 'glossiness'")

    curve = raw.get("glossiness_curve")
    if curve is not None:
        if mode != "glossiness":
            raise _refuse(where, f"{key} carries a glossiness_curve but its mode "
                          f"is {mode!r}",
                          "the curve only runs when the file is glossiness, so it "
                          "would be read, validated and then never applied",
                          "set mode: 'glossiness', or drop the curve")
        if not isinstance(curve, dict):
            raise _refuse(where, f"glossiness_curve is {type(curve).__name__}",
                          "the curve is an object with a form",
                          f"use {{'form': one of {', '.join(GLOSSINESS_FORMS)}}}")
        form = str(curve.get("form", ""))
        if form not in GLOSSINESS_FORMS:
            raise _refuse(
                where, f"glossiness_curve.form is {form!r}",
                "PRESETS.md 4.2 states that legacy glossiness is not a plain "
                "inversion and leaves the formula to the preset: this build "
                "implements the named forms below and will not invent one",
                f"use one of: " + "; ".join(f"{k} = {v}"
                                            for k, v in GLOSSINESS_FORMS.items()))
        if form == "invert_power":
            exponent = curve.get("exponent")
            if not isinstance(exponent, (int, float)) or exponent <= 0:
                raise _refuse(where, f"invert_power needs a positive exponent, "
                              f"got {exponent!r}",
                              "an exponent is the whole difference between this "
                              "form and 'invert'",
                              "set exponent to a positive number, e.g. 2.0")
        unknown = [k for k in curve if k not in ("form", "exponent", "source")]
        if unknown:
            raise _refuse(where, f"glossiness_curve has unknown field(s) "
                          f"{', '.join(sorted(unknown))}",
                          "a curve field nobody reads is a belief about the "
                          "export that is not true",
                          "use form, exponent, source")

    clamp = raw.get("clamp")
    if clamp is not None:
        if key in PACKED_ORDERS:
            raise _refuse(where, f"{key}.clamp is set on a packed file",
                          "a clamp on a packed file applies to every channel by "
                          "position, so a range meant for roughness would land on "
                          "occlusion and metallic as well",
                          "move the clamp to the separate roughness entry, or "
                          "remove it and clamp in the stage that derives the map")
        if (not isinstance(clamp, list) or len(clamp) != 2
                or not all(isinstance(v, (int, float)) for v in clamp)):
            raise _refuse(where, f"{key}.clamp is {clamp!r}",
                          "a clamp is a two-number range in 0..1",
                          "write [0.05, 0.95]")
        low, high = float(clamp[0]), float(clamp[1])
        if not 0.0 <= low < high <= 1.0:
            raise _refuse(where, f"{key}.clamp is {clamp!r}",
                          "a range outside 0..1 or with low >= high cannot be "
                          "applied to a 0..1 map without inventing a value",
                          "write two numbers in 0..1, low < high")

    unit = str(raw.get("unit", ""))
    if unit and key != "height":
        raise _refuse(where, f"{key}.unit = {unit!r}",
                      "only the height map has a physical unit in the schema",
                      "remove it")

    amplitude = raw.get("default_amplitude_mm")
    if amplitude is not None and (not isinstance(amplitude, (int, float))
                                 or amplitude <= 0):
        raise _refuse(where, f"height.default_amplitude_mm is {amplitude!r}",
                      "displacement amplitude is a positive length in mm",
                      "set a positive number, e.g. 3.0")

    return PresetMap(
        key=key, file=file_template, space=space, bit=int(bit), format=fmt,
        slot=str(raw.get("slot", "")), notes=str(raw.get("notes", "")),
        orientation=orientation, mode=mode,
        clamp=(float(clamp[0]), float(clamp[1])) if clamp is not None else None,
        unit=unit,
        default_amplitude_mm=float(amplitude) if amplitude is not None else None,
        strength_default=(float(raw["strength_default"])
                          if raw.get("strength_default") is not None else None),
        glossiness_curve=dict(curve) if curve is not None else None,
    )


def loads(text: str, where: str) -> Preset:
    """Parse a preset from JSON text. Split out so the check script can test it."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise _refuse(where, f"the preset is not valid JSON ({exc})",
                      "a preset that cannot be read cannot be trusted to have "
                      "been read",
                      "fix the JSON, or start from a shipped preset") from None
    if not isinstance(payload, dict):
        raise _refuse(where, "the preset is not a JSON object",
                      "the schema is an object", "see PRESETS.md 1")
    _check_keys(payload, _TOP_KEYS, where)

    name = str(_text(payload, "name", where))
    label = str(_text(payload, "label", where))
    target = str(_text(payload, "target_renderer", where))
    versions = payload.get("renderer_versions_tested", [])
    if not isinstance(versions, list):
        raise _refuse(where, "renderer_versions_tested is not a list",
                      "it is a list of version strings",
                      'write ["6.2", "7.0"], or [] when no version was tested')
    verified = _bool(payload, "verified", where)
    against = _bool(payload, "verified_against_renderer", where)
    default = _bool(payload, "default", where)
    if default and not verified:
        raise _refuse(
            where, "the default preset is marked verified: false",
            "PRESETS.md 1: a preset that has not been exercised cannot be the "
            "one a job reaches for without naming it",
            "either mark it verified (and be able to say what was exercised), or "
            "clear default")

    raw_maps = payload.get("maps")
    if not isinstance(raw_maps, list) or not raw_maps:
        raise _refuse(where, "maps is missing or empty",
                      "a preset with no maps writes no files, and the run would "
                      "report success",
                      "list at least the albedo")
    maps = tuple(_map_entry(raw, f"{where} maps[{i}]")
                 for i, raw in enumerate(raw_maps))
    seen: set[str] = set()
    for entry in maps:
        if entry.key in seen:
            raise _refuse(where, f"two entries both write {entry.key!r}",
                          "the two would race for the same filename and the "
                          "later one would win silently",
                          "keep one entry per key")
        seen.add(entry.key)

    handoff = payload.get("handoff") or {}
    if not isinstance(handoff, dict):
        raise _refuse(where, "handoff is not an object",
                      "it carries a snippet kind and a list of notes",
                      "see PRESETS.md 1")
    _check_keys(handoff, ("snippet", "notes"), f"{where} handoff")
    snippet = str(handoff.get("snippet", "text"))
    if snippet not in _SNIPPETS:
        raise _refuse(f"{where} handoff", f"snippet is {snippet!r}",
                      "the snippet kind selects which file pack writes beside "
                      "the maps",
                      f"use one of: {', '.join(_SNIPPETS)}")
    hand_notes = handoff.get("notes", [])
    if not isinstance(hand_notes, list):
        raise _refuse(f"{where} handoff", "notes is not a list",
                      "it is a list of one-line strings", "write [...]")

    material_defaults = payload.get("material_defaults") or {}
    if not isinstance(material_defaults, dict):
        raise _refuse(where, "material_defaults is not an object",
                      "it is a flat map of renderer-side values",
                      'write {"ior": 1.52}')
    notes = payload.get("notes", [])
    if not isinstance(notes, list):
        raise _refuse(where, "notes is not a list", "it is a list of strings",
                      "write [...]")

    validator = payload.get("validator")
    max_resolution = payload.get("max_resolution")
    if max_resolution is not None:
        if (not isinstance(max_resolution, int) or isinstance(max_resolution, bool)
                or max_resolution < 16):
            raise _refuse(where, f"max_resolution is {max_resolution!r}",
                          "it is a pixel count for the longest edge",
                          "write an integer like 4096, or remove the field")

    warnings: list[str] = []
    for entry in maps:
        if entry.key in PACKED_ORDERS:
            continue
        try:
            spec = spec_for(entry.key)
        except ColourSpaceError:
            continue
        if spec.needs_16 and entry.bit != 16:
            warnings.append(
                f"preset '{name}' writes the {entry.key} map at 8-bit. "
                f"PRESETS.md 3 says 8-bit {entry.key} bands visibly on a large "
                f"flat surface, which is why the registry asks for 16. Carrying "
                f"on because a realtime target may be exactly why you chose it")
    if validator:
        warnings.append(
            f"preset '{name}' names the external validator {validator!r}. This "
            f"build does not run a renderer, so that validation did not happen "
            f"here: what ran is the file-level audit in qc (bit depth, colour "
            f"space, channels, tiling, seam)")

    return Preset(
        name=name, label=label, target_renderer=target,
        versions=tuple(str(v) for v in versions), verified=verified,
        verified_against_renderer=against, default=default, maps=maps,
        material_defaults=dict(material_defaults),
        handoff={"snippet": snippet, "notes": [str(n) for n in hand_notes]},
        validator=str(validator) if validator else None,
        notes=tuple(str(n) for n in notes), warnings=tuple(warnings),
        max_resolution=int(max_resolution) if max_resolution else None,
    )


def default_preset() -> str | None:
    """The name of the preset marked `default: true`, or None."""
    for name in available():
        try:
            preset = load(name)
        except PbrForgeError:
            continue
        if preset.default:
            return name
    return None


def load(name: str) -> Preset:
    """Load a shipped preset by name. Refuses anything that is not a plain name."""
    if not _NAME_RE.match(name or ""):
        raise _refuse(
            f"preset {name!r}", "the name is not a preset name",
            "preset names are matched against a pattern before being joined to "
            "the preset folder, because the name arrives from a parameter block "
            "and '..\\..\\something' is not a preset",
            f"use one of: {', '.join(available()) or '(none shipped)'}")
    path = PRESETS_DIR / f"{name}.json"
    if not path.is_file():
        raise _refuse(
            f"preset {name!r}", "there is no preset with that name",
            "the packing, bit depth and naming for a target genuinely differ, so "
            "a missing preset is not something to fall back from silently",
            f"use one of: {', '.join(available()) or '(none shipped)'}")
    text = path.read_text(encoding="utf-8")
    preset = loads(text, f"preset {name!r} ({path.name})")
    return replace(preset, path=path)


def packed_channels(order: str) -> dict[str, str]:
    """The channel map for a packed order, or `{}` for `separate`."""
    if order == "separate":
        return {}
    try:
        return dict(PACKED_ORDERS[order])
    except KeyError:
        raise _refuse(
            f"channel_order {order!r}", "there is no such packing order",
            "a packing order is a channel assignment a renderer expects; "
            "guessing one would put roughness in the metallic slot",
            f"use one of: {', '.join(PACKED_ORDERS)}, separate") from None