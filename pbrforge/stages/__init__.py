"""Stage registry: the DAG order and lazy imports.

The order here IS the pipeline. Changing it changes what runs before what, which
changes the cache keys (since upstream keys are part of the key), which invalidates
the cache -- correctly, because the data dependencies changed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["STAGE_ORDER", "get_stage"]

# The pipeline order. Each stage"s `requires` must be a prefix of this list.
STAGE_ORDER = (
    "ingest",
    "preprocess",
    "lattice",
    "seamless",
    "delight",
    "derive_height",
    "derive_normal",
    "derive_ao",
    "derive_rough",
    "derive_metal",
    "variation",
    "alphamatte",
    "upscale",
    "pack",
    "qc",
)

_STAGES: dict[str, Any] = {}


def get_stage(name: str) -> Any:
    """Lazy import: only the stages that run are imported."""
    if name in _STAGES:
        return _STAGES[name]
    if name == "ingest":
        from . import ingest
        _STAGES[name] = ingest.STAGE
    elif name == "preprocess":
        from . import preprocess
        _STAGES[name] = preprocess.STAGE
    elif name == "lattice":
        from . import lattice
        _STAGES[name] = lattice.STAGE
    elif name == "seamless":
        from . import seamless
        _STAGES[name] = seamless.STAGE
    elif name == "delight":
        from . import delight
        _STAGES[name] = delight.STAGE
    elif name == "derive_height":
        from . import derive_height
        _STAGES[name] = derive_height.STAGE
    elif name == "derive_normal":
        from . import derive_normal
        _STAGES[name] = derive_normal.STAGE
    elif name == "derive_ao":
        from . import derive_ao
        _STAGES[name] = derive_ao.STAGE
    elif name == "derive_rough":
        from . import derive_rough
        _STAGES[name] = derive_rough.STAGE
    elif name == "derive_metal":
        from . import derive_metal
        _STAGES[name] = derive_metal.STAGE
    elif name == "variation":
        from . import variation
        _STAGES[name] = variation.STAGE
    elif name == "alphamatte":
        from . import alphamatte
        _STAGES[name] = alphamatte.STAGE
    elif name == "upscale":
        from . import upscale
        _STAGES[name] = upscale.STAGE
    elif name == "pack":
        from . import pack
        _STAGES[name] = pack.STAGE
    elif name == "qc":
        from . import qc
        _STAGES[name] = qc.STAGE
    else:
        raise ImportError(f"unknown stage: {name}")
    return _STAGES[name]
