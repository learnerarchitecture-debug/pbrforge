"""The handoff: what to do with the files once they exist.

PRESETS.md 8 calls this the "wire it into" panel. The value is not the prose -- it
is the *table*, because the two things every hand-written texture pipeline gets
wrong are the same two things: which file goes in which slot, and which maps must
not have gamma applied. Both are already stated by the preset (it carries a `slot`
and a `notes` field per map), so the sheet is assembled rather than written.

**What this is not.** Two of the six snippet kinds are executable-looking
(`blender` is Python, `gltf` is a JSON fragment) and four are checklists. None of
them has been *run*: this build ships no 3ds Max, no Blender, no Unity and no
Unreal, and a snippet that claims to have been tested when it has not is the exact
thing this project refuses to do. Every file therefore opens with that statement,
and a preset that names an external validator gets it repeated: the validation
that happened is the file-level audit in `qc`.

The glTF fragment is the one that can be checked mechanically rather than
rhetorically -- it is parsed as JSON by the check script, and its image URIs are
asserted to point at files that exist -- so it is the one that says what it is
without an apology.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .presets import Preset, PresetMap

__all__ = ["EXTENSIONS", "filename", "render", "write"]

#: Snippet kind -> (extension, whether it is code rather than a checklist).
EXTENSIONS: dict[str, tuple[str, bool]] = {
    "blender": (".py", True),
    "gltf": (".json", True),
    "maxscript": (".txt", False),
    "unity": (".txt", False),
    "unreal": (".txt", False),
    "text": (".txt", False),
}

_GAMMA_NOTE = {
    "srgb": "the file is sRGB-encoded; leave the renderer's colour management on",
    "data": "DIGITAL/OVERRIDE: apply no transfer function to this file",
}


def filename(preset: Preset) -> str:
    """`handoff.<ext>`, chosen by the snippet kind the preset declares."""
    snippet = str(preset.handoff.get("snippet", "text"))
    ext, _ = EXTENSIONS.get(snippet, (".txt", False))
    return f"handoff{ext}"


def _stamp(version: str, preset: Preset, material: str) -> list[str]:
    tested = ", ".join(preset.versions) if preset.versions else "none"
    lines = [
        f"PBRForge {version} -- {preset.label} handoff",
        f"material: {material}    generated: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC)",
        "",
        ("NOT RUN BY PBRFORGE. Nothing below was executed against a renderer: "
         "this build ships none. The slot names and settings come from the "
         "preset, which follows docs/PRESETS.md."),
        f"renderer versions checked against this preset: {tested}",
    ]
    if not preset.verified:
        lines.append(
            "this preset is marked verified: false -- its packing and naming "
            "follow the cookbook, and the file-level audit in qc is what has "
            "actually been run on the output")
    if preset.validator:
        lines.append(f"external validator named but NOT run here: "
                     f"{preset.validator}")
    lines.append("")
    return lines


def _table(entries: list[dict[str, Any]], by_kind: dict[str, PresetMap],
           unpacked: list[str] = ()) -> list[str]:
    lines = ["MAPS", "-" * 4]
    width = max(len(str(e["file"])) for e in entries) if entries else 8
    for entry in entries:
        spec = by_kind.get(str(entry["kind"]))
        slot = (spec.slot if spec and spec.slot else "-")
        bits = f"{entry['bit_depth']}-bit"
        gamma = _GAMMA_NOTE.get(str(entry["space"]), "")
        lines.append(f"  {str(entry['file']):<{width}}  ->  {slot:<22} "
                     f"{bits:>6}  {gamma}")
    lines.append("")
    missing = [m for m in by_kind.values() if str(m.key) not in
               {str(e["kind"]) for e in entries}]
    if missing:
        lines.append("Not written in this run (the map set does not carry them): "
                     + ", ".join(sorted({m.key for m in missing})))
        lines.append("")
    if unpacked:
        lines.append(
            "Exists in the masters folder but has no slot in this target: "
            + ", ".join(sorted(unpacked)) + ".")
        lines.append("")
    return lines


def _notes(preset: Preset) -> list[str]:
    notes = list(preset.handoff.get("notes") or [])
    lines: list[str] = []
    if notes:
        lines.append("NOTES FROM THE PRESET")
        lines += [f"  - {note}" for note in notes]
        lines.append("")
    if preset.material_defaults:
        lines.append("MATERIAL DEFAULTS THIS PRESET EXPECTS")
        for key, value in sorted(preset.material_defaults.items()):
            lines.append(f"  {key} = {value}")
        lines.append("")
    return lines


def _physics(size_mm: tuple[float, float] | None, height: PresetMap | None,
             px_per_mm: float | None) -> list[str]:
    lines = ["PHYSICAL SCALE", "-" * 14]
    if size_mm:
        lines.append(f"  tile covers {size_mm[0]:g} x {size_mm[1]:g} mm"
                     + (f"  ({px_per_mm:.3g} px/mm)" if px_per_mm else ""))
    else:
        lines.append("  size_mm was not set for this material, so the tile has "
                     "no stated real-world size and texel density is unknown. A "
                     "renderer that needs a displacement height in mm cannot be "
                     "given one from this export.")
    if height is not None:
        amplitude = height.default_amplitude_mm
        lines.append(
            f"  displacement: 2D, mid-level 0.5"
            + (f", preset default amplitude {amplitude:g} mm" if amplitude else ""))
        if not size_mm:
            lines.append("  the amplitude is in mm and the tile has no mm: set "
                         "size_mm before trusting displacement from this set")
    lines.append("")
    return lines


def _body_blender(entries: list[dict[str, Any]], by_kind: dict[str, PresetMap],
                  material: str) -> list[str]:
    """A Blender 4.x snippet. Written from the stable parts of the API only."""
    lines = [
        "# Paste into Blender's scripting workspace, with the maps in the same",
        "# folder as this file (or edit MAPDIR).",
        "import os",
        "import bpy",
        "",
        'MAPDIR = os.path.dirname(bpy.data.filepath) or "."',
        f'NAME = "{material}"',
        "",
        "mat = bpy.data.materials.get(NAME) or bpy.data.materials.new(NAME)",
        "mat.use_nodes = True",
        'nt = mat.node_tree',
        'bsdf = nt.nodes["Principled BSDF"]',
        "",
        "def image(filename, srgb, non_color=False):",
        "    node = nt.nodes.new('ShaderNodeTexImage')",
        "    node.image = bpy.data.images.load(os.path.join(MAPDIR, filename))",
        "    node.image.colorspace_settings.name = 'sRGB' if srgb else 'Non-Color'",
        "    return node",
        "",
    ]
    def emit(kind: str, target: str, node: str = "") -> None:
        entry = next((e for e in entries if e["kind"] == kind), None)
        if entry is None:
            return
        srgb = "True" if entry["space"] == "srgb" else "False"
        lines.append(f'{node or kind} = image("{entry["file"]}", {srgb})')
        if target:
            lines.append(target)

    emit("albedo", "nt.links.new(albedo.outputs['Color'], bsdf.inputs['Base Color'])")
    emit("roughness",
         "nt.links.new(rough.outputs['Color'], bsdf.inputs['Roughness'])",
         "rough")
    emit("metallic",
         "nt.links.new(metal.outputs['Color'], bsdf.inputs['Metallic'])",
         "metal")
    if any(e["kind"] == "normal" for e in entries):
        strength = by_kind["normal"].strength_default or 1.0
        lines += [
            "nrm = image('%s', False)" % next(e["file"] for e in entries
                                              if e["kind"] == "normal"),
            "nmap = nt.nodes.new('ShaderNodeNormalMap')",
            f"nmap.inputs['Strength'].default_value = {strength:g}",
            "nt.links.new(nrm.outputs['Color'], nmap.inputs['Color'])",
            "nt.links.new(nmap.outputs['Normal'], bsdf.inputs['Normal'])",
        ]
    if any(e["kind"] == "height" for e in entries):
        lines += [
            "hgt = image('%s', False)" % next(e["file"] for e in entries
                                              if e["kind"] == "height"),
            "disp = nt.nodes.new('ShaderNodeDisplacement')",
            "nt.links.new(hgt.outputs['Color'], disp.inputs['Height'])",
            "nt.links.new(disp.outputs['Displacement'], "
            "nt.nodes['Material Output'].inputs['Displacement'])",
            "mat.cycles.displacement_method = 'BOTH'",
        ]
    alpha = next((e for e in entries if e["kind"] == "opacity"), None)
    if alpha is not None:
        lines += [
            f"opac = image(\"{alpha['file']}\", False)",
            "nt.links.new(opac.outputs['Color'], bsdf.inputs['Alpha'])",
            "mat.blend_method = 'CLIP'",
        ]
    lines += [
        "",
        "# Emission, AO and cavity are deliberately not wired: Blender's",
        "# Principled BSDF has no AO input, and baking AO into Base Color is a",
        "# decision for the artist, not the exporter.",
    ]
    return lines


def _body_gltf(entries: list[dict[str, Any]], material: str,
               orientation: str) -> list[str]:
    """A glTF 2.0 material fragment. Parsed by the check script as JSON."""
    import json

    images = [{"uri": str(e["file"]), "mimeType": "image/png"
               if str(e["file"]).endswith(".png") else "image/tiff"}
              for e in entries]
    index = {str(e["kind"]): i for i, e in enumerate(entries)}
    material_json: dict[str, Any] = {"name": material}
    if "albedo" in index:
        material_json["pbrMetallicRoughness"] = {
            "baseColorTexture": {"index": index["albedo"]}}
    if "gltf" in index:
        material_json.setdefault("pbrMetallicRoughness", {})[
            "metallicRoughnessTexture"] = {"index": index["gltf"]}
    if "normal" in index:
        material_json["normalTexture"] = {"index": index["normal"],
                                          "scale": 1.0}
    if "ao" in index:
        material_json["occlusionTexture"] = {"index": index["ao"],
                                             "strength": 1.0}
    if "emissive" in index:
        material_json["emissiveTexture"] = {"index": index["emissive"]}
    fragment = {
        "materials": [material_json],
        "images": images,
        "_pbrforge": {
            "note": ("This is a material fragment, not a complete glTF asset: "
                     "textures must be registered in the asset's own arrays and "
                     "the index numbers here refer to the list below."),
            "normal_space": orientation,
            "generated_by": "PBRForge",
        },
    }
    return json.dumps(fragment, indent=2).splitlines()


def _body_sheet(preset: Preset, entries: list[dict[str, Any]]) -> list[str]:
    """The checklist body used by every non-code target."""
    lines = [
        "WIRING",
        "-" * 6,
        "  1. Create the material and set the values listed under MATERIAL "
        "DEFAULTS.",
        "  2. Drop each file into the slot named above. The files are named so",
        "     that the slot is obvious; the table is the authority, not the name.",
        "  3. For every row marked DIGITAL/OVERRIDE, force the bitmap to be read",
        "     as raw data. This is the single most common cause of a material",
        "     that looks almost right: a normal map read through sRGB.",
        "  4. Check the tiling by rendering a 3x3 tiled plane at 1 m and looking",
        "     at the seams under grazing light.",
        "  5. If displacement is in the set, start at the preset's amplitude and",
        "     scale it against the tile's real size, not by eye.",
        "",
    ]
    if preset.handoff.get("snippet") == "maxscript":
        lines += [
            "3DS MAX PROPERTY NAMES (from the cookbook; check them in your",
            "version before trusting them in a script):",
            "  VRayMtl.diffuseMap, .roughnessMap, .metalnessMap, .bumpMap",
            "  (with .bump_map_type = normal and normal_map set), .displacementMap",
            "  CoronaMtl: .baseTexmap, .roughnessTexmap, .metalnessTexmap, "
            ".normalsTexmap",
            "",
        ]
    if preset.handoff.get("snippet") == "unreal":
        lines += [
            "UNREAL 5",
            "  Import with sRGB off for every data map; Compression = Normalmap",
            "  for the normal, and Masks for packed maps. Metallic and roughness",
            "  feed a Material Instance, not the parent material.",
            "",
        ]
    if preset.handoff.get("snippet") == "unity":
        lines += [
            "UNITY HDRP / URP",
            "  Assign the Mask Map to the Lit shader's Mask Map slot: R metallic,",
            "  G occlusion, B detail mask, A smoothness. Use a Texture Importer",
            "  preset that clears sRGB for data maps, or every value is wrong",
            "  after the first import.",
            "",
        ]
    return lines


def render(preset: Preset, entries: list[dict[str, Any]], *, material: str,
           version: str, size_mm: tuple[float, float] | None = None,
           px_per_mm: float | None = None,
           orientation: str = "opengl",
           unpacked: Iterable[str] = (),
           extra_notes: Iterable[str] = ()) -> str:
    """Build the sheet. `entries` is `ctx.physical["packed"]`, in write order."""
    by_kind = {m.key: m for m in preset.maps}
    snippet = str(preset.handoff.get("snippet", "text"))
    if snippet == "gltf":
        # glTF handoff is pure JSON for machine parsing
        return "\n".join(_body_gltf(entries, material, orientation)) + "\n"
    lines = _stamp(version, preset, material)
    lines += _table(entries, by_kind, list(unpacked))
    lines += _physics(size_mm, by_kind.get("height"), px_per_mm)
    lines += _notes(preset)
    for note in extra_notes:
        lines.append(str(note))
    if list(extra_notes):
        lines.append("")
    if snippet == "blender":
        lines += _body_blender(entries, by_kind, material)
    else:
        lines += _body_sheet(preset, entries)
    return "\n".join(lines) + "\n"


def write(path: Path, preset: Preset, entries: list[dict[str, Any]], *,
          material: str, version: str,
          size_mm: tuple[float, float] | None = None,
          px_per_mm: float | None = None, orientation: str = "opengl",
          unpacked: Iterable[str] = (),
          extra_notes: Iterable[str] = ()) -> Path:
    """Write the handoff beside the maps. Returns the path."""
    text = render(preset, entries, material=material, version=version,
                  size_mm=size_mm, px_per_mm=px_per_mm,
                  orientation=orientation, unpacked=unpacked,
                  extra_notes=extra_notes)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path