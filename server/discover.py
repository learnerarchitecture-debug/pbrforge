"""LAN discovery over UDP broadcast.

Why broadcast rather than mDNS: this has to work on a network run by a domestic
router, on Windows, with no dependencies. Windows' built-in mDNS support is
patchy, `zeroconf` is a dependency, and Bonjour is not guaranteed to be present.
A one-datagram request/response protocol on a fixed port is 40 lines and works
everywhere -- including across a cheap switch that filters multicast.

Contract:
  request   b"PBRFORGE?"  ->  datagram to 255.255.255.255:DISCOVERY_PORT
  response  JSON object, UTF-8, to the sender:
            {"app":"pbrforge","version":"0.1.0","host":"DESKTOP-1",
             "scheme":"https","port":7443,"auth":true,"library":"D:\\textures"}

The response deliberately contains nothing sensitive: no secret, no pairing code,
no file listing. Finding a server must not be the same as being allowed into it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import DISCOVERY_MAGIC, DISCOVERY_PORT, __version__


@dataclass
class Announcement:
    host: str
    address: str
    port: int
    scheme: str = "http"
    version: str = __version__
    app: str = "pbrforge"
    auth: bool = True
    library: str = ""

    @property
    def url(self) -> str:
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.scheme}://{host}:{self.port}"

    def describe(self) -> str:
        lock = "pair code required" if self.auth else "OPEN (no auth)"
        return f"{self.host:<24} {self.url:<34} {self.version:<8} {lock}"


class Beacon(threading.Thread):
    """Answers discovery requests. Daemon thread; failures are never fatal.

    A discovery responder that can crash the app is worse than no discovery, so
    every socket error here is swallowed and logged once.
    """

    def __init__(self, payload_fn: Callable[[], dict], port: int = DISCOVERY_PORT) -> None:
        super().__init__(name="pbrforge-discovery", daemon=True)
        self.payload_fn = payload_fn
        self.port = port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(("", self.port))
            self._sock = sock
        except OSError as exc:
            print(f"note: LAN discovery disabled ({exc})")
            return

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(DISCOVERY_MAGIC):
                continue
            try:
                reply = json.dumps(self.payload_fn(), separators=(",", ":")).encode("utf-8")
                sock.sendto(reply, addr)
            except Exception:      # noqa: BLE001 - never let the beacon take the server down
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


@dataclass
class DiscoverResult:
    found: list[Announcement] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _payload_to_announcement(payload: dict, address: str) -> Announcement | None:
    if payload.get("app") != "pbrforge":
        return None
    return Announcement(
        host=str(payload.get("host") or address),
        address=address,
        port=int(payload.get("port") or 0),
        scheme=str(payload.get("scheme") or "http"),
        version=str(payload.get("version") or "?"),
        auth=bool(payload.get("auth", True)),
        library=str(payload.get("library") or ""),
    )


def probe(address: str, port: int = DISCOVERY_PORT, timeout: float = 1.0) -> Announcement | None:
    """Unicast discovery against a known address.

    Needed for two cases a broadcast cannot serve: verifying a specific server
    (`pbrforge discover --host 192.168.1.50`), and the self-test, where relying on
    broadcast would make the result depend on the network's mood.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        sock.settimeout(timeout)
        sock.sendto(DISCOVERY_MAGIC, (address, port))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                return None
            except OSError:
                return None
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            found = _payload_to_announcement(payload, addr[0])
            if found is not None:
                return found
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def discover(timeout: float = 1.5, port: int = DISCOVERY_PORT) -> DiscoverResult:
    """Broadcast a request and collect replies for `timeout` seconds."""
    result = DiscoverResult()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
    except OSError as exc:
        result.errors.append(f"cannot open a discovery socket: {exc}")
        return result

    targets = ["255.255.255.255"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.count(".") == 3 and not ip.startswith("127."):
                octets = ip.split(".")
                targets.append(f"{octets[0]}.{octets[1]}.{octets[2]}.255")
    except OSError:
        pass

    seen: set[str] = set()
    with sock:
        for target in dict.fromkeys(targets):
            try:
                sock.sendto(DISCOVERY_MAGIC, (target, port))
            except OSError as exc:
                result.errors.append(f"broadcast to {target} failed: {exc}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as exc:
                result.errors.append(f"receive failed: {exc}")
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            found = _payload_to_announcement(payload, addr[0])
            if found is None:
                continue
            key = f"{found.address}:{found.port}"
            if key in seen:
                continue
            seen.add(key)
            result.found.append(found)
    return result
