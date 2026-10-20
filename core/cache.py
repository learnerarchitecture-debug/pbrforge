"""Content-addressed artifact cache.

Two properties matter, and both are about *trust*:

  1. A cache hit must be byte-identical to what a fresh computation would produce.
     That is guaranteed by keying on the code version, every parameter, and every
     upstream artifact hash -- never on a timestamp or a filename.
  2. A crash must never leave a half-written artifact behind. Every write goes to
     a temp file and is then atomically renamed on the same volume.

Locking note (important for the multi-PC setup): this uses **lock files created
with O_CREAT|O_EXCL**, not OS byte-range locking. `msvcrt.locking` and `fcntl`
both work locally, but byte-range locks over SMB/NFS are unreliable enough that
two machines can both believe they hold the lock. O_EXCL create is atomic on
network filesystems because the server arbitrates it. The trade-off is that a
crashed process leaves a stale lock file, which is handled by an age check.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

LOCK_STALE_SECONDS = 600        # a lock older than this is presumed abandoned
CHUNK = 1 << 20


class CacheError(RuntimeError):
    """Both a stage, a reason and a fix -- per the project's error convention."""

    def __init__(self, stage: str, reason: str, fix: str = "") -> None:
        self.stage, self.reason, self.fix = stage, reason, fix
        parts = [f"{stage} failed", f"reason   {reason}"]
        if fix:
            parts.append(f"fix      {fix}")
        super().__init__("\n".join(parts))


@dataclass(frozen=True)
class CacheEntry:
    key: str
    path: Path
    size: int

    @property
    def age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            return 0.0


def hash_file(path: str | Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_params(parts: tuple[object, ...]) -> str:
    """Stable hash of a parameter set. Keys are sorted so dict ordering is irrelevant."""
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, dict):
            for k in sorted(part, key=str):
                h.update(repr(k).encode("utf-8"))
                h.update(b"\x1f")
                h.update(repr(part[k]).encode("utf-8"))
                h.update(b"\x1e")
        else:
            h.update(repr(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Cache:
    """A directory of artifacts named by the hash of their inputs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ keys
    @staticmethod
    def key(*parts: object) -> str:
        return hash_params(parts)[:32]

    def artifact(self, key: str, name: str) -> Path:
        """`key` must be 8-32 hex chars; it is split into two levels to keep
        directories small. A flat directory with 50 000 files is slow to list on
        Windows and worse over SMB."""
        safe = "".join(c for c in key if c in "0123456789abcdef")[:32] or "0"
        safe = safe.zfill(8)
        safe = safe if len(safe) <= 8 else safe
        name = Path(name).name            # never allow a caller to escape the cache
        return self.root / safe[:2] / safe[2:4] / safe / name

    # ------------------------------------------------------------------ reads
    def has(self, key: str, name: str) -> bool:
        p = self.artifact(key, name)
        return p.is_file() and p.stat().st_size > 0

    def get(self, key: str, name: str) -> Path | None:
        p = self.artifact(key, name)
        if p.is_file() and p.stat().st_size > 0:
            try:
                os.utime(p, None)         # touch: keeps hot artifacts off the prune list
            except OSError:
                pass
            return p
        return None

    def lookup(self, key: str) -> list[CacheEntry]:
        d = self.artifact(key, "x").parent
        if not d.is_dir():
            return []
        out = []
        for f in sorted(d.iterdir()):
            if f.is_file() and not f.name.endswith(".tmp") and not f.name.endswith(".lock"):
                out.append(CacheEntry(key=key, path=f, size=f.stat().st_size))
        return out

    # ----------------------------------------------------------------- writes
    def put_bytes(self, key: str, name: str, data: bytes) -> Path:
        return self._atomic(key, name, lambda fh: fh.write(data))

    def put_file(self, key: str, name: str, src: str | Path) -> Path:
        """Move a freshly produced artifact into the cache.

        Written via a temp file in the *same directory* so os.replace is atomic.
        A temp file in the system temp dir would be a cross-volume copy, which is
        not atomic and is exactly how half-written artifacts get into a cache.
        """
        target = self.artifact(key, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".tmp{os.getpid()}")
        with open(src, "rb") as r, open(tmp, "wb") as w:
            shutil.copyfileobj(r, w, CHUNK)
            w.flush()
            os.fsync(w.fileno())
        os.replace(tmp, target)
        return target

    def _atomic(self, key: str, name: str, write_fn: Callable[[object], None]) -> Path:
        target = self.artifact(key, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".tmp{os.getpid()}")
        try:
            with open(tmp, "wb") as fh:
                write_fn(fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return target

    # ------------------------------------------------------------------ locks
    @contextmanager
    def lock(self, key: str, timeout: float = 120.0) -> Iterator[None]:
        """Mutual exclusion for one artifact, safe across processes AND machines."""
        lock_path = self.artifact(key, "stage.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}@{time.time():.0f}".encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > LOCK_STALE_SECONDS:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise CacheError(
                        "cache.lock",
                        f"could not acquire the lock on {key} within {timeout:.0f}s "
                        f"-- another run is probably holding it",
                        "wait for the other run to finish, or delete "
                        f"{lock_path} if you are certain nothing else is running",
                    ) from None
                time.sleep(0.15)
        try:
            yield
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    # --------------------------------------------------------------- upkeep
    def size_bytes(self) -> int:
        total = 0
        for f in self.root.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        return total

    def prune(self, older_than_days: float = 30.0, keep_keys: set[str] | None = None) -> tuple[int, int]:
        """Delete artifacts last touched more than N days ago. Returns (files, bytes)."""
        cutoff = time.time() - older_than_days * 86400
        keep_keys = keep_keys or set()
        files = 0
        freed = 0
        for f in list(self.root.rglob("*")):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                if f.name.endswith(".lock") and st.st_mtime < time.time() - LOCK_STALE_SECONDS:
                    f.unlink()
                    continue
                if st.st_mtime >= cutoff:
                    continue
                if f.parent.name in keep_keys:
                    continue
                freed += st.st_size
                files += 1
                f.unlink()
            except OSError:
                continue
        # drop directories that are now empty
        for d in sorted((p for p in self.root.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        return files, freed


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
