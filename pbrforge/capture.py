"""Capture stack: combine several tripod frames into one clean source image.

Honest scope: this is an **exposure / transient-object merge**, not photometric
stereo and not a normal-map capture rig.  It reduces sensor noise and moving
specular highlights by taking a per-pixel median (or mean/max) of frames that
share the same camera position.  If the camera moved, the result is a blur; the
tool says so instead of pretending alignment happened.

The output is an ordinary sRGB PNG, so it goes through the same pipeline as a
single photograph.  No new map semantics are invented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .core import png
from .core.colour import Space
from .core.errors import LimitError, PbrForgeError
from .core.image import Image
from .security import hash_file

MODES = ("median", "mean", "max", "min")


@dataclass
class CaptureResult:
    output: Path
    width: int
    height: int
    frames: int
    mode: str
    normalized: bool
    source_sha256: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output), "width": self.width, "height": self.height,
            "frames": self.frames, "mode": self.mode,
            "normalized": self.normalized, "sha256": self.source_sha256,
            "notes": self.notes, "seconds": round(self.seconds, 3),
        }


def _as_rgb(image: png.Image) -> Image:
    """Decode a PNG into float sRGB RGB, dropping alpha after a warning."""
    processed = Image.from_png(image, Space.SRGB)
    if processed.channels == 3:
        return processed
    if processed.channels == 1:
        data = []
        for value in processed.data:
            data.extend((value, value, value))
        return Image(processed.width, processed.height, 3, Space.SRGB, data)
    if processed.channels == 4:
        data = []
        for index in range(0, len(processed.data), 4):
            r, g, b, a = processed.data[index:index + 4]
            data.extend((r, g, b))
        return Image(processed.width, processed.height, 3, Space.SRGB, data)
    raise PbrForgeError(f"a {processed.channels}-channel PNG cannot be merged")


def load_frames(paths: Iterable[str | Path]) -> list[Image]:
    frames: list[Image] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            raise PbrForgeError(f"capture frame does not exist: {path}")
        try:
            decoded = png.read(path)
        except png.PngError as exc:
            raise PbrForgeError(
                f"cannot read capture frame {path.name}: {exc}\n"
                f"  fix      export every frame as a non-interlaced PNG") from exc
        frames.append(_as_rgb(decoded))
    if len(frames) < 2:
        raise PbrForgeError("a capture stack needs at least two frames")
    if len(frames) > 12:
        raise LimitError("a capture stack is limited to 12 frames")
    width, height = frames[0].width, frames[0].height
    for index, frame in enumerate(frames[1:], start=2):
        if (frame.width, frame.height) != (width, height):
            raise PbrForgeError(
                f"frame {index} is {frame.width}x{frame.height}, but frame 1 is "
                f"{width}x{height}.\n"
                f"  why      this merge does not invent alignment; a different "
                f"crop or orientation would blur the result\n"
                f"  fix      export every frame at the same size from the same crop")
    if width * height > 4096 * 4096:
        raise LimitError(
            "capture frames are too large for the pure-Python merge; "
            "resize to 4096 px or less")
    return frames


def _normalize(frames: list[Image]) -> list[Image]:
    """Scale each frame to the median mean luminance, within 0.25x..4x.

    Exposure bracketing is exactly the case where a raw median can darken the
    result.  The guard rails matter: a nearly black frame must not be multiplied
    by a huge factor and amplify its noise into a plausible-looking result.
    """
    means = [max(1e-6, frame.mean(0) + frame.mean(1) + frame.mean(2)) for frame in frames]
    target = sorted(means)[len(means) // 2]
    out: list[Image] = []
    for frame, mean in zip(frames, means):
        scale = max(0.25, min(4.0, target / mean))
        if abs(scale - 1.0) < 0.005:
            out.append(frame)
            continue
        copy = frame.copy()
        copy.data = [max(0.0, min(1.0, value * scale)) for value in copy.data]
        out.append(copy)
    return out


def merge(paths: Iterable[str | Path], output: str | Path, *,
          mode: str = "median", normalize: bool = True) -> CaptureResult:
    import time
    started = time.monotonic()
    mode = (mode or "median").lower()
    if mode not in MODES:
        raise PbrForgeError(f"merge mode must be one of {', '.join(MODES)}")
    raw_paths = [Path(p) for p in paths]
    frames = load_frames(raw_paths)
    if normalize:
        frames = _normalize(frames)
    width, height, channels = frames[0].width, frames[0].height, 3
    count = len(frames)
    notes: list[str] = [
        f"{count} frames merged with {mode}" + (" (exposure-normalised)" if normalize else ""),
        "tripod/exposure stack only; no geometric alignment or photometric normals",
    ]
    values: list[float] = []
    if mode == "median":
        for index in range(0, width * height * channels, channels):
            r = sorted(frame.data[index] for frame in frames)[count // 2]
            g = sorted(frame.data[index + 1] for frame in frames)[count // 2]
            b = sorted(frame.data[index + 2] for frame in frames)[count // 2]
            values.extend((r, g, b))
    elif mode == "mean":
        inv = 1.0 / count
        for index in range(0, width * height * channels, channels):
            values.extend((
                sum(frame.data[index] for frame in frames) * inv,
                sum(frame.data[index + 1] for frame in frames) * inv,
                sum(frame.data[index + 2] for frame in frames) * inv,
            ))
    elif mode == "max":
        for index in range(0, width * height * channels, channels):
            values.extend((
                max(frame.data[index] for frame in frames),
                max(frame.data[index + 1] for frame in frames),
                max(frame.data[index + 2] for frame in frames),
            ))
    else:
        for index in range(0, width * height * channels, channels):
            values.extend((
                min(frame.data[index] for frame in frames),
                min(frame.data[index + 1] for frame in frames),
                min(frame.data[index + 2] for frame in frames),
            ))
    merged = Image(width, height, channels, Space.SRGB, values)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    png.write(target, merged.to_png(8))
    return CaptureResult(
        output=target, width=width, height=height, frames=count, mode=mode,
        normalized=normalize,
        source_sha256=[hash_file(path) for path in raw_paths],
        notes=notes, seconds=time.monotonic() - started,
    )
