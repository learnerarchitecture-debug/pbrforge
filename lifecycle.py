"""Starting, finding and stopping the server process.

WHY THIS MODULE EXISTS
----------------------
There was no way to stop a running PBRForge except finding it in Task Manager,
and that turned out to be actively broken, not merely inconvenient.

`pbrforge.exe` is a PyInstaller `--onefile` build. Those are two processes: a
bootloader that unpacks the bundle to a temporary `_MEIxxxxxx` directory, and the
child that actually runs the application. They share a name and an icon, and the
child is the one that binds the port -- but `Start-Process` and Task Manager both
show the parent, so killing what you can see leaves the server running with the
port still bound and the executable still locked.

That last part is what made this a real bug rather than a papercut. The Inno Setup
uninstaller deletes `pbrforge.exe` while the orphaned child still has it open, so
the uninstall silently leaves the main executable behind and the user is left with
a 8.6 MB file and no idea whether PBRForge is gone. Reproduced on the first
install/uninstall cycle ever run.

The fix is one authoritative record of what is running, written by the process
that is actually serving, plus a stop path that refuses to act when it cannot
positively identify its target.

DESIGN RULES
------------
1. The PID file is an optimisation, never the source of truth. It can be stale --
   the machine crashed, the process was killed, the file was copied. Every read
   is corroborated against the live system before anything is terminated.

2. Refuse rather than guess. If the recorded PID is alive but cannot be shown to
   be a PBRForge server, `stop` reports it and touches nothing. PID reuse is real:
   a config directory left on a USB stick for a week can easily name a PID that
   is now the user's CAD application. Killing that is far worse than not stopping,
   so the failure mode is always "did nothing, here is why".

3. Corroboration is the port. The strongest available evidence that a PID is our
   server is that it currently owns the port the server was told to bind. The
   image name is a weaker second check, used only when no port is recorded.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, __version__
from .netutil import image_name, port_owner_pid, process_alive, terminate

PID_FILENAME = "server.pid"


@dataclass
class ServerRecord:
    """What the PID file says. Not necessarily true; check before acting on it."""

    pid: int
    port: int | None = None
    host: str = ""
    scheme: str = "http"
    started: float = 0.0
    version: str = ""

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started) if self.started else 0.0

    def to_json(self) -> str:
        return json.dumps({
            "pid": self.pid,
            "port": self.port,
            "host": self.host,
            "scheme": self.scheme,
            "started": round(self.started, 3),
            "version": self.version,
        }, indent=2)


def pid_file(cfg) -> Path:
    return Path(cfg.config_dir) / PID_FILENAME


# ------------------------------------------------------------------ write / read
def write(cfg, *, pid: int | None = None, port: int | None = None,
          host: str = "", scheme: str = "http") -> Path:
    """Record the serving process. Called by `serve` once the socket is bound.

    Written after binding rather than before, so the file never describes a
    server that failed to start.
    """
    target = pid_file(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = ServerRecord(
        pid=os.getpid() if pid is None else pid,
        port=port,
        host=host,
        scheme=scheme,
        started=time.time(),
        version=__version__,
    )
    # Atomic: a reader must never see a half-written file and conclude "no server".
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    tmp.write_text(record.to_json(), encoding="utf-8")
    os.replace(tmp, target)
    return target


def clear(cfg, *, pid: int | None = None) -> None:
    """Remove the PID file, but only if it belongs to `pid`.

    The guard matters because more than one server can exist in a config
    directory's lifetime -- a test suite, a restarted service -- and a stale
    process exiting must not delete the record of the live one.
    """
    target = pid_file(cfg)
    try:
        if pid is not None:
            existing = read(cfg)
            if existing is not None and existing.pid != pid:
                return
        target.unlink(missing_ok=True)
    except OSError:
        pass


def read(cfg) -> ServerRecord | None:
    """Parse the PID file. Returns None if absent or unreadable -- never raises."""
    try:
        # utf-8-sig, not utf-8: it strips a byte-order mark if one is present and
        # behaves identically when one is not. The app never writes a BOM, but a
        # hand-edited or PowerShell-written file commonly has one, and without this
        # the leading \ufeff makes json.loads fail and the record vanish.
        raw = pid_file(cfg).read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    try:
        return ServerRecord(
            pid=int(data["pid"]),
            port=int(data["port"]) if data.get("port") is not None else None,
            host=str(data.get("host") or ""),
            scheme=str(data.get("scheme") or "http"),
            started=float(data.get("started") or 0.0),
            version=str(data.get("version") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def unreadable(cfg) -> str | None:
    """If the PID file exists but read() rejected it, say so. Else None.

    Without this, `read() -> None` means two very different things -- "nothing is
    running" and "something tried to record itself and I could not read it" -- and
    `stop` reported the first for both. That is the kind of message that sends a
    user looking for a server that is actually running.
    """
    target = pid_file(cfg)
    try:
        if not target.exists():
            return None
    except OSError:
        return None
    return f"{target} exists but could not be parsed"


# ------------------------------------------------------------------- identify
def identify(record: ServerRecord) -> tuple[bool, str]:
    """Is this record's PID a live PBRForge server? Returns (is_ours, evidence).

    Deliberately conservative. "Probably" is not good enough to justify killing a
    process, so this only says yes on positive evidence.

    The port check is the strongest evidence available, and it is also an
    *invariant* rather than a hint: `write()` runs only after the socket is bound,
    so a record that names a port necessarily describes a process listening on it.
    If nothing is listening there, the record does not describe a live server --
    the file is stale, the PID was recycled, or the server has already exited --
    and the executable name must not be allowed to override that. Letting an image
    called `pbrforge.exe` win in that case is how `stop` would terminate a second
    server belonging to a different config directory.
    """
    if not process_alive(record.pid):
        return False, "the recorded process has exited"

    if record.port:
        owner = port_owner_pid(record.port)
        if owner == record.pid:
            return True, f"it owns port {record.port}"
        if owner is None:
            return False, (f"the recorded port {record.port} has no listener, so this "
                           f"record is stale")
        return False, (f"port {record.port} is held by pid {owner}, "
                       f"not the recorded pid {record.pid}")

    # No port recorded: only reachable for a record written by something other than
    # serve(). Fall back to the executable name, and only accept names that cannot
    # plausibly be something else.
    name = image_name(record.pid)
    if name.startswith("pbrforge"):
        return True, f"the process is {name}"
    if name in ("python.exe", "pythonw.exe", "python", "pythonw"):
        return True, f"the process is {name} and no port was recorded"

    return False, (f"pid {record.pid} is {name or 'an unknown program'} -- "
                   f"refusing to terminate it")


def is_running(cfg) -> ServerRecord | None:
    """The live server for this config directory, or None. Safe to call anytime."""
    record = read(cfg)
    if record is not None:
        ok, _ = identify(record)
        if ok:
            return record
    return _adopt_by_port(cfg)


def _adopt_by_port(cfg) -> ServerRecord | None:
    """Fallback for a server with no PID file: one that is positively ours.

    Two conditions, both required, and both positive evidence rather than a guess:
    the process holds the port this config directory is configured to serve on,
    and its executable is unambiguously PBRForge. That second condition is why a
    `python.exe` on the same port is *not* adopted -- on a machine that runs
    several Python services, "a Python process is listening on a port that appears
    in this config file" is not enough to justify terminating it.

    This exists mainly so uninstalling can still stop a server started by an older
    build, which wrote no PID file at all.
    """
    port = getattr(cfg, "serve_port", None)
    if not port:
        return None
    owner = port_owner_pid(port)
    if owner is None:
        return None
    name = image_name(owner)
    if not name.startswith("pbrforge"):
        return None
    return ServerRecord(pid=owner, port=port, host="", scheme=getattr(cfg, "scheme", "http"))


def status(cfg) -> tuple[bool, str]:
    """(running, human sentence). Used by `stop`, `url`, `doctor` and the selftest."""
    record = read(cfg)

    if record is None:
        adopted = _adopt_by_port(cfg)
        if adopted is not None:
            return True, (f"running: pid {adopted.pid}, port {adopted.port} "
                          f"(found by port; no server.pid file)")
        problem = unreadable(cfg)
        if problem:
            return False, f"unknown: {problem}"
        return False, "no server.pid file, so nothing is recorded as running"

    ok, evidence = identify(record)
    if ok:
        url = f"{record.scheme}://127.0.0.1:{record.port}/" if record.port else "unknown port"
        mins = record.age_seconds / 60
        age = f"{mins:.0f} min" if mins >= 1 else f"{record.age_seconds:.0f} s"
        return True, f"running: pid {record.pid}, {url} (started {age} ago)"

    # Not ours. Distinguish "stale file" from "alive but unidentified", because
    # the second case needs the user, and the first can be cleaned automatically.
    if not process_alive(record.pid):
        adopted = _adopt_by_port(cfg)
        if adopted is not None:
            return True, (f"running: pid {adopted.pid}, port {adopted.port} "
                          f"(found by port; the PID file was stale)")
        return False, f"stale: {evidence}"
    return False, f"not running: {evidence}"


# ----------------------------------------------------------------------- stop
def stop(cfg, *, timeout: float = 8.0, clean_stale: bool = True,
         adopt_by_port: bool = True) -> tuple[bool, str]:
    """Stop the recorded server. Returns (stopped_now, message).

    `stopped_now` is True only when this call actually terminated something. A
    server that was already down returns False with an explanatory message, which
    is not an error -- uninstalling on a machine where the server is not running
    is completely normal.
    """
    record = read(cfg)
    if record is None:
        problem = unreadable(cfg)
        if problem:
            if adopt_by_port:
                adopted = _adopt_by_port(cfg)
                if adopted is not None:
                    return _terminate(cfg, adopted, reason="found by port", timeout=timeout)
            return False, f"nothing to stop: {problem}. Delete it to start clean."
        if adopt_by_port:
            adopted = _adopt_by_port(cfg)
            if adopted is not None:
                reason = "found by port (no PID file)"
                return _terminate(cfg, adopted, reason=reason, timeout=timeout)
        return False, "nothing to stop: no server.pid file"

    ok, evidence = identify(record)
    if not ok:
        if clean_stale and not process_alive(record.pid):
            clear(cfg)
            return False, f"nothing to stop: {evidence} (removed the stale record)"
        # Alive but not identifiable -- leave the file in place so the user can
        # see what it claims, and say plainly that we did nothing.
        if adopt_by_port:
            adopted = _adopt_by_port(cfg)
            if adopted is not None and adopted.pid != record.pid:
                return _terminate(cfg, adopted, reason="found by port instead",
                                  timeout=timeout)
        return False, f"not stopping anything: {evidence}"

    return _terminate(cfg, record, reason="", timeout=timeout)


def _terminate(cfg, record: ServerRecord, *, reason: str, timeout: float
               ) -> tuple[bool, str]:
    if record.pid == os.getpid():
        return False, "that is this process"

    if not terminate(record.pid):
        return False, (f"could not terminate pid {record.pid}. "
                       f"Try closing it from Task Manager.")

    # Wait for the port to actually release. `terminate` returning success only
    # means the signal was delivered, and a stop that reports success while the
    # port is still bound produces a confusing failure on the next start.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if record.port and port_owner_pid(record.port) != record.pid:
            break
        if not process_alive(record.pid):
            break
        time.sleep(0.15)

    clear(cfg)
    where = f"port {record.port}" if record.port else "the process"
    suffix = f" [{reason}]" if reason else ""
    return True, f"stopped pid {record.pid} and released {where}{suffix}"


def describe(cfg) -> str:
    """One line for the console banner, so the stop command is discoverable."""
    return f"{APP_NAME}: run 'pbrforge stop' to shut this server down"
