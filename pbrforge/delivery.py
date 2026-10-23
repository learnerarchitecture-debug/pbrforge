"""Delivery to real tools: MaterialX, glTF, USD and a Blender node script.

The library already contains renderer-specific folders.  This module adds the
*interchange* layer beside them, without re-exporting pixels:

    <material>/delivery/
        material.mtlx
        material.gltf        (JSON fragment; external PNG textures)
        material.usda        (UsdPreviewSurface ASCII)
        blender_material.py  (run in Blender's Text Editor)
        textures/            portable copies of the maps
        delivery.json        exactly what was generated and what was not

Every writer declares what it can and cannot express.  MaterialX and USD can
reference separate roughness/metallic maps; glTF 2.0 wants a packed
metallic-roughness texture, so this module packs one from the maps when the
material folder has no glTF export.  If a map is absent, the generated file says
so in ``delivery.json`` and leaves the corresponding input disconnected rather
than inventing a texture.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import __version__
from .core import png
from .core.errors import PbrForgeError
from .export import manifest
from .setdoctor import classify

ROLES = ("albedo", "normal", "roughness", "metallic", "ao", "height",
         "opacity", "orm")
_DISPLAY = {
    "albedo": "Albedo", "normal": "Normal", "roughness": "Roughness",
    "metallic": "Metallic", "ao": "Ambient Occlusion", "height": "Height",
    "opacity": "Opacity", "orm": "Packed ORM",
}


def _candidate_maps(folder: Path) -> dict[str, Path]:
    """Find the best file for each role, preferring an existing export tree."""
    found: dict[str, Path] = {}
    material = manifest.read(folder / "material.json") or {}
    for key in ("outputs", "masters"):
        values = material.get(key) or {}
        if not isinstance(values, dict):
            continue
        for role, rel in values.items():
            path = folder / str(rel)
            if role in ROLES and path.is_file() and role not in found:
                found[role] = path
    # A generic export is not always what the delivery caller wants.  Scan the
    # export tree by role, but do not let a packed ORM hide separate maps.
    for sub in ("maps",):
        base = folder / sub
        if base.is_dir():
            for path in sorted(base.glob("*.png")):
                role = classify(path.name)
                if role in ROLES and role not in found:
                    found[role] = path
    for export in sorted(folder.glob("export/*")):
        if not export.is_dir():
            continue
        for path in sorted(export.glob("*.png")):
            role = classify(path.name)
            if role in ROLES and role not in found:
                found[role] = path
    # Flat folders are the Shape Lab / Set Doctor output shape, and are also how
    # people hand PBRForge a texture set that it did not generate.  Accept them
    # after the manifest and export trees so a finished material still prefers
    # its own provenance.
    for path in sorted(folder.glob("*.png")):
        role = classify(path.name)
        if role in ROLES and role not in found:
            found[role] = path
    # maps/* is usually the master set; prefer it over an old renderer export.
    return found


def _copy_textures(folder: Path, out: Path) -> dict[str, Path]:
    found = _candidate_maps(folder)
    textures = out / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    for role, source in found.items():
        target = textures / f"{role}.png"
        shutil.copy2(source, target)
        copied[role] = target
    return copied


def _sample8(image: png.Image, x: int, y: int) -> int:
    value = image.sample(x, y)[0]
    if image.bit_depth == 16:
        value = round(value * 255 / 65535)
    return max(0, min(255, int(value)))


def _write_metallic_roughness(textures: dict[str, Path]) -> Path | None:
    """Create a glTF metallicRoughness (G=roughness, B=metallic) when needed."""
    if "orm" in textures:
        return textures["orm"]
    if "roughness" not in textures and "metallic" not in textures:
        return None
    rough = png.read(textures["roughness"]) if "roughness" in textures else None
    metal = png.read(textures["metallic"]) if "metallic" in textures else None
    base = rough or metal
    assert base is not None
    width, height = base.width, base.height
    data = bytearray(width * height * 3)
    for index in range(width * height):
        x, y = index % width, index // width
        r = 0
        g = 128
        b = 0
        if rough is not None:
            if (rough.width, rough.height) != (width, height):
                raise PbrForgeError("roughness and metallic maps have different sizes")
            g = _sample8(rough, x, y)
        if metal is not None:
            if (metal.width, metal.height) != (width, height):
                raise PbrForgeError("roughness and metallic maps have different sizes")
            b = _sample8(metal, x, y)
        data[index * 3:index * 3 + 3] = bytes((r, g, b))
    target = textures.get("roughness") or textures.get("metallic")
    assert target is not None
    target = target.parent / "metallicRoughness.png"
    png.write(target, png.Image(width, height, 3, 8, bytes(data)))
    return target


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_mtlx(folder: Path, out: Path, name: str, textures: dict[str, Path]) -> Path:
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<materialx version="1.38">']
    for role, path in textures.items():
        if role in ("albedo", "normal", "roughness", "metallic", "ao", "height"):
            typ = "color3" if role == "albedo" else ("vector3" if role == "normal" else "float")
            rows.append(f'  <image name="{role}" type="{typ}" file="{_xml_escape(_rel(path, out))}"/>')
    rows.append(f'  <standard_surface name="{_xml_escape(name)}_surface" base="1.0">')
    if "albedo" in textures:
        rows.append('    <input name="base_color" type="color3" nodename="albedo"/>')
    if "metallic" in textures:
        rows.append('    <input name="metalness" type="float" nodename="metallic"/>')
    if "roughness" in textures:
        rows.append('    <input name="roughness" type="float" nodename="roughness"/>')
    if "normal" in textures:
        rows.append('    <input name="normal" type="vector3" nodename="normal"/>')
    rows.append("  </standard_surface>")
    rows.append(f'  <surfacematerial name="{_xml_escape(name)}" type="material">')
    rows.append(f'    <input name="surfaceshader" type="surfaceshader" nodename="{_xml_escape(name)}_surface"/>')
    rows.append("  </surfacematerial>")
    rows.append("</materialx>")
    target = out / "material.mtlx"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return target


def write_gltf(folder: Path, out: Path, name: str, textures: dict[str, Path]) -> tuple[Path, list[str]]:
    notes: list[str] = []
    packed = _write_metallic_roughness(textures)
    if packed is not None and "orm" not in textures:
        textures["metallicRoughness"] = packed
        notes.append("packed roughness/metallic into textures/metallicRoughness.png for glTF")
    images: list[dict[str, Any]] = []
    image_index: dict[str, int] = {}

    def image(path: Path | None) -> int | None:
        if path is None:
            return None
        key = str(path)
        if key not in image_index:
            image_index[key] = len(images)
            images.append({"uri": _rel(path, out)})
        return image_index[key]

    base_index = image(textures.get("albedo"))
    mr_index = image(packed)
    normal_index = image(textures.get("normal"))
    ao_index = image(textures.get("ao"))
    textures_json: list[dict[str, Any]] = []
    texture_index: dict[int, int] = {}

    def texture(source: int | None) -> int | None:
        if source is None:
            return None
        if source not in texture_index:
            texture_index[source] = len(textures_json)
            textures_json.append({"sampler": 0, "source": source})
        return texture_index[source]

    material: dict[str, Any] = {"name": name, "doubleSided": False}
    pbr: dict[str, Any] = {"metallicFactor": 1.0, "roughnessFactor": 1.0}
    if base_index is not None:
        pbr["baseColorTexture"] = {"index": texture(base_index)}
    if mr_index is not None:
        pbr["metallicRoughnessTexture"] = {"index": texture(mr_index)}
    else:
        notes.append("no roughness or metallic map; glTF material is untextured")
    material["pbrMetallicRoughness"] = pbr
    if normal_index is not None:
        material["normalTexture"] = {"index": texture(normal_index)}
    if ao_index is not None:
        material["occlusionTexture"] = {"index": texture(ao_index)}
    payload = {
        "asset": {"version": "2.0", "generator": f"PBRForge {__version__}"},
        "materials": [material],
        "textures": textures_json,
        "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
    }
    target = out / "material.gltf"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target, notes


def write_usda(folder: Path, out: Path, name: str, textures: dict[str, Path]) -> Path:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name) or "material"
    rows = ["#usda 1.0", "(", f'    defaultPrim = "{safe}"', ")", "",
            f'def Material "{safe}"', "{",
            '    def Shader "PreviewSurface"', "    {",
            '        uniform token info:id = "UsdPreviewSurface"']
    if "albedo" in textures:
        rows.append(f'        color3f inputs:diffuseColor.connect = </{safe}/Albedo.outputs:rgb>')
    if "roughness" in textures:
        rows.append(f'        float inputs:roughness.connect = </{safe}/Roughness.outputs:r>')
    if "metallic" in textures:
        rows.append(f'        float inputs:metallic.connect = </{safe}/Metallic.outputs:r>')
    if "normal" in textures:
        rows.append(f'        float3 inputs:normal.connect = </{safe}/Normal.outputs:rgb>')
    rows.append("    }")
    for role, path in textures.items():
        if role not in ("albedo", "roughness", "metallic", "normal", "height", "ao"):
            continue
        prim = {"albedo": "Albedo", "roughness": "Roughness", "metallic": "Metallic",
                "normal": "Normal", "height": "Height", "ao": "AO"}[role]
        color = "sRGB" if role == "albedo" else "raw"
        rows += [f'    def Shader "{prim}"', "    {",
                 '        uniform token info:id = "UsdUVTexture"',
                 f'        asset inputs:file = @{_rel(path, out)}@',
                 f'        token inputs:sourceColorSpace = "{color}"',
                 f'        float2 inputs:st.connect = </{safe}/UV.outputs:result>',
                 '        float4 inputs:scale = (1, 1, 1, 1)',
                 f'        {"float3" if role != "albedo" else "color3f"} outputs:rgb',
                 "    }"]
    rows += ['    def Shader "UV"', "    {",
             '        uniform token info:id = "UsdPrimvarReader_float2"',
             '        token inputs:varname = "st"',
             '        float2 outputs:result', "    }", "}"]
    target = out / "material.usda"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return target


def write_blender(folder: Path, out: Path, name: str, textures: dict[str, Path]) -> Path:
    texture_dir = out / "textures"
    rows = [
        "# PBRForge Blender material builder",
        "# Run this from Blender's Text Editor. The texture folder defaults to the",
        "# delivery/textures folder beside this script; if you move the material,",
        "# set PBRFORGE_TEXTURE_DIR before running.",
        "import bpy, os",
        "",
        f'MATERIAL = {name!r}',
        f'NAME = {name!r}',
        f'TEXTURE_DIR = os.environ.get("PBRFORGE_TEXTURE_DIR", r"{texture_dir}")',
        "",
        "def image(role):",
        '    path = os.path.join(TEXTURE_DIR, role + ".png")',
        "    if not os.path.exists(path):",
        "        return None",
        "    img = bpy.data.images.load(path, check_existing=True)",
        "    if role in {'normal', 'roughness', 'metallic', 'ao', 'height'}:",
        "        img.colorspace_settings.name = 'Non-Color'",
        "    return img",
        "",
        "mat = bpy.data.materials.new(NAME)",
        "mat.use_nodes = True",
        "nodes = mat.node_tree.nodes",
        "links = mat.node_tree.links",
        "bsdf = nodes.get('Principled BSDF')",
        "if bsdf is None:",
        "    raise RuntimeError('this Blender build has no Principled BSDF')",
        "albedo = image('albedo')",
        "if albedo:",
        "    node = nodes.new('ShaderNodeTexImage'); node.image = albedo; node.label = 'Albedo'",
        "    links.new(node.outputs['Color'], bsdf.inputs['Base Color'])",
        "rough = image('roughness')",
        "if rough:",
        "    node = nodes.new('ShaderNodeTexImage'); node.image = rough; node.label = 'Roughness'",
        "    links.new(node.outputs['Color'], bsdf.inputs['Roughness'])",
        "metal = image('metallic')",
        "if metal:",
        "    node = nodes.new('ShaderNodeTexImage'); node.image = metal; node.label = 'Metallic'",
        "    links.new(node.outputs['Color'], bsdf.inputs['Metallic'])",
        "normal = image('normal')",
        "if normal:",
        "    tex = nodes.new('ShaderNodeTexImage'); tex.image = normal; tex.label = 'Normal'",
        "    nm = nodes.new('ShaderNodeNormalMap')",
        "    links.new(tex.outputs['Color'], nm.inputs['Color'])",
        "    links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])",
        "elif image('height'):",
        "    tex = nodes.new('ShaderNodeTexImage'); tex.image = image('height'); tex.label = 'Height'",
        "    bump = nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.35",
        "    links.new(tex.outputs['Color'], bump.inputs['Height'])",
        "    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])",
        "",
        "# Assign to the active object when possible.",
        "obj = bpy.context.active_object",
        "if obj is not None:",
        "    obj.data.materials.append(mat)",
        "",
    ]
    target = out / "blender_material.py"
    target.write_text("\n".join(rows), encoding="utf-8", newline="\n")
    return target


def generate(material_folder: Path, *, output: Path | None = None,
             name: str | None = None) -> dict[str, Any]:
    folder = Path(material_folder)
    if not folder.is_dir():
        raise PbrForgeError(f"material folder does not exist: {folder}")
    metadata = manifest.read(folder / "material.json") or {}
    display = name or str(metadata.get("material") or folder.name)
    out = Path(output) if output else folder / "delivery"
    out.mkdir(parents=True, exist_ok=True)
    textures = _copy_textures(folder, out)
    if not textures:
        raise PbrForgeError(
            "no PNG maps were found in this material folder.\n"
            "  why      delivery builds interchange files around the existing maps\n"
            "  fix      point at a finished PBRForge library material")
    notes: list[str] = []
    mtlx = write_mtlx(folder, out, display, textures)
    gltf, gltf_notes = write_gltf(folder, out, display, textures)
    notes.extend(gltf_notes)
    usda = write_usda(folder, out, display, textures)
    blender = write_blender(folder, out, display, textures)
    payload = {
        "app": "pbrforge", "version": __version__,
        "material": display, "folder": str(folder),
        "textures": {role: _rel(path, out) for role, path in textures.items()},
        "files": [mtlx.name, gltf.name, usda.name, blender.name],
        "notes": notes + [
            "MaterialX and USD are interchange files, not a renderer certification.",
            "glTF is a fragment with external PNG images; embed them if your pipeline requires a .glb.",
            "The Blender script is a starting node graph; review it before production use.",
            *(["A packed ORM/MRA map was used as-is; verify its channel order for your renderer."]
              if "orm" in textures else []),
        ],
        "missing": [role for role in ("albedo", "normal", "roughness", "metallic")
                    if role not in textures],
    }
    (out / "delivery.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (out / "README.txt").write_text(
        f"PBRForge delivery\n================\n\n"
        f"material: {display}\n"
        f"textures: {', '.join(sorted(textures))}\n\n"
        "material.mtlx   MaterialX 1.38 document\n"
        "material.gltf   glTF 2.0 JSON fragment (external textures)\n"
        "material.usda   USD UsdPreviewSurface ASCII\n"
        "blender_material.py  run in Blender's Text Editor\n\n"
        "These files reference textures/ relatively. Keep the delivery folder\n"
        "together, or update the references if you move the maps.\n",
        encoding="utf-8", newline="\n")
    return payload


def describe(payload: dict[str, Any]) -> str:
    lines = [f"  material  {payload.get('material')}",
             f"  folder    {payload.get('folder')}",
             f"  textures  {', '.join(sorted(payload.get('textures') or {})) or '(none)'}",
             "  files     " + ", ".join(payload.get("files") or [])]
    for note in payload.get("notes") or []:
        lines.append(f"  note      {note}")
    return "\n".join(lines)
