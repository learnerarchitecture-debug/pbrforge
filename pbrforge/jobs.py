"""Job orchestration for the upload → PBR feature.

The HTTP server must answer while a 20-second pure-Python pipeline is running, so
conversion happens on a worker thread and the browser polls a small JSON record.
One worker is deliberate: the derive stages are CPU-bound Python, so a second
worker would double memory and make both jobs slower rather than one job finish
sooner.

The lifecycle is:

    submit -> staging/<job-id>/source.png
           -> run the real stage DAG (no cache: a first conversion is cold anyway)
           -> promote the packed tree to <library>/<slug>/
           -> build previews + a zip next to the material
           -> job.json, so the web UI can list the library after a restart

A failed job is *not* promoted. It stays under ``cache/staging/<id>`` with the
error, which is the only way to debug a failure on the machine that produced it.
"""

from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .core.errors import LimitError, PbrForgeError
from .core.params import Params
from .core.pipeline import run_pipeline
from .core.context import PipelineContext
from .core import png as png_mod
from .core.image import Image
from .core.ops import resize_image
from .export import manifest as manifest_mod

__all__ = ["Job", "JobManager", "slugify"]

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PREVIEW_KINDS = ("albedo", "normal", "roughness", "metallic", "ao", "height",
                 "opacity", "macro")
PREVIEW_EDGE = 768
MAX_EDGE = 16384


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return (text[:48] or "material")


def _png_size(path: Path) -> tuple[int, int]:
    """Read width/height from a PNG IHDR without decoding the pixels."""
    with path.open("rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != PNG_SIG or head[12:16] != b"IHDR":
        raise LimitError(
            "the uploaded file is not a valid PNG.\n"
            "  why      the IHDR header is missing or the signature is wrong\n"
            "  fix      upload the image through the web UI, which converts "
            "every source format to PNG in the browser")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


@dataclass
class Job:
    id: str
    name: str
    slug: str
    preset: str
    category: str
    resolution: int
    source_name: str = ""
    state: str = "queued"          # queued / running / done / failed
    progress: float = 0.0
    stage: str = "queued"
    message: str = "waiting for the worker"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    seconds: float = 0.0
    folder: str | None = None
    zip_path: str | None = None
    error: str = ""
    error_stage: str = ""
    qc: dict[str, Any] | None = None
    maps: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        warnings = [n[len("WARNING: "):] for n in self.notes
                    if n.startswith("WARNING: ")]
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "state": self.state,
            "progress": round(self.progress, 4),
            "stage": self.stage,
            "message": self.message,
            "preset": self.preset,
            "category": self.category,
            "resolution": self.resolution,
            "source_name": self.source_name,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "seconds": round(self.seconds, 3),
            "folder": self.folder,
            "error": self.error,
            "error_stage": self.error_stage,
            "qc": self.qc,
            "maps": self.maps,
            "notes": self.notes,
            "warnings": warnings,
            "metrics": self.metrics,
            "download": f"/api/jobs/{self.id}/download" if self.state == "done" else None,
        }


