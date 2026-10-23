"""Watch a folder and forge new PNGs as they arrive.

Deliberately a polling watcher, not an OS filesystem hook.  Windows, OneDrive,
SMB shares and network drives all disagree about change notifications, and a
missed event is silent.  A five-second `scandir` is boring, portable and easy to
explain.

Processed files are remembered by SHA-256 in a small state file, so renaming a
file does not forge it twice and editing one does.  The state file is written
atomically after each successful submission, not after each *job*: if the
process dies during a long pipeline run, the next start will see the state entry
and not duplicate it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .core.errors import PbrForgeError
from .jobs import JobManager
from .security import hash_file

STATE_NAME = ".pbrforge-watch.json"


@dataclass
class WatchResult:
    path: Path
    job: Any


def _load_state(folder: Path) -> dict[str, Any]:
    path = folder / STATE_NAME
    if not path.is_file():
        return {"version": 1, "seen": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "seen": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("seen"), dict):
        return {"version": 1, "seen": {}}
    return payload


def _save_state(folder: Path, state: dict[str, Any]) -> None:
    path = folder / STATE_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(path)


def watch(folder: Path, manager: JobManager, *, interval: float = 5.0,
          once: bool = False, recursive: bool = False,
          name_prefix: str = "", options: dict[str, Any] | None = None,
          on_job: Callable[[Path, Any], None] | None = None,
          on_error: Callable[[Path, str], None] | None = None,
          stop: Callable[[], bool] | None = None) -> list[WatchResult]:
    folder = Path(folder)
    if not folder.is_dir():
        raise PbrForgeError(f"watch folder does not exist: {folder}")
    state = _load_state(folder)
    seen: dict[str, Any] = state.setdefault("seen", {})
    options = dict(options or {})
    results: list[WatchResult] = []
    pattern = "**/*.png" if recursive else "*.png"
    while True:
        candidates = sorted(folder.glob(pattern))
        for path in candidates:
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                digest = hash_file(path)
            except OSError:
                continue
            if digest in seen:
                continue
            try:
                job = manager.submit_file(
                    path,
                    name=(name_prefix + path.stem)[:80] or path.stem[:80],
                    **options,
                )
                seen[digest] = {"file": path.name, "job": job.id,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                # Persist before waiting: a crash during a long job must not make
                # the next watcher run process the same source again.
                _save_state(folder, state)
                if on_job is not None:
                    on_job(path, job)
                results.append(WatchResult(path, job))
            except PbrForgeError as exc:
                # A bad file must not stop the folder. Remember its digest with
                # the error so a scan every five seconds does not spam the same
                # failure; editing the file changes the digest and retries it.
                seen[digest] = {"file": path.name, "error": str(exc),
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                _save_state(folder, state)
                if on_error is not None:
                    on_error(path, str(exc))
        if once:
            return results
        deadline = time.monotonic() + max(1.0, float(interval))
        while time.monotonic() < deadline:
            if stop is not None and stop():
                return results
            time.sleep(0.2)
