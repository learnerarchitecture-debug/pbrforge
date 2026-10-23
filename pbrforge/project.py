"""Editable material projects: a source plus an ordered, non-destructive stack.

This is intentionally a **stack**, not a general node graph.  A project is a
small JSON document plus the source PNG.  Layers are applied in order:

    source -> offset -> surface -> weather -> grade -> pipeline

The pipeline (height, normal, AO, roughness, metallic, QC and packing) still
owns every derived map.  A project only changes the flat-lit source image, which
is the one thing a photograph cannot undo afterwards.  That keeps the project
format understandable, keeps every export measured, and makes an old project
reproducible with the same pipeline hash rules as a direct upload.

Projects live beside the config (not in the library) so editing one never
rewrites a finished material.  Rendering creates a normal library material; the
project records the last job id.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .core import png
from .core.colour import Space
from .core.errors import LimitError, PbrForgeError
from .core.image import Image
from .jobs import JobManager, slugify
from .surface import SurfaceSpec, generate

PROJECT_SCHEMA = 1
LAYER_KINDS = ("offset", "surface", "weather", "grade")
BLENDS = ("normal", "multiply", "screen", "overlay")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


@dataclass
class Project:
    id: str
    name: str
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    source_name: str = "source.png"
    width: int = 0
    height: int = 0
    layers: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=lambda: {
        "preset": "generic-pbr", "category": "unknown", "resolution": 1024,
        "metal": "auto", "seamless": True, "delight": True,
        "auto_levels": False, "white_balance": False, "square": False,
        "height": "auto",
    })
    last_job: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PROJECT_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Project":
        if payload.get("schema") != PROJECT_SCHEMA:
            raise PbrForgeError(
                f"project schema {payload.get('schema')!r} is not supported by "
                f"this build (expected {PROJECT_SCHEMA})")
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in allowed})

    def validate(self) -> None:
        if not self.name.strip():
            raise PbrForgeError("the project needs a name")
        if not self.width or not self.height:
            raise PbrForgeError("the project has no source size")
        if len(self.layers) > 24:
            raise PbrForgeError("a project is limited to 24 layers")
        for index, layer in enumerate(self.layers):
            _validate_layer(layer, index)


def _validate_layer(layer: dict[str, Any], index: int) -> None:
    if not isinstance(layer, dict):
        raise PbrForgeError(f"layer {index + 1} is not an object")
    kind = layer.get("kind")
    if kind not in LAYER_KINDS:
        raise PbrForgeError(
            f"layer {index + 1} has kind {kind!r}; use one of {', '.join(LAYER_KINDS)}")
    if kind == "offset":
        for axis in ("x", "y"):
            try:
                value = int(layer.get(axis, 0))
            except (TypeError, ValueError):
                raise PbrForgeError(f"layer {index + 1} offset {axis} must be an integer")
            if abs(value) > 100000:
                raise PbrForgeError(f"layer {index + 1} offset {axis} is too large")
    elif kind == "surface":
        spec = SurfaceSpec(
            pattern=str(layer.get("pattern", "brick")),
            size=int(layer.get("size", 512)),
            seed=int(layer.get("seed", 1234)),
            weathering=str(layer.get("weathering", "none")),
            weather_amount=float(layer.get("weather_amount", 0.0)),
            variant=int(layer.get("variant", 0)),
            rows=int(layer.get("rows", 6)), cols=int(layer.get("cols", 12)),
            mortar=float(layer.get("mortar", 0.055)),
            hue=str(layer.get("hue", "warm")),
        )
        spec.validate()
        _check_unit(layer.get("opacity", 1.0), f"layer {index + 1} opacity")
        if str(layer.get("blend", "normal")) not in BLENDS:
            raise PbrForgeError(f"layer {index + 1} blend must be one of {', '.join(BLENDS)}")
    elif kind == "weather":
        if str(layer.get("type", "grime")) not in ("dust", "grime", "moss", "wear"):
            raise PbrForgeError(f"layer {index + 1} weather type is unknown")
        _check_unit(layer.get("amount", 0.35), f"layer {index + 1} amount")
        _check_unit(layer.get("opacity", 1.0), f"layer {index + 1} opacity")
    elif kind == "grade":
        # Every grade control is a 0..1 slider; 0.5 is neutral.  That makes the
        # UI consistent and avoids a second range convention hidden in JSON.
        for key in ("brightness", "contrast", "saturation", "warmth"):
            _check_unit(layer.get(key, 0.5), f"layer {index + 1} {key}")
        _check_unit(layer.get("opacity", 1.0), f"layer {index + 1} opacity")


def _check_unit(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PbrForgeError(f"{where} must be a number")
    if not 0.0 <= number <= 1.0:
        raise PbrForgeError(f"{where} must be between 0 and 1")
    return number


def default_layers() -> list[dict[str, Any]]:
    return []


def _blend(base: float, top: float, mode: str) -> float:
    if mode == "multiply":
        return base * top
    if mode == "screen":
        return 1.0 - (1.0 - base) * (1.0 - top)
    if mode == "overlay":
        return 2.0 * base * top if base < 0.5 else 1.0 - 2.0 * (1.0 - base) * (1.0 - top)
    return top


def _apply_grade(image: Image, layer: dict[str, Any]) -> Image:
    opacity = float(layer.get("opacity", 1.0))
    brightness = (float(layer.get("brightness", 0.5)) - 0.5) * 0.5
    contrast = max(0.0, float(layer.get("contrast", 0.5))) * 2.0
    saturation = max(0.0, float(layer.get("saturation", 0.5))) * 2.0
    warmth = (float(layer.get("warmth", 0.5)) - 0.5) * 0.4
    out = image.copy()
    for i in range(0, len(out.data), 3):
        r, g, b = out.data[i], out.data[i + 1], out.data[i + 2]
        # luminance-preserving saturation, so a grade cannot silently change the
        # material's measured albedo distribution without the user seeing it.
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        r = y + (r - y) * saturation
        g = y + (g - y) * saturation
        b = y + (b - y) * saturation
        r = (r - 0.5) * contrast + 0.5 + brightness + warmth
        g = (g - 0.5) * contrast + 0.5 + brightness
        b = (b - 0.5) * contrast + 0.5 + brightness - warmth
        out.data[i] = image.data[i] * (1 - opacity) + max(0.0, min(1.0, r)) * opacity
        out.data[i + 1] = image.data[i + 1] * (1 - opacity) + max(0.0, min(1.0, g)) * opacity
        out.data[i + 2] = image.data[i + 2] * (1 - opacity) + max(0.0, min(1.0, b)) * opacity
    return out


def _apply_weather(image: Image, layer: dict[str, Any]) -> Image:
    from .core import synth
    kind = str(layer.get("type", "grime"))
    amount = float(layer.get("amount", 0.35))
    opacity = float(layer.get("opacity", 1.0))
    seed = int(layer.get("seed", 1234))
    inv_w = 1.0 / image.width
    inv_h = 1.0 / image.height
    out = image.copy()
    for y in range(image.height):
        v = y * inv_h
        for x in range(image.width):
            i = (y * image.width + x) * 3
            n = synth.fbm(x * inv_w, v, 8, seed=seed)
            fine = synth.fbm(x * inv_w, v, 64, seed=seed + 1)
            mask = 0.0
            if kind == "dust":
                mask = max(0.0, n - 0.42) * 1.8
                color = (0.72, 0.69, 0.61)
            elif kind == "grime":
                mask = max(0.0, n - 0.36) * 2.2
                color = (0.16, 0.14, 0.11)
            elif kind == "moss":
                mask = max(0.0, n - 0.30) * 1.9
                color = (0.18, 0.30, 0.12)
            else:  # wear
                mask = max(0.0, fine - 0.55) * 2.2
                color = (0.80, 0.76, 0.68)
            blend = max(0.0, min(1.0, mask * amount * opacity))
            out.data[i] = image.data[i] * (1 - blend) + color[0] * blend
            out.data[i + 1] = image.data[i + 1] * (1 - blend) + color[1] * blend
            out.data[i + 2] = image.data[i + 2] * (1 - blend) + color[2] * blend
    return out


def _apply_surface(image: Image, layer: dict[str, Any]) -> Image:
    spec = SurfaceSpec(
        pattern=str(layer.get("pattern", "brick")),
        size=min(max(image.width, 64), 1024),
        seed=int(layer.get("seed", 1234)),
        weathering=str(layer.get("weathering", "none")),
        weather_amount=float(layer.get("weather_amount", 0.0)),
        variant=int(layer.get("variant", 0)),
        rows=int(layer.get("rows", 6)), cols=int(layer.get("cols", 12)),
        mortar=float(layer.get("mortar", 0.055)),
        hue=str(layer.get("hue", "warm")),
    )
    generated = generate(spec)
    pattern = Image.from_png(generated.albedo, Space.SRGB)
    if (pattern.width, pattern.height) != (image.width, image.height):
        pattern = pattern.resample(image.width, image.height, wrap=True,
                                   kernel="triangle")
    opacity = float(layer.get("opacity", 1.0))
    mode = str(layer.get("blend", "normal"))
    out = image.copy()
    for i in range(len(out.data)):
        top = pattern.data[i]
        out.data[i] = image.data[i] * (1 - opacity) + _blend(image.data[i], top, mode) * opacity
    return out


def _apply_offset(image: Image, layer: dict[str, Any]) -> Image:
    dx = int(layer.get("x", 0)) % image.width
    dy = int(layer.get("y", 0)) % image.height
    if dx == 0 and dy == 0:
        return image
    out = Image(image.width, image.height, image.channels, image.space,
                [0.0] * len(image.data))
    n = image.channels
    for y in range(image.height):
        sy = (y - dy) % image.height
        for x in range(image.width):
            sx = (x - dx) % image.width
            src = (sy * image.width + sx) * n
            dst = (y * image.width + x) * n
            out.data[dst:dst + n] = image.data[src:src + n]
    return out


def render_source(source: Path, layers: Iterable[dict[str, Any]], *,
                  max_edge: int = 2048) -> Image:
    decoded = png.read(source)
    image = Image.from_png(decoded, Space.SRGB)
    if image.channels != 3:
        if image.channels == 1:
            data = []
            for value in image.data:
                data.extend((value, value, value))
            image = Image(image.width, image.height, 3, Space.SRGB, data)
        elif image.channels == 4:
            data = []
            for i in range(0, len(image.data), 4):
                data.extend(image.data[i:i + 3])
            image = Image(image.width, image.height, 3, Space.SRGB, data)
    longest = max(image.width, image.height)
    if longest > max_edge:
        scale = max_edge / longest
        image = image.resample(max(1, int(image.width * scale)),
                               max(1, int(image.height * scale)), wrap=True,
                               kernel="triangle")
    for layer in layers:
        _validate_layer(layer, 0)
        kind = layer.get("kind")
        if kind == "offset":
            image = _apply_offset(image, layer)
        elif kind == "surface":
            image = _apply_surface(image, layer)
        elif kind == "weather":
            image = _apply_weather(image, layer)
        elif kind == "grade":
            image = _apply_grade(image, layer)
    return image


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _folder(self, project_id: str) -> Path:
        if not _ID_RE.fullmatch(project_id or ""):
            raise PbrForgeError("invalid project id")
        folder = (self.root / project_id).resolve()
        if not folder.is_relative_to(self.root.resolve()):
            raise PbrForgeError("invalid project path")
        return folder

    def _unique_id(self, name: str) -> str:
        base = slugify(name)
        candidate = base
        for index in range(2, 10000):
            if not (self.root / candidate).exists():
                return candidate
            candidate = f"{base}-{index}"
        raise PbrForgeError("could not allocate a project id")

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        for child in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            payload = self._read(child)
            if payload is None:
                continue
            payload["id"] = child.name
            payload["folder"] = str(child)
            out.append(payload)
        return out

    @staticmethod
    def _read(folder: Path) -> dict[str, Any] | None:
        path = folder / "project.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def load(self, project_id: str) -> tuple[Project, Path]:
        folder = self._folder(project_id)
        payload = self._read(folder)
        if payload is None:
            raise PbrForgeError(f"no project {project_id}")
        payload["id"] = project_id
        project = Project.from_dict(payload)
        project.validate()
        source = folder / "source.png"
        if not source.is_file():
            raise PbrForgeError(f"project {project_id} has no source.png")
        return project, source

    def create(self, name: str, source_blob: bytes, *, source_name: str = "source.png",
               layers: Iterable[dict[str, Any]] | None = None,
               options: dict[str, Any] | None = None) -> Project:
        if not source_blob.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PbrForgeError("a project source must be a PNG")
        # Decode once, so a corrupt upload cannot become a permanent project.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pbrforge-project-") as tmp:
            path = Path(tmp) / "source.png"
            path.write_bytes(source_blob)
            decoded = png.read(path)
            width, height = decoded.width, decoded.height
        if width < 16 or height < 16:
            raise LimitError("a project source must be at least 16x16")
        if width > 8192 or height > 8192:
            raise LimitError("a project source must be at most 8192 px on each edge")
        project_id = self._unique_id(name)
        project = Project(id=project_id, name=(name or "Untitled project")[:80],
                          source_name=source_name[:160], width=width, height=height)
        if layers is not None:
            if not isinstance(layers, list):
                raise PbrForgeError("layers must be a list")
            project.layers = [dict(layer) for layer in layers]
        if options:
            project.options.update(options)
        # Validate before creating a folder: a rejected request must not leave a
        # sourceless or half-created project behind.
        project.validate()
        folder = self.root / project_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "source.png").write_bytes(source_blob)
        self.save(project)
        return project

    def save(self, project: Project) -> Path:
        project.updated = time.time()
        project.validate()
        folder = self._folder(project.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "project.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(project.to_dict(), indent=2, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
        tmp.replace(path)
        return path

    def update(self, project_id: str, payload: dict[str, Any]) -> Project:
        project, _ = self.load(project_id)
        if "name" in payload:
            project.name = str(payload["name"])[:80]
        if "layers" in payload:
            layers = payload["layers"]
            if not isinstance(layers, list):
                raise PbrForgeError("layers must be a list")
            kept = []
            for index, item in enumerate(layers):
                if not isinstance(item, dict):
                    raise PbrForgeError(f"layer {index + 1} is not an object")
                kept.append(dict(item))
            project.layers = kept
        if "options" in payload and isinstance(payload["options"], dict):
            project.options.update(payload["options"])
        project.validate()
        self.save(project)
        return project

    def add_layer(self, project_id: str, layer: dict[str, Any]) -> Project:
        project, _ = self.load(project_id)
        if len(project.layers) >= 24:
            raise PbrForgeError("a project is limited to 24 layers")
        project.layers.append(dict(layer))
        project.validate()
        self.save(project)
        return project

    def remove_layer(self, project_id: str, index: int) -> Project:
        project, _ = self.load(project_id)
        if not 0 <= index < len(project.layers):
            raise PbrForgeError(f"layer {index} does not exist")
        project.layers.pop(index)
        project.validate()
        self.save(project)
        return project

    def delete(self, project_id: str) -> None:
        folder = self._folder(project_id)
        if not folder.is_dir():
            raise PbrForgeError(f"no project {project_id}")
        # Removing a project never touches a rendered library material.  The
        # library folder is immutable and the project only points at its last job.
        shutil.rmtree(folder)

    def render(self, project_id: str, output: Path, *,
               max_edge: int = 2048) -> dict[str, Any]:
        project, source = self.load(project_id)
        image = render_source(source, project.layers, max_edge=max_edge)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        png.write(output, image.to_png(8))
        return {"output": str(output), "width": image.width, "height": image.height,
                "layers": len(project.layers), "project": project.id,
                "name": project.name, "sha256": _sha256(output)}

    def submit(self, manager: JobManager, project_id: str) -> Any:
        project, source = self.load(project_id)
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pbrforge-project-") as tmp:
            output = Path(tmp) / "source.png"
            info = self.render(project_id, output)
            blob = output.read_bytes()
        options = project.options
        job = manager.submit_bytes(
            blob,
            name=project.name,
            preset=str(options.get("preset", "generic-pbr")),
            category=str(options.get("category", "unknown")),
            resolution=int(options.get("resolution", 1024)),
            metal=str(options.get("metal", "auto")),
            seamless=bool(options.get("seamless", True)),
            delight=bool(options.get("delight", True)),
            auto_levels=bool(options.get("auto_levels", False)),
            white_balance=bool(options.get("white_balance", False)),
            square=bool(options.get("square", False)),
            height_polarity=str(options.get("height", "auto")),
            source_name=f"{project.name}.png",
        )
        project.last_job = job.id
        project.notes.append(f"rendered {info['width']}x{info['height']} at "
                             f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
        project.notes = project.notes[-20:]
        self.save(project)
        return job


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_summary(project: Project) -> dict[str, Any]:
    return {
        "id": project.id, "name": project.name, "created": project.created,
        "updated": project.updated, "source_name": project.source_name,
        "width": project.width, "height": project.height,
        "layers": len(project.layers), "last_job": project.last_job,
    }
