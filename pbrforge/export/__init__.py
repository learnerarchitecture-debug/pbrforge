"""The export layer: presets, naming, containers, the manifest and the handoff.

`stages/pack.py` decides *what* to write; this package decides *how a target
wants it*. The split matters because the second half is data (preset files the
user can read and edit) rather than code, and because `qc` audits the result
without knowing anything about renderers.
"""

from __future__ import annotations

from . import handoff, manifest, naming, presets, writers
from .presets import (PACKED_ORDERS, Preset, PresetError, PresetMap, available,
                      load, packed_channels)
from .writers import write_file

__all__ = [
    "PACKED_ORDERS", "Preset", "PresetError", "PresetMap", "available",
    "handoff", "load", "manifest", "naming", "packed_channels", "presets",
    "write_file", "writers",
]