"""PBR Set Doctor: inspect and repair an existing texture set.

This is the feature that pays for itself on day one with an old library.  It
does not guess what a texture means from its pixels; it classifies the *filename*
against a conservative vocabulary, reports what is missing, and only changes a
file when the user names the operation:

    scan       read dimensions, channels, bit depth and the inferred role
    normal     flip a DirectX normal set to OpenGL (or the reverse)
    invert     convert glossiness to roughness (or the reverse)
    repack     build ORM / MRA from separate AO, roughness and metallic maps
    rename     copy into a clean, portable folder with canonical names

Every action writes to a new folder; the source folder is never modified.  A
tool that "cleans" a library by destroying it is not a tool anyone should run.

PNG only, matching the portable imaging path.  TIFF/JPG sets are reported as
unsupported with the conversion fix rather than silently skipped.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import png
from .core.errors import LimitError, PbrForgeError
from .security import hash_file

# Ordered, most specific first.  A filename can contain "normal" in a project
# name, so tokens are matched with separators rather than by a loose substring.
_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("normal", ("normal", "normalgl", "normaldx", "nrm", "norm")),
    ("roughness", ("roughness", "rough", "rgh", "glossiness", "gloss", "smoothness")),
    ("metallic", ("metallic", "metalness", "metal", "mtl")),
    ("ao", ("ambientocclusion", "occlusion", "ao")),
    ("height", ("height", "displacement", "disp", "bump")),
    ("opacity", ("opacity", "alpha", "mask")),
    ("albedo", ("albedo", "basecolor", "base_color", "diffuse", "color", "colour", "diff")),
    ("orm", ("orm", "rma", "mra", "maskmap")),
)
_ROLE_LABEL = {
    "albedo": "Albedo / base colour", "normal": "Normal", "roughness": "Roughness",
    "metallic": "Metallic", "ao": "Ambient occlusion", "height": "Height / bump",
    "opacity": "Opacity / alpha", "orm": "Packed ORM/MRA", "unknown": "Unknown",
}
_SUPPORTED = (".png",)
_IMAGE_EXT = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".webp", ".exr")
# These are generated views, not source maps.  Scanning a finished library
# material otherwise reports every preview as a duplicate of its master.
_SKIP_DIRS = {"previews", "delivery", "doctor", "cache", "config", "textures",
              "__pycache__", ".git", ".pbrforge-cache"}


def _in_derived(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return any(part.lower() in _SKIP_DIRS for part in parts)


@dataclass
class MapEntry:
    path: Path
    role: str
    width: int
    height: int
    channels: int
    bit_depth: int
    sha256: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class SetReport:
    folder: Path
    maps: list[MapEntry]
    missing: list[str]
    duplicates: list[str]
    notes: list[str] = field(default_factory=list)
    generated: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder": str(self.folder), "generated": self.generated,
            "maps": [{"file": m.path.name, "role": m.role,
                      "role_label": _ROLE_LABEL.get(m.role, m.role),
                      "width": m.width, "height": m.height,
                      "channels": m.channels, "bit_depth": m.bit_depth,
                      "sha256": m.sha256, "problems": m.problems}
                     for m in self.maps],
            "missing": self.missing, "duplicates": self.duplicates,
            "notes": self.notes,
            "complete": not self.missing,
        }

    def text(self) -> str:
        lines = [f"PBRForge Set Doctor", f"folder: {self.folder}", ""]
        if not self.maps:
            lines.append("No supported images found.")
        for entry in self.maps:
            flag = " !" if entry.problems else "  "
            lines.append(f"{flag} {entry.path.name:<42} {_ROLE_LABEL.get(entry.role, entry.role):<22} "
                         f"{entry.width}x{entry.height} {entry.channels}ch {entry.bit_depth}-bit")
            for problem in entry.problems:
                lines.append(f"     - {problem}")
        if self.missing:
            lines.extend(["", "Missing roles: " + ", ".join(self.missing)])
        if self.duplicates:
            lines.extend(["", "Duplicate roles: " + ", ".join(self.duplicates)])
        for note in self.notes:
            lines.append(f"note: {note}")
        lines.append("")
        return "\n".join(lines)


def _clean_tokens(name: str) -> list[str]:
    stem = Path(name).stem.lower()
    stem = re.sub(r"([a-z])([A-Z])", r"\1_\2", stem)
    return [token for token in re.split(r"[^a-z0-9]+", stem) if token]


def classify(name: str) -> str:
    tokens = _clean_tokens(name)
    joined = "_".join(tokens)
    # A token such as "normal" in "normalized_wall" is a trap; exact token
    # matching is used, with a few common glued forms handled explicitly.
    for role, patterns in _ROLE_PATTERNS:
        for pattern in patterns:
            if pattern in tokens or joined.endswith("_" + pattern) or joined == pattern:
                return role
    if "normal" in joined and ("gl" in tokens or "dx" in tokens):
        return "normal"
    return "unknown"


def scan(folder: Path) -> SetReport:
    folder = Path(folder)
    if not folder.is_dir():
        raise PbrForgeError(f"set folder does not exist: {folder}")
    entries: list[MapEntry] = []
    notes: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or _in_derived(path, folder):
            continue
        if path.suffix.lower() not in _IMAGE_EXT:
            continue
        if path.suffix.lower() not in _SUPPORTED:
            notes.append(f"{path.name}: {path.suffix} is not supported by the portable "
                         f"reader; convert it to PNG first")
            continue
        role = classify(path.name)
        try:
            image = png.read(path)
        except png.PngError as exc:
            entries.append(MapEntry(path, role, 0, 0, 0, 0, problems=[str(exc)]))
            continue
        problems: list[str] = []
        if image.width != image.height:
            problems.append("not square; most material sets expect a square tile")
        if image.width & (image.width - 1) or image.height & (image.height - 1):
            problems.append("not a power-of-two size")
        if image.width < 128:
            problems.append("below 128 px; too small for a useful material")
        if role == "normal" and image.channels < 3:
            problems.append("normal map is not RGB")
        if role in ("roughness", "metallic", "ao", "height", "opacity") and image.channels != 1:
            problems.append("data map is not single-channel; a packed RGB file may be misinterpreted")
        entries.append(MapEntry(path, role, image.width, image.height,
                                image.channels, image.bit_depth,
                                sha256=hash_file(path), problems=problems))
    roles: dict[str, list[MapEntry]] = {}
    for entry in entries:
        roles.setdefault(entry.role, []).append(entry)
    missing = [role for role in ("albedo", "normal", "roughness") if role not in roles]
    duplicates = [role for role, items in roles.items() if len(items) > 1 and role != "unknown"]
    if not any(role in roles for role in ("albedo", "orm")):
        notes.append("no albedo or packed ORM map was recognized")
    return SetReport(folder=folder, maps=entries, missing=missing,
                     duplicates=duplicates, notes=notes)


def _write_report(report: SetReport, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "set-report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    (out / "set-report.txt").write_text(report.text(), encoding="utf-8", newline="\n")
    return path


def _copy_source(folder: Path, out: Path) -> None:
    for path in sorted(folder.rglob("*")):
        if (not path.is_file() or path.suffix.lower() not in _SUPPORTED
                or _in_derived(path, folder)):
            continue
        rel = path.relative_to(folder)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _read(path: Path) -> png.Image:
    return png.read(path)


def _replace_channel(image: png.Image, channel: int, value: int) -> png.Image:
    stride = image.channels * (2 if image.bit_depth == 16 else 1)
    raw = bytearray(image.data)
    if image.bit_depth == 16:
        for offset in range(channel * 2, len(raw), stride):
            raw[offset:offset + 2] = int(value).to_bytes(2, "big")
    else:
        for offset in range(channel, len(raw), stride):
            raw[offset] = max(0, min(255, value))
    return png.Image(image.width, image.height, image.channels, image.bit_depth, bytes(raw))


def flip_normal(path: Path, out: Path, *, to: str = "opengl") -> Path:
    image = _read(path)
    if image.channels < 3:
        raise PbrForgeError(f"{path.name} is not an RGB normal map")
    target = out / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    maxv = 65535 if image.bit_depth == 16 else 255
    if to not in ("opengl", "directx"):
        raise PbrForgeError("normal orientation must be 'opengl' or 'directx'")
    flipped = png.Image(image.width, image.height, image.channels, image.bit_depth,
                        bytearray(image.data))
    stride = image.channels * (2 if image.bit_depth == 16 else 1)
    raw = bytearray(flipped.data)
    if image.bit_depth == 16:
        for offset in range(2, len(raw), stride):
            raw[offset:offset + 2] = (maxv - int.from_bytes(raw[offset:offset + 2], "big")).to_bytes(2, "big")
    else:
        for offset in range(1, len(raw), stride):
            raw[offset] = 255 - raw[offset]
    png.write(target, png.Image(image.width, image.height, image.channels,
                                image.bit_depth, bytes(raw)))
    return target


def invert_data(path: Path, out: Path) -> Path:
    image = _read(path)
    if image.channels not in (1, 3, 4):
        raise PbrForgeError(f"{path.name} cannot be inverted as a data map")
    maxv = 65535 if image.bit_depth == 16 else 255
    raw = bytearray(image.data)
    if image.bit_depth == 16:
        for offset in range(0, len(raw), 2):
            raw[offset:offset + 2] = (maxv - int.from_bytes(raw[offset:offset + 2], "big")).to_bytes(2, "big")
    else:
        for offset in range(len(raw)):
            raw[offset] = 255 - raw[offset]
    target = out / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    png.write(target, png.Image(image.width, image.height, image.channels,
                                image.bit_depth, bytes(raw)))
    return target


def _grey_values(path: Path) -> tuple[list[int], int, int, int]:
    image = _read(path)
    if image.channels != 1:
        raise PbrForgeError(f"{path.name} is not single-channel; cannot use it as a data map")
    maxv = 65535 if image.bit_depth == 16 else 255
    if image.bit_depth == 16:
        import struct
        values = list(struct.unpack(f">{image.width * image.height}H", image.data))
    else:
        values = list(image.data)
    return values, image.width, image.height, maxv


def repack_orm(folder: Path, out: Path, *, preset: str = "orm") -> dict[str, Any]:
    """Build ORM (R=AO, G=roughness, B=metallic) or MRA (R=metal, G=rough, B=AO)."""
    if preset not in ("orm", "mra"):
        raise PbrForgeError("packed preset must be 'orm' or 'mra'")
    report = scan(folder)
    by_role: dict[str, Path] = {}
    for entry in report.maps:
        if entry.role in ("albedo", "normal", "roughness", "metallic", "ao", "height",
                          "opacity", "orm"):
            by_role.setdefault(entry.role, entry.path)
    needed = ("ao", "roughness", "metallic")
    missing = [role for role in needed if role not in by_role]
    if missing:
        raise PbrForgeError(
            "cannot repack: missing " + ", ".join(missing) + "\n"
            "  why      an ORM/MRA file needs all three channels\n"
            "  fix      pass a folder that contains AO, roughness and metallic maps")
    arrays: dict[str, tuple[list[int], int]] = {}
    size = None
    for role in needed:
        values, width, height, depth = _grey_values(by_role[role])
        if size is None:
            size = (width, height)
        elif (width, height) != size:
            raise PbrForgeError(
                f"{by_role[role].name} is {width}x{height}, expected {size[0]}x{size[1]}\n"
                "  why      resampling data maps here would change their values\n"
                "  fix      resize the set to one resolution first")
        arrays[role] = (values, depth)
    width, height = size  # type: ignore[misc]
    data = bytearray(width * height * 3)
    order = ("ao", "roughness", "metallic") if preset == "orm" else ("metallic", "roughness", "ao")
    for index in range(width * height):
        for channel, role in enumerate(order):
            values, depth = arrays[role]
            value = values[index]
            # Normalise against the source depth, not against the value's
            # magnitude: a dark 16-bit sample is still a 16-bit sample.
            data[index * 3 + channel] = max(0, min(255, round(value * 255 / depth)))
    target = out / f"PBRForge-{preset.upper()}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    png.write(target, png.Image(width, height, 3, 8, bytes(data)))
    return {"output": str(target), "preset": preset, "width": width,
            "height": height, "sources": {role: str(by_role[role]) for role in needed}}


def repair(folder: Path, out: Path, *, normal_to: str = "", invert: str = "",
           repack: str = "", rename: bool = False, overwrite: bool = False) -> dict[str, Any]:
    folder = Path(folder)
    out = Path(out)
    # The CLI uses the user's vocabulary ("glossiness", "metalness"), while the
    # scanner stores the canonical channel role.  Normalise before comparing.
    invert_role = {"glossiness": "roughness", "metalness": "metallic"}.get(invert, invert)
    if invert and invert_role not in ("roughness", "metallic"):
        raise PbrForgeError("invert must target roughness/glossiness or metallic/metalness")
    report = scan(folder)
    roles = {entry.role for entry in report.maps}
    # Preflight every requested action before creating or copying anything.  A
    # failed repack must not leave a half-repaired folder that looks successful.
    if normal_to and "normal" not in roles:
        raise PbrForgeError("cannot flip normals: this folder has no normal map")
    if invert_role and invert_role not in roles:
        raise PbrForgeError(f"cannot invert: this folder has no {invert_role} map")
    if repack:
        needed = {"ao", "roughness", "metallic"} - roles
        if needed:
            raise PbrForgeError(
                "cannot repack: missing " + ", ".join(sorted(needed)) +
                "; an ORM/MRA file needs AO, roughness and metallic maps")
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise PbrForgeError(f"{out} is not empty; use --overwrite to replace its contents")
    out.mkdir(parents=True, exist_ok=True)
    _copy_source(folder, out)
    actions: list[str] = []
    for entry in report.maps:
        if entry.role == "normal" and normal_to:
            flip_normal(entry.path, out, to=normal_to)
            actions.append(f"{entry.path.name}: green channel flipped for {normal_to}")
        if invert_role and entry.role == invert_role:
            invert_data(entry.path, out)
            actions.append(f"{entry.path.name}: values inverted")
    if repack:
        packed = repack_orm(folder, out, preset=repack)
        actions.append(f"repacked {repack.upper()}: {packed['output']}")
    if rename:
        for entry in report.maps:
            if entry.role == "unknown":
                continue
            canonical = f"PBRForge-{entry.role}.png"
            target = out / canonical
            if target.exists():
                continue
            # Copy the repaired copy in `out`, not the original.  Otherwise a
            # flipped normal in the output folder would be replaced by the
            # untouched original under a new name.
            repaired = out / entry.path.relative_to(folder)
            shutil.copy2(repaired if repaired.is_file() else entry.path, target)
            actions.append(f"{entry.path.name} -> {canonical}")
    _write_report(report, out)
    (out / "doctor-actions.txt").write_text("\n".join(actions) + "\n",
                                             encoding="utf-8", newline="\n")
    return {"folder": str(folder), "output": str(out), "report": report.to_dict(),
            "actions": actions}
