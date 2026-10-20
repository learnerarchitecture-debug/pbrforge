"""Locating bundled resources in both the source tree and a frozen build.

A single-file PyInstaller build unpacks its data into `sys._MEIPASS`, while the
source tree has `ui/` next to the package. Getting this wrong produces the most
confusing possible failure -- a working dev checkout and an installed build that
serves a 404 -- so it is centralised here and reported explicitly when missing.
"""

from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _candidates(relative: str) -> list[Path]:
    out: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / relative)

    if frozen():
        # One-file builds: data is in _MEIPASS. One-dir builds and portable
        # installs: data sits next to the exe. Check both, in that order.
        out.append(Path(sys.executable).resolve().parent / relative)

    here = Path(__file__).resolve()
    out.append(here.parent.parent.parent / relative)   # <repo>/relative
    out.append(here.parent.parent / relative)          # <repo>/pbrforge/relative
    return out


def find(relative: str) -> Path | None:
    for candidate in _candidates(relative):
        if candidate.exists():
            return candidate
    return None


def ui_dir() -> Path | None:
    """The directory holding the web UI. `ui/` in the source tree, bundled data
    when frozen."""
    return find("ui")


def index_file() -> Path | None:
    ui = ui_dir()
    if ui is None:
        return None
    for name in ("index.html", "prototype.html"):
        candidate = ui / name
        if candidate.is_file():
            return candidate
    return None


def searched_paths(relative: str) -> list[str]:
    """For error messages: where we looked, so the fix is obvious."""
    return [str(p) for p in _candidates(relative)]
