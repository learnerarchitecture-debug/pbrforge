"""Small local plugin SDK.  Explicit code execution, never loaded by the server.

A plugin is a folder with ``plugin.json`` and an entry Python file::

    {
      "name": "texel-report",
      "version": "1.0.0",
      "api": 1,
      "description": "write a texel-density report",
      "entry": "plugin.py"
    }

The entry exposes ``def run(ctx): ...``.  ``ctx.material`` is the material
folder, ``ctx.out`` is a writable output folder, and the helper methods are the
supported API.  A plugin can of course import anything; that is the point of a
plugin, and why plugins are only ever run by an explicit CLI command.  The HTTP
server does not scan this folder, does not import plugins, and has no route that
executes one.

This is intentionally a Python SDK rather than a JSON-only template system:
the proposed use cases (custom delivery naming, a contact sheet, a renderer
specific metadata file) all need code, and pretending otherwise would produce a
worse API with an allow-list nobody can extend.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from .core.errors import PbrForgeError
from .core import png

API_VERSION = 1
MANIFEST = "plugin.json"


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    entry: Path
    folder: Path
    api: int = API_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "description": self.description, "entry": str(self.entry),
                "folder": str(self.folder), "api": self.api}


@dataclass
class Context:
    material: Path
    out: Path
    plugin: Plugin
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(str(message))

    def read_text(self, relative: str) -> str:
        path = (self.material / relative).resolve()
        if not path.is_relative_to(self.material.resolve()):
            raise PbrForgeError("a plugin path escaped the material folder")
        return path.read_text(encoding="utf-8")

    def read_json(self, relative: str) -> Any:
        return json.loads(self.read_text(relative))

    def write_text(self, relative: str, text: str) -> Path:
        path = (self.out / relative).resolve()
        if not path.is_relative_to(self.out.resolve()):
            raise PbrForgeError("a plugin output path escaped the output folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def write_json(self, relative: str, payload: Any) -> Path:
        return self.write_text(relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def copy(self, relative: str, output: str | None = None) -> Path:
        source = (self.material / relative).resolve()
        if not source.is_relative_to(self.material.resolve()) or not source.is_file():
            raise PbrForgeError(f"plugin source not found: {relative}")
        target = (self.out / (output or source.name)).resolve()
        if not target.is_relative_to(self.out.resolve()):
            raise PbrForgeError("a plugin output path escaped the output folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def load_png(self, relative: str) -> png.Image:
        path = (self.material / relative).resolve()
        if not path.is_relative_to(self.material.resolve()):
            raise PbrForgeError("a plugin path escaped the material folder")
        return png.read(path)


def _manifest(path: Path) -> Plugin | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    entry = str(payload.get("entry") or "plugin.py")
    if (not name or len(name) > 64
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in name)
            or "/" in entry or "\\" in entry or ".." in entry):
        return None
    try:
        api = int(payload.get("api") or API_VERSION)
    except (TypeError, ValueError):
        return None
    if api != API_VERSION:
        return None
    folder = path.parent
    entry_path = (folder / entry).resolve()
    if not entry_path.is_relative_to(folder.resolve()) or not entry_path.is_file():
        return None
    return Plugin(name=name, version=str(payload.get("version") or "0"),
                  description=str(payload.get("description") or ""),
                  entry=entry_path, folder=folder, api=api,
                  extra={k: v for k, v in payload.items()
                         if k not in ("name", "version", "description", "entry", "api")})


def discover(paths: Iterable[str | Path]) -> list[Plugin]:
    found: dict[str, Plugin] = {}
    for raw in paths:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        for manifest in sorted(root.glob(f"*/{MANIFEST}")):
            plugin = _manifest(manifest)
            if plugin is not None and plugin.name not in found:
                found[plugin.name] = plugin
    return [found[name] for name in sorted(found)]


def _load(plugin: Plugin) -> ModuleType:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"pbrforge_plugin_{plugin.name.replace('-', '_')}", plugin.entry)
    if spec is None or spec.loader is None:
        raise PbrForgeError(f"cannot load plugin {plugin.name}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:      # noqa: BLE001 - plugin errors are user errors
        raise PbrForgeError(f"plugin {plugin.name} failed to import: {exc}") from exc
    return module


def run(plugin: Plugin, material: Path, output: Path) -> dict[str, Any]:
    material = Path(material).expanduser().resolve()
    if not material.is_dir():
        raise PbrForgeError(f"material folder does not exist: {material}")
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    module = _load(plugin)
    runner = getattr(module, "run", None)
    if not callable(runner):
        raise PbrForgeError(f"plugin {plugin.name} has no run(ctx) function")
    ctx = Context(material=material, out=output, plugin=plugin)
    result = runner(ctx)
    return {"plugin": plugin.name, "version": plugin.version,
            "material": str(material), "output": str(output),
            "result": result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else repr(result),
            "logs": list(ctx.logs), "files": sorted(str(p.relative_to(output)) for p in output.rglob("*") if p.is_file())}
