"""Network helpers: interface enumeration, LAN URL construction, port checks.

Everything here is stdlib. The UDP default-route trick is used instead of parsing
`ipconfig` because parsing localized Windows output is a losing game -- the
interface names and column layout change with the display language.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Interface:
    ip: str
    name: str = ""
    is_private: bool = True

    @property
    def url_host(self) -> str:
        # IPv6 literals need brackets in a URL; IPv4 must not have them.
        return f"[{self.ip}]" if ":" in self.ip else self.ip


def _is_usable(ip: str) -> bool:
    if ip.startswith("127.") or ip == "::1":
        return False
    if ip.startswith("169.254."):        # APIPA -- a failed DHCP lease, never routable
        return False
    if ip.startswith("0."):
        return False
    return True


def primary_ip() -> str:
    """The IP this machine would use to reach the outside world.

    A UDP socket connect() sends no packets; it only makes the OS pick a route,
    which tells us which interface is the real one. Falls back to enumeration
    (and then to loopback) if there is no route at all -- an offline laptop.
    """
    for probe in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 1))
            return s.getsockname()[0]
        except OSError:
            continue
        finally:
            s.close()
    ips = [i.ip for i in interfaces() if ":" not in i.ip]
    return ips[0] if ips else "127.0.0.1"


def interfaces() -> list[Interface]:
    """Usable **IPv4** local addresses, best candidate first.

    IPv4 only, deliberately. The server binds an AF_INET socket, so an IPv6
    address here would be listed as a way in when it is not one. Advertising
    four IPv6 addresses plus one IPv4 address -- which is what a Windows machine
    with a modern ISP hands you -- buries the one address that works. IPv6 support
    would be a real feature (dual-stack binding, bracketed URLs, again for the
    certificate SANs); listing the addresses without it would be a lie.
    """
    found: dict[str, Interface] = {}

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable(ip) and ip not in found:
                found[ip] = Interface(ip=ip, name="hostname")
    except OSError:
        pass

    # A second, independent source. getaddrinfo on the hostname is missing on
    # some Windows configurations (especially with only a Wi-Fi adapter up).
    for probe in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 1))
            ip = s.getsockname()[0]
            if _is_usable(ip) and ip not in found:
                found[ip] = Interface(ip=ip, name="default-route")
        except OSError:
            pass
        finally:
            s.close()

    adapter = _adapter_names()

    def rank(i: Interface) -> tuple[int, str]:
        if i.name == "default-route":
            return (0, i.ip)
        if i.ip.startswith(("192.168.", "10.", "172.")):
            return (1, i.ip)
        return (2, i.ip)

    out = []
    for iface in sorted(found.values(), key=rank):
        name = adapter.get(iface.ip, iface.name)
        out.append(Interface(ip=iface.ip, name=name))
    return out


_ADAPTER_CACHE: dict[str, object] = {"at": 0.0, "map": {}}
_ADAPTER_TTL = 60.0


def _adapter_names() -> dict[str, str]:
    """Map IP -> adapter name, so a wrong-interface problem is diagnosable.

    Cached for a minute because the lookup spawns PowerShell, and this function is
    called by the doctor, the banner, the URL list and the certificate SAN builder.
    Four PowerShell startups at ~400 ms each is a second of nothing on every start.
    """
    import time as _time

    now = _time.monotonic()
    cached = _ADAPTER_CACHE["map"]
    if now - float(_ADAPTER_CACHE["at"]) < _ADAPTER_TTL and isinstance(cached, dict):
        return cached                                  # type: ignore[return-value]

    mapping: dict[str, str] = {}
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-NetIPAddress -AddressFamily IPv4 | "
                 "ForEach-Object { \"$($_.IPAddress)|$($_.InterfaceAlias)\" }"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in out.stdout.splitlines():
                if "|" in line:
                    ip, _, name = line.strip().partition("|")
                    if ip and name:
                        mapping[ip.strip()] = name.strip()
        except Exception:      # noqa: BLE001 - cosmetic only
            mapping = {}

    _ADAPTER_CACHE["at"] = now
    _ADAPTER_CACHE["map"] = mapping
    return mapping


def urls(port: int, scheme: str = "http", include_loopback: bool = True) -> list[tuple[str, str]]:
    """[(label, url)] -- what `pbrforge url` prints and the console banner shows."""
    out: list[tuple[str, str]] = []
    if include_loopback:
        out.append(("This PC", f"{scheme}://127.0.0.1:{port}"))
    for iface in interfaces():
        out.append((f"LAN ({iface.name})", f"{scheme}://{iface.url_host}:{port}"))
    return out


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.35)
        return s.connect_ex((host, port)) == 0


def port_owner_pid(port: int) -> int | None:
    """PID of the process LISTENING on `port`, or None.

    Windows-only, best-effort, and deliberately built on `netstat -ano` rather
    than psutil: this has to work on a bare install with nothing pip-installed,
    and it is the safety check that decides whether `pbrforge stop` is allowed to
    terminate a process. A wrong answer here kills the wrong program, so every
    failure path returns None (meaning "cannot identify", i.e. do not touch it).

    Note it matches only LISTENING rows. A row in TIME_WAIT or ESTABLISHED state
    shares the port number but has no owner worth killing, and matching those
    would make `stop` fire at whatever unrelated process happened to be
    connected.
    """
    if sys.platform != "win32":
        return None
    try:
        net = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True, timeout=8).stdout
    except Exception:
        return None
    for line in net.splitlines():
        parts = line.split()
        # Proto  Local Address  Foreign Address  State  PID
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def process_alive(pid: int) -> bool:
    """True if a process with this PID exists. Never assumes it is *our* process."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=8).stdout
        except Exception:
            return False
        return "no tasks" not in out.lower() and str(pid) in out
    try:
        os.kill(pid, 0)                      # type: ignore[name-defined]
        return True
    except OSError:
        return False


def image_name(pid: int) -> str:
    """Executable name for a PID, lowercased, or "". Used to corroborate identity."""
    if pid <= 0 or sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return ""
    line = out.strip().splitlines()[0] if out.strip() else ""
    return line.split(",")[0].strip('"').lower() if line else ""


def terminate(pid: int, *, force: bool = True) -> bool:
    """Ask a process to exit. Returns True if the request was delivered.

    On Windows this is TerminateProcess; there is no SIGTERM to be polite with,
    and the server holds no state that needs flushing (it writes cache files
    atomically and the config is already on disk), so a hard kill is safe here.
    """
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            out = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                 capture_output=True, text=True, timeout=15)
            return out.returncode == 0
        os.kill(pid, signal.SIGTERM)         # type: ignore[name-defined]
        return True
    except Exception:
        return False


def who_holds_port(port: int) -> str:
    """Best-effort: name the process holding a port, so the error is actionable."""
    pid = port_owner_pid(port)
    if pid is None:
        return ""
    name = image_name(pid)
    return f"{name} (pid {pid})" if name else f"pid {pid}"


def local_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "localhost"


def free_port(start: int, end: int = 0) -> int:
    """First free TCP port at or after `start`. Used by `serve --auto-port` and tests."""
    end = end or start + 50
    for p in range(start, end + 1):
        if not port_in_use("127.0.0.1", p):
            return p
    raise RuntimeError(f"no free port in {start}..{end}")
