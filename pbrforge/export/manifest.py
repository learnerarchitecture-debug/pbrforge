"""`material.json`: what this material is, what produced it, and how to read it.

ARCHITECTURE.md 5 specifies the record; this module writes it and, after `qc` has
run, fills in the verdict. It is the one file in the export whose value is not the
pixels, and the one that ages best: a folder of PNGs whose provenance has been
forgotten is a folder of PNGs.

Two decisions worth stating, because both are visible in the JSON:

**Relative paths, relative to the material root.** The file is meant to survive
being copied to another machine, so it never contains a local absolute path. Every
path in it is written with forward slashes even on Windows, because a manifest
that only one platform can parse is a manifest that gets ignored.

**The verdict is filled in later.** `pack` runs before `qc` (the order is in
`stages/__init__.py`), so the `qc` block is written as `null` with a note saying
who fills it. The pipeline driver calls `update_qc` once the run is over. A
manifest that claims a verdict nobody computed would be the worst kind of wrong.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.cache import hash_file
from ..core.context import PipelineContext
from .presets import Preset

__all__ = ["SCHEMA", "build", "merge_history", "read", "relpath", "update_qc",
           "write"]

#: Bumped when the *meaning* of a field changes, not when a field is added.
SCHEMA = 3


def relpath(path: Path | str, root: Path) -> str:
    """A path in the manifest: relative to the material root, forward slashes."""
    p = Path(path)
    try:
        p = p.relative_to(root)
    except ValueError:
        # A file outside the export root cannot be described relatively. Saying so
        # is better than a string that looks portable and is not.
        return str(p).replace("\\", "/")
    return str(p).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build(ctx: PipelineContext, preset: Preset, *, out_root: Path,
          entries: list[dict[str, Any]], masters: list[dict[str, Any]],
          orientation: str, roughness_mode: str, resolutions: tuple[int, ...],
          texel_source: str) -> dict[str, Any]:
    """The manifest for this run. Pure: it reads `ctx` and returns a dict."""
    params = ctx.params
    lattice = ctx.physical.get("lattice") or {}
    confident = float(lattice.get("confidence") or 0.0)
    size_mm = params.size_mm
    px_per_mm = None
    if size_mm and size_mm[0]:
        primary = next((m for m in masters
                        if m["kind"] in ("albedo", "height")), None)
        if primary is not None and primary.get("width"):
            px_per_mm = round(float(primary["width"]) / float(size_mm[0]), 6)

    sources = []
    for path in ctx.sources:
        try:
            digest = hash_file(path)
        except OSError:
            digest = None
        sources.append({
            "file": path.name,
            "sha256": digest,
            "role": next((s.get("role") for s in
                          ctx.classification.get("sources", [])
                          if s.get("file") == path.name), "primary"),
            "kind": next((s.get("kind") for s in
                          ctx.classification.get("sources", [])
                          if s.get("file") == path.name), ""),
        })

    outputs = {str(e["kind"]): relpath(e["file"], out_root) for e in entries}
    return {
        "schema": SCHEMA,
        "material": params.material,
        "created": _now(),
        "pipeline_hash": params.digest(),
        "pbrforge_version": __version__,
        "deterministic": not ctx.ai_used,
        "seed": params.seed,
        "source": {
            "files": sources,
            # Copyright is the user's to state. A tool that fills this in from
            # somewhere is a tool that lies in a legal document.
            "rights": None,
            "note": ("the SHA-256 of every input, so a re-run against the same "
                     "files can be proved rather than believed"),
        },
        "classification": {
            "type": params.category,
            "origin": ctx.physical.get("origin"),
            "kinds": ctx.classification.get("kinds", []),
            "lattice": ([lattice.get("period_x"), lattice.get("period_y"),
                         round(confident, 4)] if confident else None),
        },
        "physical": {
            "size_mm": list(size_mm) if size_mm else None,
            "px_per_mm": px_per_mm,
            "texel_source": texel_source,
            "amplitude_mm": ctx.physical.get("amplitude_mm"),
            "midlevel": ctx.physical.get("midlevel"),
        },
        "preset": {
            "name": preset.name,
            "label": preset.label,
            "target_renderer": preset.target_renderer,
            "verified": preset.verified,
            "verified_against_renderer": preset.verified_against_renderer,
            "renderer_versions_tested": list(preset.versions),
            "channel_order": preset.channel_order,
            "normal_orientation": orientation,
            "roughness_mode": roughness_mode,
        },
        "resolutions": list(resolutions),
        "stages": list(ctx.runs),
        "ai": list(ctx.ai_used),
        "outputs": outputs,
        "masters": {str(m["kind"]): relpath(m["file"], out_root)
                    for m in masters},
        "qc": None,
        "qc_note": "filled in by the qc stage; null until it has run",
        "history": [{
            "at": _now(),
            "preset": preset.name,
            "resolutions": list(resolutions),
            "pipeline_hash": params.digest(),
            "pbrforge_version": __version__,
        }],
    }


def write(path: Path, payload: dict[str, Any]) -> Path:
    """Write the manifest atomically, sorted so two runs diff cleanly."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(blob, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def read(path: Path) -> dict[str, Any] | None:
    """Read a manifest, or None when there is not one."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def update_qc(path: Path, ctx: PipelineContext) -> bool:
    """Copy the qc block from `ctx.physical["qc"]` into an existing manifest.

    Returns False when there is nothing to do, and says nothing: the caller is
    the CLI, which reports. `qc` is not cacheable, so this always has fresh data
    to copy.
    """
    payload = read(path)
    if payload is None:
        return False
    verdict = ctx.physical.get("qc")
    if not isinstance(verdict, dict):
        return False
    payload["qc"] = {
        "verdict": verdict.get("verdict"),
        "reasons": list(verdict.get("reasons") or [])[:4],
        "report": verdict.get("report"),
        "thresholds": verdict.get("thresholds"),
        "measured": verdict.get("measured"),
    }
    payload.pop("qc_note", None)
    write(path, payload)
    return True


def merge_history(path: Path, previous: Path, *, keep: int = 20) -> bool:
    """Fold an earlier manifest's history into this one, newest first.

    Called by the publisher, which is the only place that knows both the new
    manifest (inside the job's work directory) and the one it is about to
    replace.
    """
    new = read(path)
    old = read(previous)
    if new is None or old is None:
        return False
    history = list(new.get("history") or []) + list(old.get("history") or [])
    # De-duplicate the way a human would: same minute, same preset, same hash is
    # the same entry arriving twice through a re-run.
    seen: set[tuple[Any, ...]] = set()
    kept: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("at", ""))[:16], item.get("preset"),
               item.get("pipeline_hash"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    new["history"] = kept[:keep]
    new["replaces"] = {
        "created": old.get("created"),
        "pbrforge_version": old.get("pbrforge_version"),
        "preset": (old.get("preset") or {}).get("name"),
    }
    write(path, new)
    return True