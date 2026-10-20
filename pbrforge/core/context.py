"""Pipeline context: the single mutable object passed through every stage.

`ctx.maps` is the working map set (the maps stages read and write).
`ctx.inputs` is the decoded source material, frozen after `ingest`.
`ctx.physical` is the hand-off between stages that never read each other's maps.
`ctx.classification` is the ingest's verdict on what the user dropped in.
`ctx.artifacts` are file paths the stage writes that must survive the run.
`ctx.runs` is the per-stage record for the manifest.
`ctx.ai_used` is the list of AI invocations for the violet badge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .colour import ColourSpaceError, Space, spec_for
from .errors import LimitError
from .image import Image

__all__ = ["PipelineContext"]


@dataclass
class PipelineContext:
    work: Path
    params: Any
    sources: list[Path] = field(default_factory=list)
    cache: Any = None

    #: The map set. Keys are registry kinds: albedo, height, normal, ao, cavity,
    #: curvature, roughness, metallic, opacity, shading, macro, emissive.
    maps: dict[str, Image] = field(default_factory=dict)

    #: Decoded inputs by detected kind, before any processing. `ingest` fills it;
    #: nothing else writes it, and it is what "compare against the source" means.
    inputs: dict[str, Image] = field(default_factory=dict)

    #: Plain-language lines the UI shows verbatim, e.g. "Lattice detected: 4 x 8
    #: brick courses. Cropped to an exact multiple - no blending needed."
    notes: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    physical: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
    ai_used: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.notes.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def warn(self, message: str) -> None:
        self.notes.append(f"WARNING: {message}")

    def metric(self, name: str, value: float) -> None:
        self.metrics[name] = value

    def set_map(self, kind: str, image: Image) -> Image:
        """Write a map into the working set, enforcing the registry."""
        spec = spec_for(kind)
        if image.space != spec.space:
            raise ColourSpaceError(
                f"stage tried to write {kind} as {image.space} but the registry "
                f"says {spec.space}.\n"
                f"  why      the colour space is a contract between stages; "
                f"changing it silently would make a later stage read the wrong "
                f"numbers\n"
                f"  fix      convert the image before writing it, or fix the stage "
                f"that produced the wrong space")
        if image.channels not in spec.channels:
            raise LimitError(
                f"stage tried to write {kind} with {image.channels} channel(s) "
                f"but the registry allows {spec.channels}.\n"
                f"  why      a 3-channel normal map is a normal map; a 4-channel "
                f"one is a normal+height hybrid and a downstream stage will "
                f"misread it\n"
                f"  fix      drop or split the extra channel, or fix the stage "
                f"that produced it")
        self.maps[kind] = image
        return image

    def require(self, *kinds: str) -> list[Image]:
        """Get maps that must exist, or raise naming the missing ones."""
        missing = [k for k in kinds if k not in self.maps]
        if missing:
            raise LimitError(
                f"stage requires {', '.join(missing)} but the map set has "
                f"{', '.join(sorted(self.maps))}.\n"
                f"  why      a stage cannot read maps that were not produced\n"
                f"  fix      run the stages that produce them, or supply them as "
                f"inputs")
        return [self.maps[k] for k in kinds]

    def sub(self, name: str) -> Path:
        """A subdirectory inside the work dir, created on demand."""
        p = self.work / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def summary(self) -> dict[str, Any]:
        """A JSON-able view of the run, for the manifest and the UI.

        Deliberately excludes `cache` (not serialisable) and keeps `maps` as the
        kind list rather than pixel data.
        """
        return {
            "work": str(self.work),
            "sources": [str(p) for p in self.sources],
            "maps": sorted(self.maps),
            "inputs": sorted(self.inputs),
            "notes": list(self.notes),
            "warnings": [n for n in self.notes if n.startswith("WARNING: ")],
            "metrics": dict(self.metrics),
            "timings": dict(self.timings),
            "classification": dict(self.classification),
            "physical": dict(self.physical),
            "artifacts": {k: str(v) for k, v in self.artifacts.items()},
            "ai_used": list(self.ai_used),
            "runs": list(self.runs),
        }

    def record(self, name: str, params: Any, metrics: dict[str, float] | None = None,
               seconds: float = 0.0, cached: bool = False) -> None:
        row: dict[str, Any] = {"name": name}
        if params is not None:
            row["params"] = params
        if metrics:
            row["metrics"] = {k: round(v, 6) for k, v in metrics.items()}
        if seconds:
            row["seconds"] = round(seconds, 3)
        if cached:
            row["cached"] = True
        self.runs.append(row)