class JobManager:
    def __init__(self, config: Config, *, workers: int = 1) -> None:
        self.config = config
        self.library = Path(config.library or "")
        self.library.mkdir(parents=True, exist_ok=True)
        self.staging = Path(config.cache) / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[Job] = queue.Queue()
        self._workers = max(1, int(workers))
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._start_workers()

    # ------------------------------------------------------------- lifecycle
    def _start_workers(self) -> None:
        for index in range(self._workers):
            thread = threading.Thread(target=self._worker, name=f"pbrforge-job-{index}",
                                      daemon=True)
            thread.start()
            self._threads.append(thread)

    def shutdown(self) -> None:
        self._stop.set()
        for _ in self._threads:
            self._queue.put(None)  # type: ignore[arg-type]

    def _worker(self) -> None:
        while not self._stop.is_set():
            job = self._queue.get()
            if job is None:
                return
            try:
                self._execute(job)
            except Exception:      # noqa: BLE001 - the job record carries the error
                job.state = "failed"
                job.error = job.error or "the job failed before it could report why"
            finally:
                job._done.set()
                self._queue.task_done()

    # --------------------------------------------------------------- submit
    def submit_bytes(self, blob: bytes, *, name: str, preset: str = "generic-pbr",
                     category: str = "unknown", resolution: int = 1024,
                     metal: str = "auto", seamless: bool = True,
                     delight: bool = True, auto_levels: bool = False,
                     white_balance: bool = False, square: bool = False,
                     height_polarity: str = "auto",
                     source_name: str = "upload.png") -> Job:
        limit = int(self.config.max_upload_mb) * 1024 * 1024
        if len(blob) > limit:
            raise LimitError(
                f"the upload is {len(blob) / 1e6:.1f} MB, above the configured "
                f"limit of {self.config.max_upload_mb} MB.\n"
                "  why      the server refuses before writing to disk\n"
                "  fix      raise server.max_upload_mb in config.toml, or upload "
                "a smaller image")
        if not blob.startswith(PNG_SIG):
            raise LimitError(
                "the upload is not a PNG.\n"
                "  why      the portable server decodes PNG only; the browser "
                "converts JPEG/WebP/HEIC to PNG before sending\n"
                "  fix      use the web UI's upload button, or save the image as "
                "PNG and retry")
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        folder = self.staging / job_id
        folder.mkdir(parents=True, exist_ok=False)
        source = folder / "source.png"
        source.write_bytes(blob)
        try:
            width, height = _png_size(source)
        except LimitError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if width < 16 or height < 16:
            shutil.rmtree(folder, ignore_errors=True)
            raise LimitError(
                f"the image is {width}x{height}; the smallest supported material "
                f"is 16x16.\n"
                f"  why      there is no repeat unit or PBR detail to measure\n"
                f"  fix      upload a larger photograph")
        if width > MAX_EDGE or height > MAX_EDGE:
            shutil.rmtree(folder, ignore_errors=True)
            raise LimitError(
                f"the image is {width}x{height}, above the {MAX_EDGE}px decoder "
                f"limit.\n"
                f"  why      a single-frame PNG decode at this size needs more "
                f"memory than the portable build assumes\n"
                f"  fix      downscale it to {MAX_EDGE}px on the long edge first")

        resolution = max(256, min(4096, int(resolution)))
        clean_name = (name or Path(source_name).stem or "material").strip()[:80]
        job = Job(
            id=job_id, name=clean_name, slug=slugify(clean_name),
            preset=preset, category=(category or "unknown").lower(),
            resolution=resolution, source_name=source_name,
            message="queued",
        )
        job._options = {  # type: ignore[attr-defined]
            "metal": metal, "seamless": bool(seamless),
            "delight": bool(delight), "auto_levels": bool(auto_levels),
            "white_balance": bool(white_balance), "square": bool(square),
            "height_polarity": height_polarity,
            "source_size": [width, height],
        }
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job)
        return job

    def submit_file(self, path: Path, **kwargs: Any) -> Job:
        path = Path(path)
        if path.suffix.lower() != ".png":
            raise LimitError(
                f"{path.name} is not a PNG.\n"
                "  why      the portable build ships no JPEG decoder\n"
                "  fix      use 'pbrforge make' with a PNG, or the web UI, which "
                "converts in the browser")
        return self.submit_bytes(path.read_bytes(), source_name=path.name, **kwargs)

    # ----------------------------------------------------------------- run
    def _set(self, job: Job, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(job, key, value)

    def _execute(self, job: Job) -> None:
        options = getattr(job, "_options", {})
        folder = self.staging / job.id
        source = folder / "source.png"
        started = time.monotonic()
        self._set(job, state="running", started=time.time(), progress=0.02,
                  stage="ingest", message="decoding the source")

        try:
            params = self._params_for(job, options)
            ctx = PipelineContext(work=folder, params=params, sources=[source],
                                  cache=None)
            ctx.physical["version"] = __version__
            ctx.physical["job_id"] = job.id
            order = 15

            def on_row(row: Any) -> None:
                done = len(ctx.runs)
                fraction = min(1.0, done / order)
                status = row.status
                detail = row.detail or status
                self._set(job, progress=0.04 + 0.84 * fraction,
                          stage=row.name, message=f"{row.name}: {detail}")

            report = run_pipeline(ctx, use_cache=False, on_row=on_row)
            if report.failed:
                failed = next((r for r in report.rows if r.status == "failed"), None)
                stage = failed.name if failed else "pipeline"
                detail = failed.detail if failed else "unknown failure"
                raise _JobFailure(stage, detail)
            if "albedo" not in ctx.maps:
                raise _JobFailure("pipeline", "the run finished without an albedo map")

            self._set(job, progress=0.90, stage="publish",
                      message="moving the packed material into the library")
            final = self._promote(job, folder)
            self._write_previews(ctx, final)
            self._write_docs(job, ctx, final)
            self._write_zip(job, final)
            self._write_job_record(job, ctx, final)

            self._set(
                job, state="done", progress=1.0, stage="done",
                finished=time.time(), seconds=time.monotonic() - started,
                folder=str(final), message="converted",
                qc=(ctx.physical.get("qc") or {}).get("verdict"),
                notes=list(ctx.notes),
                metrics={k: round(v, 6) for k, v in ctx.metrics.items()},
            )
        except _JobFailure as exc:
            self._fail(job, folder, exc.stage, str(exc))
        except (LimitError, PbrForgeError) as exc:
            self._fail(job, folder, job.stage, str(exc))
        except Exception as exc:      # noqa: BLE001 - record, do not pretend
            trace = traceback.format_exc()
            self._fail(job, folder, job.stage, f"{type(exc).__name__}: {exc}",
                       trace)
        finally:
            self._set(job, seconds=time.monotonic() - started)

    def _params_for(self, job: Job, options: dict[str, Any]) -> Params:
        source_w, source_h = options.get("source_size", [1024, 1024])
        side = min(source_w, source_h) if options.get("square") else max(source_w, source_h)
        work_size = min(job.resolution, side)
        params = Params(material=job.name, category=job.category, seed=1234)
        params.apply_category(job.category)
        params.preprocess.enabled = True
        params.preprocess.white_balance = "gray_world" if options.get("white_balance") else "none"
        params.preprocess.crop = "square" if options.get("square") else "none"
        params.preprocess.size = work_size
        params.preprocess.auto_levels = bool(options.get("auto_levels"))
        params.preprocess.denoise = 0.10
        params.preprocess.dejpeg = 0.0
        params.preprocess.vignette_correct = 0.0
        params.ingest.max_pixels = max(4_000_000, work_size * work_size * 2)
        params.lattice.enabled = True
        params.lattice.snap = True
        params.seamless.enabled = bool(options.get("seamless", True))
        params.seamless.method = "auto"
        params.seamless.accept_score = 88
        params.seamless.search_window = 96
        params.delight.enabled = bool(options.get("delight", True))
        params.delight.strength = 0.75
        params.delight.preserve_micro = True
        params.height.enabled = True
        params.height.source = "delight"
        params.height.detail_boost = 0.6
        params.height.amplitude_mm = 10.0
        polarity = (options.get("height_polarity") or "auto").lower()
        if polarity in ("invert", "dark", "dark_high", "recessed_light"):
            params.height.invert = True
        elif polarity in ("normal", "light", "light_high", "bright_high"):
            params.height.invert = False
        params.normal.enabled = True
        params.normal.strength = 1.0
        params.normal.orientation = "opengl"
        params.ao.enabled = True
        params.ao.radius_px = max(8, int(round(work_size / 32)))
        params.ao.strength = 0.65
        params.rough.enabled = True
        params.rough.algorithm = "hybrid"
        params.rough.base = 0.55
        params.rough.remap = (0.12, 0.88)
        params.rough.output_mode = "roughness"
        params.metal.enabled = True
        metal = (options.get("metal") or "auto").lower()
        if metal in ("none", "dielectric", "zero"):
            params.metal.mode = "zero"
            params.metal.value = 0.0
        elif metal in ("full", "metal", "metallic", "one"):
            params.metal.mode = "one"
            params.metal.value = 1.0
        else:
            params.metal.mode = "classify"
            params.metal.value = 0.0
        params.variation.enabled = True
        params.variation.macro_strength = 0.25
        params.variation.roughness_nudge = 0.1
        params.matte.enabled = True
        params.upscale.enabled = False
        params.pack.preset = job.preset
        params.pack.resolutions = (work_size,)
        # File names are a filesystem contract: use the ASCII slug, not the
        # display name. Spaces and non-ASCII characters in a texture path break
        # more renderers and command lines than they help.
        params.pack.material_name = job.slug
        params.pack.category = job.category
        params.pack.version = __version__
        params.pack.write_sidecar = True
        params.pack.write_handoff = True
        params.qc.enabled = True
        params.validate()
        return params

    # -------------------------------------------------------------- publish
    def _promote(self, job: Job, staging: Path) -> Path:
        export_root = staging / "export"
        if not export_root.is_dir():
            raise _JobFailure("pack", "pack did not produce an export tree")
        if (staging / "maps").exists():
            shutil.rmtree(staging / "maps")
        target_export = staging / "export_moved"
        target_export.mkdir(parents=True, exist_ok=True)
        for entry in list(export_root.iterdir()):
            if entry.name == "export" and entry.is_dir():
                for child in entry.iterdir():
                    shutil.move(str(child), str(target_export / child.name))
            else:
                shutil.move(str(entry), str(staging / entry.name))
        shutil.rmtree(export_root, ignore_errors=True)
        target_export.rename(staging / "export")
        final = self._unique_folder(job.slug)
        shutil.move(str(staging), str(final))
        return final

    def _unique_folder(self, slug: str) -> Path:
        base = self.library / slug
        if not base.exists():
            return base
        for index in range(2, 1000):
            candidate = self.library / f"{slug}-{index}"
            if not candidate.exists():
                return candidate
        raise _JobFailure("publish", f"cannot find a free library name for {slug}")

    def _write_previews(self, ctx: PipelineContext, folder: Path) -> None:
        previews = folder / "previews"
        previews.mkdir(parents=True, exist_ok=True)
        for kind in PREVIEW_KINDS:
            image = ctx.maps.get(kind)
            if image is None:
                continue
            longest = max(image.width, image.height)
            if longest > PREVIEW_EDGE:
                scale = PREVIEW_EDGE / longest
                preview = resize_image(image, max(1, int(image.width * scale)),
                                       max(1, int(image.height * scale)),
                                       wrap=True, renormalise=(kind == "normal"))
            else:
                preview = image
            png_mod.write(previews / f"{kind}.png", preview.to_png(8))

    def _write_docs(self, job: Job, ctx: PipelineContext, folder: Path) -> None:
        qc = ctx.physical.get("qc") or {}
        lines = [
            "PBRForge material",
            "=================",
            "",
            f"name:        {job.name}",
            f"preset:      {job.preset}",
            f"category:    {job.category}",
            f"resolution:  {job.resolution}px (working size {ctx.maps['albedo'].width}"
            f"x{ctx.maps['albedo'].height})",
            f"version:     {__version__}",
            f"qc verdict:  {qc.get('verdict', 'not run')}",
            "",
            "Files",
            "-----",
            "maps/                 one file per delivered map",
            "export/<preset>/      the preset's packaged set",
            "previews/             768px PNG previews for the browser",
            "material.json         provenance, scale and the QC verdict",
            "qc_report.json        every measured value and threshold",
            "source.png            the exact PNG that was converted",
            "",
            "A seam score of 100 means the wrap join is no worse than the",
            "texture's own strongest interior join. See docs/QC_METRICS.md.",
            "",
        ]
        if ctx.notes:
            lines.append("Run notes")
            lines.append("---------")
            lines.extend(ctx.notes)
        (folder / "README.txt").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8", newline="\n")

    def _write_zip(self, job: Job, folder: Path) -> None:
        zip_path = folder / f"PBRForge-{job.slug}-{job.resolution}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path == zip_path:
                    continue
                if path.name.startswith("."):
                    continue
                relative = path.relative_to(folder).as_posix()
                archive.write(path, relative)
        self._set(job, zip_path=str(zip_path))

    def _write_job_record(self, job: Job, ctx: PipelineContext, folder: Path) -> None:
        # This is written before the final state flip, so stamp the finish time
        # here; otherwise a library material reloads as "0.0 s" forever.
        if job.finished is None:
            job.finished = time.time()
        if job.seconds <= 0.0 and job.started:
            job.seconds = max(0.0, job.finished - job.started)
        payload = job.to_dict()
        payload.update({
            "state": "done",
            "progress": 1.0,
            "version": __version__,
            "source_size": (ctx.classification.get("input_size") or [None, None]),
            "working_size": [ctx.maps["albedo"].width, ctx.maps["albedo"].height],
            "maps": [{"kind": kind, "width": image.width, "height": image.height,
                      "url": f"/api/jobs/{job.id}/map/{kind}"}
                     for kind, image in sorted(ctx.maps.items())
                     if kind in PREVIEW_KINDS],
            "notes": list(ctx.notes),
            "warnings": [n[len("WARNING: "):] for n in ctx.notes
                         if n.startswith("WARNING: ")],
            "metrics": {k: round(v, 6) for k, v in ctx.metrics.items()},
            "qc": ctx.physical.get("qc"),
            "runs": [{"name": name, "seconds": round(seconds, 3)}
                     for name, seconds in ctx.timings.items()],
            "download": f"/api/jobs/{job.id}/download",
            "job": "job.json",
        })
        self._set(job, maps=payload["maps"], notes=payload["notes"],
                  metrics=payload["metrics"], qc=payload["qc"],
                  folder=str(folder), zip_path=job.zip_path)
        (folder / "job.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")

    def _fail(self, job: Job, folder: Path, stage: str, message: str,
              trace: str | None = None) -> None:
        error = f"{stage}: {message}" if stage and not message.startswith(stage) else message
        self._set(job, state="failed", stage=stage or "pipeline", error=error,
                  error_stage=stage or "pipeline", progress=1.0,
                  finished=time.time(), message=error.splitlines()[0][:200])
        try:
            (folder / "error.txt").write_text(
                (trace or message) + "\n", encoding="utf-8", newline="\n")
        except OSError:
            pass

    # --------------------------------------------------------------- access
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]

    def counts(self) -> dict[str, int]:
        with self._lock:
            jobs = list(self._jobs.values())
        out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        for job in jobs:
            out[job.state] = out.get(job.state, 0) + 1
        return out

    def map_file(self, job: Job, kind: str) -> Path | None:
        if not job.folder:
            return None
        if kind not in PREVIEW_KINDS:
            return None
        path = Path(job.folder) / "previews" / f"{kind}.png"
        return path if path.is_file() else None

    def zip_file(self, job: Job) -> Path | None:
        if not job.zip_path:
            return None
        path = Path(job.zip_path)
        return path if path.is_file() else None

    def find_job_record(self, job_id: str) -> dict[str, Any] | None:
        """Look up a finished job after a restart, when memory is empty.

        The library is the durable record; the in-memory dict exists only for
        live progress. Without this, a bookmarked result URL 404s after the app
        is restarted, which is indistinguishable from the result being deleted.
        """
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", job_id or ""):
            return None
        if not self.library.is_dir():
            return None
        for child in self.library.iterdir():
            if not child.is_dir():
                continue
            record = _read_json(child / "job.json")
            if not record or record.get("id") != job_id:
                continue
            record["folder"] = str(child)
            zips = sorted(child.glob("PBRForge-*.zip"))
            record["zip_path"] = str(zips[0]) if zips else record.get("zip_path")
            return record
        return None

    def find_library_map(self, slug: str, kind: str) -> Path | None:
        return self.library_map(slug, kind)

    # -------------------------------------------------------------- library
    def list_library(self) -> list[dict[str, Any]]:
        out = []
        if not self.library.is_dir():
            return out
        for child in sorted(self.library.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            record = manifest_mod.read(child / "material.json")
            job = _read_json(child / "job.json")
            if record is None and job is None:
                continue
            qc = (record or {}).get("qc") or (job or {}).get("qc") or {}
            if isinstance(qc, dict):
                verdict = qc.get("verdict")
            else:
                verdict = qc
            previews = []
            for kind in PREVIEW_KINDS:
                path = child / "previews" / f"{kind}.png"
                if path.is_file():
                    previews.append({"kind": kind,
                                     "url": f"/api/library/{child.name}/map/{kind}"})
            out.append({
                "slug": child.name,
                "name": (job or {}).get("name") or str((record or {}).get("material") or child.name),
                "created": (job or {}).get("created"),
                "preset": (job or {}).get("preset") or (record or {}).get("preset"),
                "resolution": (job or {}).get("resolution"),
                "verdict": verdict,
                "maps": previews,
                "folder": str(child),
            })
        return out

    def library_map(self, slug: str, kind: str) -> Path | None:
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", slug or ""):
            return None
        if kind not in PREVIEW_KINDS:
            return None
        path = self.library / slug / "previews" / f"{kind}.png"
        return path if path.is_file() else None


class _JobFailure(Exception):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
