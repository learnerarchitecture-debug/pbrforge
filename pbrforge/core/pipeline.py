"""Pipeline orchestration: DAG execution, caching, and the single source of truth.

The pipeline runs stages in the order declared in `stages/__init__.py`. Each stage
declares what it `needs` (maps that must already exist), what it `produces` (maps
it will write), and its `params_block` (the section of `Params` it reads). The
orchestrator:

  1. Topologically sorts by `requires` (a stage cannot run before its upstream
     stage has finished).
  2. Computes a cache key from the stage name, its params, global params, and the
     cache keys of its upstream stages.
  3. On a cold miss, runs the stage and stores (maps + metrics + notes + warnings
     + detail_line + physical_diff + classification_diff).
  4. On a warm hit, replays the maps *and* the metrics/notes/warnings/physical/
     classification diffs.

The cache payload is **not** the map pixels -- those are stored as files in the
cache directory. The payload is the small JSON record that says what happened.
Persistent per-stage output (a file on disk) must be written to `ctx.artifacts`,
because the cache only survives within a single run.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .cache import Cache, hash_file, hash_params
from .colour import MAP_SPECS, Space, spec_for
from .context import PipelineContext
from .errors import Cancelled, LimitError, PbrForgeError

__all__ = ["RunReport", "Row", "stage_key", "run_pipeline", "PipelineContext"]


@dataclass
class Row:
    name: str
    status: str                    # done / cached / supplied / off / skipped / failed
    params: dict | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    cached: bool = False
    detail: str = ""


@dataclass
class RunReport:
    rows: list[Row] = field(default_factory=list)
    seconds: float = 0.0
    failed: bool = False


def _diff(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """The keys a stage added or changed, for the cache payload.

    Small JSON-able records -- a period, a method name, a verdict -- not maps, so
    comparing and storing them directly is cheap. A value that will not compare
    cleanly is stored anyway: replaying a record twice is a nuisance, losing it is
    the bug this exists to prevent.
    """
    out: dict[str, Any] = {}
    for key, value in after.items():
        if key in before:
            try:
                if before[key] == value:
                    continue
            except Exception:
                if before[key] is value:
                    continue
        out[key] = value
    return out


def _null() -> contextmanager:
    @contextmanager
    def _n():
        yield None
    return _n()


def block_to_dict(block: Any) -> dict[str, Any]:
    return {k: v for k, v in block.__dict__.items() if not k.startswith("_")}


def stage_key(name: str, ctx: PipelineContext, upstream_keys: dict[str, str]) -> str:
    """`sha256(stage + version + its params + the global params + upstream keys)`.

    The parameter block is looked up through the stage, not by stage name: the
    derive stages deliberately read ``height``/``normal``/``ao`` blocks whose
    names do not equal their stage names. Assuming the two matched made every
    cold run fail at the first derive stage, while a warm cache could hide it.
    """
    from .. import __version__
    from ..stages import get_stage
    stage = get_stage(name)
    block = stage.params(ctx) if hasattr(ctx.params, "block") else None
    g = {"material": ctx.params.material, "category": ctx.params.category,
         "seed": ctx.params.seed, "size_mm": ctx.params.size_mm}
    blob = json.dumps({"v": __version__, "g": g, "p": block_to_dict(block),
                       "u": upstream_keys}, sort_keys=True).encode()
    import hashlib
    return hashlib.sha256(blob).hexdigest()[:16]


def _store_maps(ctx: PipelineContext, key: str, kinds: Iterable[str],
                source: dict[str, Any]) -> dict[str, str]:
    """Write each map into the cache. Returns the metadata a hit needs."""
    if ctx.cache is None:
        return {}
    out = {}
    for kind in kinds:
        image = source.get(kind)
        if image is None:
            continue
        path = ctx.cache.path / f"{key}_{kind}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        from . import png as png_mod
        png_mod.write(path, image.to_png(8))
        out[kind] = hash_file(path)
    return out


def _load_maps(ctx: PipelineContext, key: str, meta: dict[str, str],
               into: str) -> None:
    """Replay maps from the cache into `ctx.maps` or `ctx.inputs`."""
    if ctx.cache is None:
        return
    from . import png as png_mod
    from .image import Image
    target = ctx.inputs if into == "inputs" else ctx.maps
    for kind, digest in meta.items():
        path = ctx.cache.path / f"{key}_{kind}.png"
        if path.is_file() and hash_file(path) == digest:
            target[kind] = Image.from_png(png_mod.read(path),
                                          spec_for(kind).space)


def save_keys(ctx: PipelineContext, keys: dict[str, str]) -> None:
    path = ctx.work / ".pbrforge_stage_keys.json"
    path.write_text(json.dumps(keys, sort_keys=True), encoding="utf-8")


def run_pipeline(
    ctx: PipelineContext,
    *,
    force: bool = False,
    use_cache: bool = True,
    supplied: set[str] | None = None,
    on_row: Any = None,
) -> RunReport:
    """Run the full stage DAG. Returns a `RunReport` with per-stage rows.

    ``on_row`` is an optional callback invoked with each completed ``Row``. The
    server uses it to expose per-stage progress without changing the stage
    protocol or making a stage know it is being watched.
    """
    from ..stages import STAGE_ORDER, get_stage

    started = time.monotonic()
    report = RunReport()
    keys: dict[str, str] = {}
    supplied = set(supplied if supplied is not None else ctx.maps)

    def _emit(row: Row) -> None:
        report.rows.append(row)
        if on_row is not None:
            try:
                on_row(row)
            except Exception:      # a progress listener must never fail a job
                pass

    for name in STAGE_ORDER:
        st = get_stage(name)
        row = Row(name=name, status="pending")

        if not st.enabled(ctx):
            row.status = "off"
            _emit(row)
            continue

        if not all(k in ctx.maps for k in st.needs):
            row.status = "skipped"
            row.detail = f"missing needs: {', '.join(k for k in st.needs if k not in ctx.maps)}"
            _emit(row)
            continue

        key = stage_key(name, ctx, keys)
        keys[name] = key
        meta_key = f"result:{name}"

        cached = None
        if use_cache and st.cacheable and ctx.cache is not None and not force:
            if ctx.cache.has(key, meta_key):
                path = ctx.cache.get(key, meta_key)
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    _load_maps(ctx, key, payload.get("maps", {}), payload.get("into", "maps"))
                    cached = payload
                except (OSError, ValueError, LimitError) as exc:
                    ctx.warn(f"the cached result for {name} could not be replayed "
                             f"({exc}); recomputing it")

        if cached is not None:
            row.status = "cached"
            row.metrics = {k: float(v) for k, v in cached.get("metrics", {}).items()}
            # Metrics are part of the result too, and they are the part a later
            # stage *reads*. `qc` looks for `rough.confidence` in `ctx.metrics` to
            # decide whether the roughness figure is trustworthy and reports the
            # entry "not measured" when it is missing, so a warm run that replayed
            # the maps but not the numbers would deliver a weaker verdict for
            # identical parameters. Same argument as the warnings below.
            ctx.metrics.update(row.metrics)
            for note in cached.get("notes", []):
                ctx.notes.append(note)
            # Warnings are part of the result, not commentary on it. Replaying a map
            # without the doubts that came with it would make a warm run report less
            # than a cold one for identical parameters, and the user would reasonably
            # conclude the doubt had gone away.
            for warning in cached.get("warnings", []):
                ctx.warn(warning)
            # `ctx.physical` is the hand-off between stages that never read each
            # other's maps: `derive_rough` records the roughness convention there,
            # `lattice` records the period, `seamless` records the method it used,
            # and `pack` needs all three. It is replayed from a per-stage diff
            # rather than a snapshot, so a replayed stage cannot overwrite a later
            # stage's record with an older value.
            for key_name, value in (cached.get("physical") or {}).items():
                ctx.physical[key_name] = value
            if cached.get("classification"):
                ctx.classification.update(cached["classification"])
            row.detail = cached.get("detail_line", "")
            ctx.record(name, block_to_dict(st.params(ctx)), row.metrics,
                       cached=True)
            _emit(row)
            continue

        # ---- run it ----
        t0 = time.monotonic()
        physical_before = dict(ctx.physical)
        classification_before = dict(ctx.classification)
        lock = ctx.cache.lock(key) if (ctx.cache is not None and st.cacheable) else _null()
        with lock:
            try:
                result = st.run(ctx)
            except Cancelled:
                raise
            except LimitError:
                raise
            except Exception as exc:
                row.status = "failed"
                row.detail = f"{type(exc).__name__}: {exc}"
                _emit(row)
                report.failed = True
                return report

        row.status = "done"
        row.seconds = time.monotonic() - t0
        row.metrics = dict(result.metrics)
        row.detail = result.detail.get("line", "") if isinstance(result.detail, dict) else ""
        ctx.timings[name] = row.seconds
        ctx.record(name, block_to_dict(st.params(ctx)), result.metrics, row.seconds)

        if ctx.cache is not None and st.cacheable and use_cache:
            into = "inputs" if st.produces_inputs else "maps"
            kinds = st.kinds_produced(into, ctx)
            payload = {
                "name": name, "metrics": result.metrics, "notes": result.notes,
                "warnings": result.warnings,
                "detail_line": row.detail, "maps": {}, "into": into,
                "physical": _diff(ctx.physical, physical_before),
                "classification": _diff(ctx.classification, classification_before),
            }
            payload["maps"] = _store_maps(
                ctx, key, kinds, ctx.inputs if into == "inputs" else ctx.maps)
            ctx.cache.put_bytes(key, meta_key,
                                json.dumps(payload, sort_keys=True).encode("utf-8"))

        _emit(row)

    save_keys(ctx, keys)
    report.seconds = time.monotonic() - started
    return report