"""The local server. Standard library only, by design.

The v2 security and studio modules it calls may import `cryptography` lazily for
vault/signature work; the HTTP server itself remains stdlib.

Why not FastAPI/uvicorn, despite docs/ARCHITECTURE.md originally specifying them:

  * The installable build has to run on a second PC where nothing is pip-installed.
    A single-file exe carrying only the stdlib is ~11 MB; adding a full ASGI stack
    plus pydantic plus multipart is ~90 MB and needs a compiler-free wheel set that
    is not guaranteed on an arbitrary Windows box.
  * The actual traffic profile is one HTML page, a handful of JSON endpoints, and
    eventually a multipart upload. `ThreadingHTTPServer` handles that at LAN speeds
    with no measurable difference.
  * Debuggability on Windows. When something goes wrong at 11pm on a machine you
    are not sitting in front of, a stdlib traceback in a console window beats an
    ASGI worker that has swallowed the exception.

ARCHITECTURE.md has been amended to record this. WebSockets are the one thing that
would change the answer, and they are not needed yet: job progress is polled.

What this module deliberately implements, because the LAN story depends on it:
  * pairing-code auth with a signed, stateless session cookie
  * an unauthenticated /api/ping that reveals only the app name and version
  * correct Content-Length on HTTP/1.1 keep-alive (a wrong length hangs the browser)
  * a real 404 for path traversal attempts rather than a directory listing
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .. import APP_NAME, __version__
from .. import lifecycle
from ..config import Config
from ..netutil import local_hostname, port_in_use, primary_ip, urls, who_holds_port
from ..resources import index_file, searched_paths, ui_dir
from . import auth as auth_mod
from .auth import AUTH_EXEMPT, COOKIE_NAME, Auth
from .discover import Beacon

STARTED_AT = time.time()


# ------------------------------------------------------------------- rate limit
class RateLimiter:
    """Small per-client token bucket for authenticated API traffic.

    This is not a promise to absorb a flood; a LAN peer can always saturate a
    link.  It is here so a broken browser loop or a curious guest cannot fill the
    staging directory or make the single pipeline worker useless for everyone
    else.  The server stays single-worker by design, so the write bucket is much
    tighter than the read bucket.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str, *, kind: str = "read") -> bool:
        now = time.monotonic()
        limit, window = (120, 60.0) if kind == "read" else (18, 60.0)
        with self._lock:
            bucket = self._events.setdefault(key + ":" + kind, deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            self._seen[key] = now
            # Keep the map bounded even if a scanner rotates source ports.  The
            # check is cheap and happens only after a bucket has been touched.
            if len(self._seen) > 4096:
                cutoff = now - 3600
                for old in [k for k, stamp in self._seen.items() if stamp < cutoff]:
                    self._seen.pop(old, None)
                    self._events.pop(old + ":read", None)
                    self._events.pop(old + ":write", None)
            return True


# --------------------------------------------------------------------- payload
@dataclass
class App:
    config: Config
    auth: Auth
    verbose: bool = False
    log_file: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    requests: int = 0
    jobs: Any = None
    update_info: Any = None
    update_checked: float = 0.0
    limiter: RateLimiter = field(default_factory=RateLimiter, repr=False)
    _update_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _update_thread: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.jobs is None:
            from ..jobs import JobManager
            self.jobs = JobManager(self.config, workers=1)
        if self.update_info is None:
            from ..update import load_cached
            self.update_info = load_cached(self._update_cache, max_age_s=24 * 3600)
            if self.update_info is not None:
                self.update_checked = self.update_info.checked_at

    @property
    def _update_cache(self) -> Path:
        return Path(self.config.config_dir) / "update.json"

    def ping_payload(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "app": "pbrforge",
            "version": __version__,
            "host": local_hostname(),
            "scheme": cfg.scheme,
            "port": cfg.serve_port,
            "auth": cfg.auth,
            "library": str(cfg.library),
            "features": ["upload", "pbr", "library", "library-metadata",
                         "updates", "projects", "capture", "surface", "setdoctor",
                         "delivery", "encrypted-vault", "security-audit",
                         "watch-cli", "plugins-cli"],
            "vault": True,
            "signature_required": True,
        }

    def update_payload(self, *, force: bool = False) -> dict[str, Any]:
        """Cached update state; a forced call blocks and asks GitHub.

        The UI calls the non-forced form on load. That is deliberately *not* a
        network call unless the cache is empty, in which case a daemon thread
        checks in the background and the next poll has the answer. A local tool
        must open instantly even when the network is down.
        """
        from .. import update as update_mod
        settings = {"enabled": bool(self.config.update_check),
                    "auto_install": bool(self.config.update_auto_install),
                    "repo": self.config.update_repo}
        if not self.config.update_check and not force:
            return {"current": __version__, "available": False, "error": "",
                    **settings}
        now = time.time()
        info = self.update_info
        if info is not None and not force and (now - self.update_checked) < 3600:
            return {**info.to_dict(), **settings}
        if force:
            info = update_mod.check(self.config.update_repo)
            update_mod.save_cached(self._update_cache, info)
            self.update_info = info
            self.update_checked = now
            if (info.available and self.config.update_auto_install
                    and info.asset_url and not info.error):
                threading.Thread(target=self._auto_install, args=(info,),
                                 daemon=True, name="pbrforge-auto-update").start()
            return {**info.to_dict(), **settings}
        with self._update_lock:
            if self._update_thread is None or not self._update_thread.is_alive():
                self._update_thread = threading.Thread(
                    target=self._background_check, daemon=True,
                    name="pbrforge-update-check")
                self._update_thread.start()
        return {"current": __version__, "checking": True,
                "available": bool(info.available) if info else False,
                **(info.to_dict() if info else {}), **settings}

    def _background_check(self) -> None:
        from .. import update as update_mod
        info = update_mod.check(self.config.update_repo)
        update_mod.save_cached(self._update_cache, info)
        self.update_info = info
        self.update_checked = time.time()
        if (info.available and self.config.update_auto_install
                and info.asset_url and not info.error):
            self._auto_install(info)

    def _auto_install(self, info: Any) -> None:
        from .. import update as update_mod
        try:
            target = update_mod.install(info)
            print(f"  update   launched {target.name}; the installer will stop "
                  f"the server and restart it", flush=True)
        except Exception as exc:      # noqa: BLE001 - never kill the server for an update
            print(f"  update   auto-install failed: {exc}", flush=True)

    def health_payload(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "app": "pbrforge",
            "version": __version__,
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
            "host": local_hostname(),
            "primary_ip": primary_ip(),
            "scheme": cfg.scheme,
            "port": cfg.serve_port,
            "http_port": cfg.port,
            "https_port": cfg.https_port,
            "tls": cfg.tls,
            "auth": cfg.auth,
            "discovery": cfg.discovery,
            "library": str(cfg.library),
            "library_exists": cfg.library.exists(),
            "cache": str(cfg.cache),
            "config_file": str(cfg.config_file),
            "config_source": cfg.source,
            "requests": self.requests,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


# ----------------------------------------------------------------- pair page
PAIR_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair with PBRForge</title>
<style>
:root{--bg:#0B0D10;--panel:#12151A;--elev:#191D24;--border:#242A33;--text:#E6E9EF;
--dim:#A6AEBC;--muted:#6E7787;--accent:#E0A458;--fail:#F2707A;--ok:#5FD08A;
--font:"Inter","Segoe UI Variable",system-ui,sans-serif;--mono:"JetBrains Mono","Cascadia Mono",ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:400 14px/20px var(--font);
min-height:100vh;display:grid;place-items:center;padding:24px}
.card{width:min(460px,100%);background:var(--panel);border:1px solid var(--border);
border-radius:10px;padding:28px}
.glyph{width:26px;height:26px;border-radius:5px;background:linear-gradient(135deg,var(--accent),#C88F45);
display:grid;place-items:center;font-size:15px;color:#1a1206;font-weight:800;margin-bottom:16px}
h1{font-size:19px;font-weight:600;margin-bottom:6px}
p{color:var(--dim);margin-bottom:18px;font-size:13px}
label{display:block;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
color:var(--muted);margin-bottom:7px}
input{width:100%;padding:13px 15px;border-radius:6px;background:var(--elev);
border:1px solid var(--border);color:var(--text);font:600 22px/1 var(--mono);
letter-spacing:.14em;text-align:center;text-transform:uppercase}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(224,164,88,.15)}
button{width:100%;margin-top:16px;padding:11px;border:none;border-radius:6px;
background:var(--accent);color:#1a1206;font:600 14px var(--font);cursor:pointer}
button:hover{background:#F0B468}
.err{margin-top:14px;padding:11px 13px;border-radius:6px;background:rgba(242,112,122,.12);
border-left:3px solid var(--fail);color:#e8a3a9;font-size:13px}
.hint{margin-top:18px;padding-top:16px;border-top:1px solid var(--border);
font-size:12px;color:var(--muted);line-height:1.6}
code{font-family:var(--mono);font-size:12px;color:var(--accent)}
</style></head><body>
<div class="card">
  <div class="glyph">&#9670;</div>
  <h1>Pair this device</h1>
  <p>Enter the pairing code shown in the PBRForge console on <b>__HOST__</b>.</p>
  <form method="POST" action="/pair">
    <label for="code">Pairing code</label>
    <input id="code" name="code" autocomplete="off" autocapitalize="characters"
           spellcheck="false" placeholder="XXXX-XXXX" autofocus maxlength="12">
    <button type="submit">Pair</button>
  </form>
  __ERROR__
  <div class="hint">
    The code is derived from this server's secret and stays the same until you rotate it.
    Run <code>pbrforge code</code> on the server to see it again, or
    <code>pbrforge code --rotate</code> to revoke every paired device at once.
  </div>
</div></body></html>
"""


# ----------------------------------------------------------------- the handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{APP_NAME}/{__version__}"
    sys_version = ""

    # Never echo the client's file path into the log: it can contain a pairing
    # token if someone pastes an authenticated URL around.
    def log_message(self, fmt: str, *args: Any) -> None:
        app: App = self.server.app       # type: ignore[attr-defined]
        if not app.verbose:
            return
        print(f"  {self.address_string()}  {fmt % args}", flush=True)

    # ---------------------------------------------------------------- plumbing
    @property
    def app(self) -> App:
        return self.server.app           # type: ignore[attr-defined]

    def _client(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    def _cookies(self) -> dict[str, str]:
        return Auth.parse_cookie(self.headers.get("Cookie"))

    def _authed(self) -> bool:
        if not self.app.config.auth:
            return True
        return self.app.auth.verify_session(self._cookies().get(COOKIE_NAME))

    def _session(self) -> str:
        return self._cookies().get(COOKIE_NAME, "")

    def _csrf_ok(self) -> bool:
        if not self.app.config.auth:
            return True
        return self.app.auth.verify_csrf(self._session(),
                                         self.headers.get("X-CSRF-Token"))

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        limit = self.app.config.max_upload_mb * 1024 * 1024
        if length > limit:
            return b""
        return self.rfile.read(length) if length else b""

    def _security_headers(self) -> list[tuple[str, str]]:
        headers = [
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("X-Permitted-Cross-Domain-Policies", "none"),
            ("Permissions-Policy", "geolocation=(), camera=(), microphone=(), usb=()"),
            # The UI is one self-contained file with inline style and script, so
            # 'unsafe-inline' is required. Everything is served from this origin
            # and nothing is loaded from the network, which is the trade-off being
            # made -- documented in docs/DEPLOYMENT.md rather than left implicit.
            ("Content-Security-Policy",
             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
             "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' blob:; "
             "connect-src 'self'; form-action 'self'; object-src 'none'; "
             "base-uri 'none'; frame-ancestors 'none'"),
        ]
        if self.app.config.tls:
            # Only send HSTS over TLS. Sending it on plain HTTP is ignored by
            # browsers, and worse, it hides that the transport is still insecure.
            headers.append(("Strict-Transport-Security",
                            "max-age=31536000; includeSubDomains"))
        return headers

    def _host_ok(self) -> tuple[bool, str]:
        """Reject Host headers that do not name this machine.

        DNS rebinding works by making attacker.example resolve to 192.168.x.x
        after the browser has accepted the origin.  An allow-list of local names
        and private IP literals stops that without needing a certificate per PC.
        A custom hostname can be added to the OS hosts file or reached by IP.
        """
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            return False, "the request has no Host header"
        try:
            parsed = urlparse("//" + raw)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError:
            return False, "the Host header is malformed"
        if not host:
            return False, "the Host header has no hostname"
        local = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
        machine = ""
        try:
            machine = (local_hostname() or "").lower()
            local.add(machine)
            local.add((primary_ip() or "").lower())
        except Exception:       # noqa: BLE001 - host detection is best effort
            pass
        if ((machine and host == machine) or host.endswith(".local")
                or host.endswith(".localhost") or host in local):
            if not self._host_port_ok(port):
                return False, "the Host port does not match this server"
            return True, ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False, (
                f"Host {host!r} is not a local name. Use localhost, the machine's "
                f"hostname, a *.local name, or a private IP address.")
        if address.is_loopback or address.is_private or address.is_link_local:
            if not self._host_port_ok(port):
                return False, "the Host port does not match this server"
            return True, ""
        return False, "public IP addresses are not accepted as a Host header"

    def _host_port_ok(self, port: int | None) -> bool:
        """An explicit Host port must be one this server actually listens on."""
        if port is None:
            return True
        server = self.server.server_address
        allowed = {server[1] if len(server) > 1 else None,
                   self.app.config.port, self.app.config.https_port}
        allowed.discard(None)
        allowed.discard(0)
        return port in allowed

    def _send(self, status: int, body: bytes, content_type: str,
              extra: list[tuple[str, str]] | None = None, *, cache: str = "no-store",
              etag: str | None = None, head_only: bool = False) -> None:
        app = self.app
        with app._lock:
            app.requests += 1

        headers: list[tuple[str, str]] = [("Content-Type", content_type)]
        headers += self._security_headers()
        headers.append(("Cache-Control", cache))
        if etag:
            headers.append(("ETag", etag))

        payload = body
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        compressible = (content_type.startswith("text/")
                        or "json" in content_type or "javascript" in content_type
                        or "svg" in content_type)
        if accepts_gzip and compressible and len(body) > 1024 and not head_only:
            payload = gzip.compress(body, 6)
            headers.append(("Content-Encoding", "gzip"))
            headers.append(("Vary", "Accept-Encoding"))

        # Content-Length is mandatory: without it the browser waits forever on an
        # HTTP/1.1 keep-alive connection, which presents as "the app loaded but
        # nothing works".
        headers.append(("Content-Length", str(len(payload))))

        self.send_response(status)
        for key, value in headers + (extra or []):
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass       # client navigated away mid-response; not our problem

    def _send_json(self, status: int, data: Any, *, cache: str = "no-store") -> None:
        body = json.dumps(data, indent=2, sort_keys=False).encode("utf-8") + b"\n"
        self._send(status, body, "application/json; charset=utf-8", cache=cache)

    def _error(self, status: int, title: str, detail: str = "",
               fix: str = "") -> None:
        body = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{status} {title}</title></head>"
                f"<body style='background:#0B0D10;color:#E6E9EF;"
                f"font:14px/1.6 ui-monospace,Consolas,monospace;padding:40px;max-width:80ch'>"
                f"<h1 style='font-size:17px;margin-bottom:14px'>{status} &middot; {title}</h1>"
                f"<p style='color:#A6AEBC'>{detail}</p>"
                + (f"<p style='color:#E0A458;margin-top:14px'>fix &nbsp;{fix}</p>" if fix else "")
                + "</body></html>").encode("utf-8")
        self._send(status, body, "text/html; charset=utf-8")

    # ------------------------------------------------------------------ routing
    def do_GET(self) -> None:            # noqa: N802 - http.server's API
        self._route("GET")

    def do_HEAD(self) -> None:           # noqa: N802
        self._route("HEAD")

    def do_POST(self) -> None:           # noqa: N802
        self._route("POST")

    def do_PATCH(self) -> None:          # noqa: N802
        self._route("PATCH")

    def do_DELETE(self) -> None:         # noqa: N802
        self._route("DELETE")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        head_only = method == "HEAD"

        try:
            host_ok, host_detail = self._host_ok()
            if not host_ok:
                self._send_json(400, {"error": host_detail})
                return

            # ---- unauthenticated -------------------------------------------
            if path == "/api/ping":
                self._send_json(200, self.app.ping_payload(), cache="no-store")
                return

            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return

            if path == "/favicon.svg":
                svg = (b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                       b"<rect width='32' height='32' rx='7' fill='#E0A458'/>"
                       b"<text x='16' y='23' font-size='20' text-anchor='middle'"
                       b" fill='#1a1206'>&#9670;</text></svg>")
                self._send(200, svg, "image/svg+xml", cache="public, max-age=86400")
                return

            if path.startswith("/pair"):
                self._handle_pair(method, parsed.query)
                return

            # ---- authenticated ---------------------------------------------
            if not self._authed():
                if path.startswith("/api/"):
                    self._send_json(401, {"error": "not paired",
                                          "pair_url": "/pair"})
                else:
                    self._send(302, b"", "text/plain",
                               extra=[("Location", f"/pair?next={quote(path)}")])
                return

            if method in ("POST", "PATCH", "PUT", "DELETE"):
                if not self._same_origin():
                    self._send_json(403, {"error": "cross-origin write refused"})
                    return
                if not self._csrf_ok():
                    self._send_json(403, {"error": "missing or invalid CSRF token",
                                          "session": "/api/session"})
                    return
                if not self.app.limiter.allow(self._client(), kind="write"):
                    self._send_json(429, {
                        "error": "too many write requests; wait a minute and retry",
                    })
                    return
            elif path.startswith("/api/"):
                if not self.app.limiter.allow(self._client(), kind="read"):
                    self._send_json(429, {
                        "error": "too many API requests; wait a minute and retry",
                    })
                    return

            if path == "/api/session":
                self._send_json(200, {
                    "app": APP_NAME, "version": __version__,
                    "authenticated": True, "auth": bool(self.app.config.auth),
                    "tls": bool(self.app.config.tls),
                    "csrf": (self.app.auth.csrf_token(self._session())
                             if self.app.config.auth else ""),
                })
                return

            if path == "/api/health":
                self._send_json(200, self.app.health_payload())
                return

            if path == "/api/presets":
                self._send_json(200, self._presets_payload())
                return

            if path == "/api/security":
                from .. import security
                self._send_json(200, security.audit(self.app.config))
                return

            if path == "/api/projects":
                self._handle_projects(method)
                return

            if path.startswith("/api/projects/"):
                self._handle_project(path, method)
                return

            if path == "/api/capture" and method == "POST":
                self._handle_capture(parsed.query)
                return

            if path == "/api/surface":
                self._handle_surface(method, parsed.query)
                return

            if path == "/api/vault" and method == "POST":
                self._handle_vault()
                return

            if path == "/api/jobs":
                if method == "POST":
                    self._handle_upload(parsed.query)
                else:
                    self._send_json(200, {"jobs": self.app.jobs.recent(),
                                          "counts": self.app.jobs.counts()})
                return

            if path.startswith("/api/jobs/"):
                self._handle_job(path, method)
                return

            if path == "/api/library":
                query = self._query_one(parsed.query, "q", "").strip()
                tag = self._query_one(parsed.query, "tag", "").strip()
                category = self._query_one(parsed.query, "category", "").strip()
                favorite_raw = self._query_one(parsed.query, "favorite", "").lower()
                favorite = (True if favorite_raw in ("1", "true", "yes", "on")
                            else False if favorite_raw in ("0", "false", "no", "off")
                            else None)
                items = self.app.jobs.list_library(query=query, tag=tag,
                                                   favorite=favorite,
                                                   category=category)
                self._send_json(200, {"materials": items,
                                      "tags": self._library_tags(items),
                                      "query": query})
                return

            if path.startswith("/api/library/"):
                self._handle_library(path, method)
                return

            if path == "/api/update":
                force = "check=1" in (parsed.query or "")
                self._send_json(200, self.app.update_payload(force=force))
                return

            if path == "/api/update/install" and method == "POST":
                self._handle_update_install()
                return

            if path == "/api/settings" and method == "POST":
                self._handle_settings()
                return

            if path == "/api/logout":
                cookie = auth_mod.session_cookie("", secure=bool(self.app.config.tls),
                                                 max_age=0)
                self._send(200, b"unpaired\n", "text/plain",
                           extra=[("Set-Cookie", cookie)])
                return

            if path in ("/", "/index.html"):
                self._serve_index(head_only)
                return

            if path.startswith("/ui/"):
                self._serve_static(path[len("/ui/"):], head_only)
                return

            self._error(404, "Not found", f"No route matches <code>{path}</code>.",
                        "Check the URL, or open <code>/</code> for the app.")
        except Exception as exc:            # noqa: BLE001 - a 500 must never kill the server
            import traceback
            traceback.print_exc()
            try:
                self._error(500, "Internal error", f"{type(exc).__name__}: {exc}",
                            "See the console on the server for the full traceback.")
            except Exception:
                pass

    # --------------------------------------------------------------- handlers
    @staticmethod
    def _query_one(query: str, key: str, default: str = "") -> str:
        values = parse_qs(query or "").get(key)
        return values[0] if values else default

    @staticmethod
    def _bool_param(query: str, key: str, default: bool) -> bool:
        raw = Handler._query_one(query, key, "1" if default else "0").lower()
        return raw not in ("0", "false", "no", "off", "")

    def _same_origin(self) -> bool:
        """Reject a cross-site form post. SameSite=Strict already blocks most of it."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host:
            return True
        # Some privacy-hardened Chromium shells and proxied form posts report
        # `Origin: null` even for a same-origin navigation.  Browsers that do
        # this still send Sec-Fetch-Site, which an attacker cannot forge, so use
        # it as the fallback rather than breaking pairing for those clients.
        if origin.strip().lower() == "null":
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            return site != "cross-site"
        try:
            parsed = urlparse(origin)
            scheme = "https" if self.app.config.tls else "http"
            return (parsed.scheme == scheme
                    and parsed.netloc.lower() == host.lower())
        except ValueError:
            return False

    def _presets_payload(self) -> dict[str, Any]:
        from ..export import presets as preset_mod
        items = []
        for name in preset_mod.available():
            try:
                preset = preset_mod.load(name)
            except Exception:      # noqa: BLE001 - one bad preset must not hide the rest
                continue
            items.append({
                "name": preset.name,
                "label": preset.label,
                "target": preset.target_renderer,
                "verified": bool(preset.verified),
                "default": bool(preset.default),
            })
        return {"presets": items, "default": preset_mod.default_preset()}

    # --------------------------------------------------------- studio helpers
    def _read_body_limit(self, limit: int) -> tuple[bytes, str]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b"", "Content-Length is not an integer"
        if length <= 0:
            return b"", "the request has no body"
        if length > limit:
            return b"", f"the request is {length / 1e6:.1f} MB, above the {limit / 1e6:.1f} MB limit"
        body = self.rfile.read(length)
        if len(body) != length:
            return b"", "the request ended early"
        return body, ""

    def _json_body(self, limit: int = 2 * 1024 * 1024) -> tuple[dict[str, Any], str]:
        body, error = self._read_body_limit(limit)
        if error:
            return {}, error
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            return {}, "the body must be a JSON object"
        if not isinstance(payload, dict):
            return {}, "the body must be a JSON object"
        return payload, ""

    def _project_store(self) -> Any:
        from ..project import ProjectStore
        return ProjectStore(Path(self.app.config.config_dir) / "projects")

    def _handle_projects(self, method: str) -> None:
        from ..core.errors import PbrForgeError
        store = self._project_store()
        if method == "GET":
            projects = []
            for item in store.list():
                projects.append({
                    "id": item.get("id"), "name": item.get("name"),
                    "created": item.get("created"), "updated": item.get("updated"),
                    "width": item.get("width"), "height": item.get("height"),
                    "layers": len(item.get("layers") or []),
                    "last_job": item.get("last_job") or "",
                })
            self._send_json(200, {"projects": projects})
            return
        if method != "POST":
            self._send_json(405, {"error": "use GET or POST for /api/projects"})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype not in ("image/png", "application/octet-stream"):
            self._send_json(415, {"error": "a project source must be a PNG body"})
            return
        body, error = self._read_body_limit(int(self.app.config.max_upload_mb) * 1024 * 1024)
        if error:
            self._send_json(413 if "above" in error else 400, {"error": error})
            return
        name = self._query_one(urlparse(self.path).query, "name", "Untitled project")[:80]
        source_name = self._query_one(urlparse(self.path).query, "filename", "source.png")[:160]
        try:
            project = store.create(name, body, source_name=source_name)
        except PbrForgeError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(201, {"project": project.to_dict(),
                             "source": f"/api/projects/{project.id}/source"})

    def _handle_project(self, path: str, method: str) -> None:
        from ..core.errors import PbrForgeError
        parts = [part for part in path.strip("/").split("/") if part]
        # api / projects / <id> / [action]
        if len(parts) < 3:
            self._send_json(404, {"error": "no project id in the path"})
            return
        project_id = parts[2]
        store = self._project_store()
        if len(parts) == 3:
            if method == "GET":
                try:
                    project, _source = store.load(project_id)
                except PbrForgeError as exc:
                    self._send_json(404, {"error": str(exc)})
                    return
                self._send_json(200, {"project": project.to_dict(),
                                     "source": f"/api/projects/{project_id}/source"})
                return
            if method == "PATCH":
                payload, error = self._json_body()
                if error:
                    self._send_json(400, {"error": error})
                    return
                try:
                    project = store.update(project_id, payload)
                except PbrForgeError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, {"project": project.to_dict()})
                return
            if method == "DELETE":
                try:
                    store.delete(project_id)
                except PbrForgeError as exc:
                    self._send_json(404, {"error": str(exc)})
                    return
                self._send_json(200, {"deleted": project_id})
                return
            self._send_json(405, {"error": f"method {method} is not supported"})
            return
        action = parts[3]
        if action == "source" and method == "GET":
            try:
                _project, source = store.load(project_id)
            except PbrForgeError as exc:
                self._send_json(404, {"error": str(exc)})
                return
            self._send_file(source)
            return
        if action == "render" and method == "POST":
            try:
                job = store.submit(self.app.jobs, project_id)
            except PbrForgeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(202, {"job": job.to_dict()})
            return
        self._send_json(404, {"error": f"unknown project action {action!r}"})

    def _surface_spec_from(self, values: dict[str, Any]) -> Any:
        from ..surface import SurfaceSpec
        def number(key: str, default: Any) -> Any:
            raw = values.get(key, default)
            if isinstance(raw, list):
                raw = raw[0] if raw else default
            return raw
        return SurfaceSpec(
            pattern=str(number("pattern", "brick")),
            size=int(number("size", 512)),
            seed=int(number("seed", 1234)),
            weathering=str(number("weathering", "dust")),
            weather_amount=float(number("weather_amount", 0.35)),
            variant=int(number("variant", 0)),
            rows=int(number("rows", 6)), cols=int(number("cols", 12)),
            mortar=float(number("mortar", 0.055)),
            hue=str(number("hue", "warm")),
        )

    def _handle_surface(self, method: str, query: str) -> None:
        from ..core.errors import PbrForgeError
        from ..surface import generate
        if method not in ("GET", "POST"):
            self._send_json(405, {"error": "use GET for preview or POST to forge"})
            return
        values = {key: vals[0] for key, vals in parse_qs(query or "").items()}
        if method == "POST":
            payload, error = self._json_body(256 * 1024)
            if error:
                self._send_json(400, {"error": error})
                return
            values.update(payload)
        try:
            spec = self._surface_spec_from(values)
            result = generate(spec)
        except (PbrForgeError, ValueError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        if method == "GET":
            with tempfile.TemporaryDirectory(prefix="pbrforge-surface-") as tmp:
                path = Path(tmp) / "albedo.png"
                from ..core import png
                png.write(path, result.albedo)
                self._send_file(path)
            return
        with tempfile.TemporaryDirectory(prefix="pbrforge-surface-") as tmp:
            path = Path(tmp) / "surface.png"
            from ..core import png
            png.write(path, result.albedo)
            try:
                job = self.app.jobs.submit_bytes(
                    path.read_bytes(), name=str(values.get("name") or
                                                f"Surface {spec.pattern}")[:80],
                    preset=str(values.get("preset") or "generic-pbr"),
                    category=str(values.get("category") or "unknown"),
                    resolution=int(values.get("resolution") or 1024),
                    metal=str(values.get("metal") or "auto"),
                    source_name=f"surface-{spec.pattern}.png")
            except PbrForgeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
        self._send_json(202, {"job": job.to_dict(), "surface": result.to_dict()})

    def _handle_capture(self, query: str) -> None:
        from .. import capture as capture_mod
        from ..core.errors import PbrForgeError
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype.lower():
            self._send_json(415, {"error": "capture stack expects multipart/form-data"})
            return
        # Twelve frames is the product limit; the absolute cap keeps a forged
        # Content-Length from asking the request thread to allocate a gigabyte.
        limit = min(256 * 1024 * 1024,
                    int(self.app.config.max_upload_mb) * 1024 * 1024 * 12)
        body, error = self._read_body_limit(limit)
        if error:
            self._send_json(413 if "above" in error else 400, {"error": error})
            return
        raw = (f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8") + body
        try:
            message = BytesParser(policy=email_policy.default).parsebytes(raw)
        except Exception as exc:      # noqa: BLE001
            self._send_json(400, {"error": f"could not parse the capture upload: {exc}"})
            return
        if not message.is_multipart():
            self._send_json(400, {"error": "the capture upload has no multipart parts"})
            return
        fields: dict[str, str] = {}
        frames: list[bytes] = []
        for part in message.iter_parts():
            filename = part.get_filename()
            if filename:
                payload = part.get_payload(decode=True) or b""
                if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                    self._send_json(400, {"error": f"{filename} is not a PNG"})
                    return
                frames.append(payload)
            else:
                name = (part.get_param("name", header="content-disposition") or "").lower()
                payload = part.get_payload(decode=True) or b""
                if name:
                    fields[name] = payload.decode("utf-8", "replace")[:200]
        if len(frames) < 2:
            self._send_json(400, {"error": "a capture stack needs at least two frames"})
            return
        if len(frames) > 12:
            self._send_json(400, {"error": "a capture stack is limited to 12 frames"})
            return
        mode = (fields.get("mode") or "median").lower()
        normalize = (fields.get("normalize") or "1").lower() not in ("0", "false", "no")
        try:
            with tempfile.TemporaryDirectory(prefix="pbrforge-capture-") as tmp:
                source_paths = []
                for index, payload in enumerate(frames, start=1):
                    path = Path(tmp) / f"frame-{index}.png"
                    path.write_bytes(payload)
                    source_paths.append(path)
                merged = Path(tmp) / "merged.png"
                result = capture_mod.merge(source_paths, merged, mode=mode,
                                           normalize=normalize)
                job = self.app.jobs.submit_bytes(
                    merged.read_bytes(),
                    name=(fields.get("name") or "Capture stack")[:80],
                    preset=(fields.get("preset") or "generic-pbr"),
                    category=(fields.get("category") or "unknown"),
                    resolution=int(fields.get("resolution") or 1024),
                    metal=(fields.get("metal") or "auto"),
                    source_name="capture-merged.png")
        except (PbrForgeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(202, {"job": job.to_dict(), "capture": result.to_dict()})

    def _handle_vault(self) -> None:
        from .. import security
        from ..core.errors import PbrForgeError
        payload, error = self._json_body(64 * 1024)
        if error:
            self._send_json(400, {"error": error})
            return
        passphrase = str(payload.get("passphrase") or "")
        try:
            security.validate_passphrase(passphrase)
            backups = Path(self.app.config.config_dir) / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            target = backups / security.default_backup_name()
            info = security.create_backup(
                Path(self.app.config.config_dir), Path(self.app.config.library),
                target, passphrase)
        except PbrForgeError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_file(target, download=True)
        # Keep the encrypted file: it is the user's backup, not a cache entry.
        print(f"  backup   wrote {target} ({info['bytes']:,} bytes)", flush=True)

    def _handle_upload(self, query: str) -> None:
        from ..core.errors import PbrForgeError
        from ..export import presets as preset_mod

        if not self._same_origin():
            self._send_json(403, {"error": "cross-origin upload refused"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(411, {"error": "the upload has no Content-Length; "
                                           "send the PNG bytes as the request body"})
            return
        limit = int(self.app.config.max_upload_mb) * 1024 * 1024
        if length > limit:
            self._send_json(413, {
                "error": f"the upload is {length / 1e6:.1f} MB, above the "
                         f"{self.app.config.max_upload_mb} MB limit",
            })
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype not in ("image/png", "application/octet-stream"):
            self._send_json(415, {
                "error": f"content type {ctype or '(none)'} is not accepted; "
                         "the UI converts the image to PNG in the browser",
            })
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._send_json(400, {"error": "the upload ended early"})
            return

        name = self._query_one(query, "name", "material")[:80]
        source_name = self._query_one(query, "filename", "upload.png")[:160]
        preset = self._query_one(query, "preset", self.app.config.preset)
        try:
            preset_mod.load(preset)
        except PbrForgeError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        category = self._query_one(query, "category", "unknown").lower()
        if category not in ("brick", "marble", "unknown"):
            self._send_json(400, {"error": "category must be brick, marble or unknown"})
            return
        try:
            resolution = int(self._query_one(query, "resolution", "1024"))
        except ValueError:
            self._send_json(400, {"error": "resolution must be an integer"})
            return
        resolution = max(256, min(4096, resolution))
        metal = self._query_one(query, "metal", "auto").lower()
        if metal not in ("auto", "none", "full"):
            self._send_json(400, {"error": "metal must be auto, none or full"})
            return
        polarity = self._query_one(query, "height", "auto").lower()
        if polarity not in ("auto", "normal", "invert"):
            self._send_json(400, {"error": "height must be auto, normal or invert"})
            return

        try:
            job = self.app.jobs.submit_bytes(
                body, name=name, preset=preset, category=category,
                resolution=resolution, metal=metal,
                seamless=self._bool_param(query, "seamless", True),
                delight=self._bool_param(query, "delight", True),
                auto_levels=self._bool_param(query, "auto_levels", False),
                white_balance=self._bool_param(query, "white_balance", False),
                square=self._bool_param(query, "square", False),
                height_polarity=polarity, source_name=source_name)
        except PbrForgeError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(202, {"job": job.to_dict()})

    def _handle_job(self, path: str, method: str) -> None:
        parts = [part for part in path.strip("/").split("/") if part]
        # api / jobs / <id> / [action] / [kind]
        if len(parts) < 3:
            self._send_json(404, {"error": "no job id in the path"})
            return
        job_id = parts[2]
        job = self.app.jobs.get(job_id)
        record = job.to_dict() if job is not None else self.app.jobs.find_job_record(job_id)
        if record is None:
            self._send_json(404, {"error": f"no job {job_id}"})
            return
        if len(parts) == 3:
            self._send_json(200, record)
            return

        action = parts[3]
        if action == "download":
            zpath = (self.app.jobs.zip_file(job) if job is not None
                     else (Path(record.get("zip_path")) if record.get("zip_path") else None))
            if zpath is not None:
                try:
                    zpath = Path(zpath).resolve()
                    zpath = (zpath if zpath.is_relative_to(self.app.jobs.library.resolve())
                             else None)
                except OSError:
                    zpath = None
            if zpath is None or not zpath.is_file():
                self._send_json(404, {"error": "the result zip is not available yet"})
                return
            self._send_file(zpath, download=True, head_only=(method == "HEAD"))
            return

        if action == "map" and len(parts) >= 5:
            kind = parts[4]
            if job is not None:
                mpath = self.app.jobs.map_file(job, kind)
            else:
                folder = Path(record.get("folder") or "")
                try:
                    folder = folder.resolve()
                    folder = (folder if folder.is_relative_to(self.app.jobs.library.resolve())
                              else None)
                except OSError:
                    folder = None
                mpath = folder / "previews" / f"{kind}.png" if folder else None
                mpath = mpath if mpath and mpath.is_file() else None
            if mpath is None:
                self._send_json(404, {"error": f"no preview for {kind}"})
                return
            self._send_file(Path(mpath), head_only=(method == "HEAD"))
            return

        self._send_json(404, {"error": f"unknown job action {action!r}"})

    @staticmethod
    def _json_file(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _library_tags(items: list[dict[str, Any]]) -> list[str]:
        tags: set[str] = set()
        for item in items:
            tags.update(str(tag) for tag in item.get("tags") or [])
        return sorted(tags)

    def _handle_library(self, path: str, method: str = "GET") -> None:
        from .. import delivery as delivery_mod
        from .. import library as library_mod
        from .. import setdoctor
        from ..core.errors import PbrForgeError
        parts = [part for part in path.strip("/").split("/") if part]
        # api / library / <slug> / map / <kind>  or  ... / download
        if len(parts) < 3:
            self._send_json(404, {"error": "no library material in the path"})
            return
        slug = parts[2]
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", slug or ""):
            self._send_json(404, {"error": "invalid library name"})
            return
        folder = self.app.jobs.library / slug
        if not folder.is_dir():
            self._send_json(404, {"error": f"no library material {slug}"})
            return

        if len(parts) == 3:
            if method == "PATCH":
                payload, error = self._json_body(128 * 1024)
                if error:
                    self._send_json(400, {"error": error})
                    return
                material = self._json_file(folder / "material.json") or {}
                job = self._json_file(folder / "job.json") or {}
                resolutions = material.get("resolutions") or []
                edge = resolutions[-1] if resolutions else job.get("resolution")
                payload["resolution_px"] = edge
                try:
                    meta = library_mod.update(folder, payload)
                except PbrForgeError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, {"slug": slug, "meta": meta})
                return
            if method != "GET":
                self._send_json(405, {"error": f"method {method} is not supported"})
                return
            material = self._json_file(folder / "material.json")
            job = self._json_file(folder / "job.json")
            resolutions = (material or {}).get("resolutions") or []
            edge = resolutions[-1] if resolutions else None
            maps = []
            for preview in sorted((folder / "previews").glob("*.png")):
                maps.append({"kind": preview.stem, "width": edge, "height": edge,
                             "url": f"/api/library/{slug}/map/{preview.stem}"})
            self._send_json(200, {
                "slug": slug,
                "folder": str(folder),
                "material": material,
                "job": job,
                "maps": maps,
                "meta": library_mod.read(folder),
                "download": f"/api/library/{slug}/download",
                "delivery": f"/api/library/{slug}/delivery",
                "doctor": f"/api/library/{slug}/doctor",
            })
            return
        if len(parts) < 4:
            self._send_json(404, {"error": "no library action in the path"})
            return
        action = parts[3]
        if action in ("map", "download") and method not in ("GET", "HEAD"):
            self._send_json(405, {"error": f"use GET for library {action}"})
            return
        if action == "map" and len(parts) >= 5:
            mpath = self.app.jobs.library_map(slug, parts[4])
            if mpath is None:
                self._send_json(404, {"error": f"no preview for {parts[4]}"})
                return
            self._send_file(mpath)
            return
        if action == "download":
            zips = sorted(Path(folder).glob("PBRForge-*.zip"))
            if not zips:
                self._send_json(404, {"error": "no zip for this material"})
                return
            self._send_file(zips[0], download=True)
            return
        if action == "delivery":
            if method == "GET":
                payload = self._json_file(folder / "delivery" / "delivery.json")
                if payload is None:
                    self._send_json(404, {"error": "delivery has not been generated yet"})
                    return
                self._send_json(200, payload)
                return
            if method != "POST":
                self._send_json(405, {"error": "use POST to generate delivery"})
                return
            try:
                payload = delivery_mod.generate(folder)
            except PbrForgeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, payload)
            return
        if action == "doctor":
            if method == "GET":
                payload = self._json_file(folder / "doctor" / "set-report.json")
                if payload is not None:
                    self._send_json(200, payload)
                    return
                self._send_json(200, setdoctor.scan(folder).to_dict())
                return
            if method != "POST":
                self._send_json(405, {"error": "use POST to repair a set"})
                return
            payload, error = self._json_body(128 * 1024)
            if error:
                self._send_json(400, {"error": error})
                return
            try:
                out = folder / f"doctor-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
                result = setdoctor.repair(
                    folder, out,
                    normal_to=str(payload.get("normal") or ""),
                    invert=str(payload.get("invert") or ""),
                    repack=str(payload.get("repack") or ""),
                    rename=bool(payload.get("rename")),
                    overwrite=bool(payload.get("overwrite")))
            except PbrForgeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, result)
            return
        self._send_json(404, {"error": f"unknown library action {action!r}"})

    def _handle_update_install(self) -> None:
        if not self._same_origin():
            self._send_json(403, {"error": "cross-origin update refused"})
            return
        from .. import update as update_mod
        info = self.app.update_info
        if info is None or not info.available:
            payload = self.app.update_payload(force=True)
            info = self.app.update_info
            if info is None or not info.available:
                self._send_json(409, {
                    "error": payload.get("error") or "there is no newer release",
                })
                return
        if not info.asset_url:
            self._send_json(409, {
                "error": info.error or "the release has no Windows installer asset",
            })
            return

        def worker() -> None:
            time.sleep(0.8)      # let the HTTP response reach the browser first
            try:
                target = update_mod.install(info)
                print(f"  update   installer started: {target}", flush=True)
            except Exception as exc:      # noqa: BLE001 - report on the next poll
                info.error = f"install failed: {exc}"
                print(f"  update   install failed: {exc}", flush=True)

        threading.Thread(target=worker, daemon=True,
                         name="pbrforge-update-install").start()
        self._send_json(202, {"started": True, "version": info.latest,
                              "asset": info.asset_name})

    def _handle_settings(self) -> None:
        if not self._same_origin():
            self._send_json(403, {"error": "cross-origin settings change refused"})
            return
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeError):
            self._send_json(400, {"error": "settings must be a JSON object"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "settings must be a JSON object"})
            return
        changed: dict[str, Any] = {}
        if "update_check" in payload:
            self.app.config.update_check = bool(payload["update_check"])
            changed["update_check"] = self.app.config.update_check
        if "update_auto_install" in payload:
            self.app.config.update_auto_install = bool(payload["update_auto_install"])
            changed["update_auto_install"] = self.app.config.update_auto_install
        if not changed:
            self._send_json(400, {"error": "no known setting was supplied"})
            return
        try:
            path = self.app.config.save()
        except OSError as exc:
            self._send_json(500, {"error": f"could not write config: {exc}"})
            return
        self._send_json(200, {
            "saved": changed,
            "config": str(path),
            "update_check": self.app.config.update_check,
            "update_auto_install": self.app.config.update_auto_install,
        })

    def _send_file(self, path: Path, *, download: bool = False,
                   head_only: bool = False) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": f"file not found: {path.name}"})
            return
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        extra: list[tuple[str, str]] = []
        if download:
            extra.append(("Content-Disposition",
                          f'attachment; filename="{quote(path.name)}"'))
        self._send(200, data, ctype, extra=extra,
                   cache="private, max-age=3600", head_only=head_only)

    def _handle_pair(self, method: str, query: str) -> None:
        cfg = self.app.config
        if not cfg.auth:
            self._send(302, b"", "text/plain", extra=[("Location", "/")])
            return

        client = self._client()
        attempt = self.app.auth.attempt(client)
        if attempt.locked:
            wait = int(attempt.locked_until - time.time()) + 1
            self._error(429, "Too many attempts",
                        f"This device is locked out for another {wait}s.",
                        "Wait, then try again with the code from the server console.")
            return

        if method == "POST":
            if not self._same_origin():
                self._send_json(403, {"error": "cross-origin pairing request refused"})
                return
            body = self._read_body().decode("utf-8", "replace")
            code = (parse_qs(body).get("code") or [""])[0]
            if self.app.auth.check_pairing_code(code):
                self.app.auth.note_success(client)
                cookie = self.app.auth.issue_session()
                next_path = (parse_qs(query).get("next") or ["/"])[0]
                # Only a local absolute path is a valid post-pair destination.
                # `//evil.example` would otherwise be an open redirect.
                if (not next_path.startswith("/") or next_path.startswith("//")
                        or "\\" in next_path or "\r" in next_path or "\n" in next_path):
                    next_path = "/"
                self._send(302, b"", "text/plain", extra=[
                    ("Location", next_path),
                    ("Set-Cookie", auth_mod.session_cookie(cookie, secure=bool(cfg.tls))),
                ])
                print(f"  paired   {client}", flush=True)
                return

            self.app.auth.note_failure(client)
            page = PAIR_PAGE.replace("__HOST__", local_hostname() or "this PC")
            page = page.replace("__ERROR__",
                                "<div class='err'>That code did not match. "
                                "Check the console on the server.</div>")
            self._send(403, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        page = PAIR_PAGE.replace("__HOST__", local_hostname() or "this PC").replace("__ERROR__", "")
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_index(self, head_only: bool) -> None:
        index = index_file()
        if index is None:
            self._error(
                500, "UI not found",
                "The web UI could not be located. Looked in:<br><br>"
                + "<br>".join(f"<code>{p}</code>" for p in searched_paths("ui")),
                "If this is a packaged build, the <code>ui</code> folder is missing from it. "
                "Reinstall, or run from a source checkout.")
            return
        data = index.read_bytes()
        stat = index.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self._send(304, b"", "text/html; charset=utf-8", etag=etag, head_only=True)
            return
        # no-cache on the app shell: an upgraded server must not be masked by a
        # stale cached page, and this file is 60 KB.
        self._send(200, data, "text/html; charset=utf-8", etag=etag, cache="no-cache",
                   head_only=head_only)

    def _serve_static(self, relative: str, head_only: bool) -> None:
        base = ui_dir()
        if base is None:
            self._error(500, "UI not found", "The <code>ui</code> directory is missing.",
                        "Reinstall, or run from a source checkout.")
            return
        try:
            target = (base / relative).resolve()
        except OSError:
            self._error(400, "Bad path", "The path could not be resolved.")
            return

        # Traversal guard. resolve() collapses `..`, then we require containment.
        # `Path.is_relative_to` handles the sibling-directory prefix trap
        # (/srv/ui vs /srv/ui-secret) which a naive startswith() does not.
        base_res = base.resolve()
        if not target.is_relative_to(base_res):
            self._error(403, "Forbidden",
                        f"That path is outside the UI directory.",
                        "Serve only files from <code>ui/</code>.")
            return
        if not target.is_file():
            self._error(404, "Not found", f"<code>{relative}</code> does not exist.")
            return

        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        stat = target.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self._send(304, b"", ctype, etag=etag, head_only=True)
            return
        self._send(200, target.read_bytes(), ctype, etag=etag,
                   cache="public, max-age=300", head_only=head_only)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, addr: tuple[str, int], handler: type[Handler], app: App) -> None:
        self.app = app
        super().__init__(addr, handler)

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: D102
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return          # browsers closing sockets is normal, not an error
        super().handle_error(request, client_address)


class RedirectHandler(BaseHTTPRequestHandler):
    """Plain HTTP -> HTTPS. Clients should never use an insecure origin: secure-context
    and the clipboard API are gated on a secure context, and `http://192.168.x.x`
    is not one."""

    protocol_version = "HTTP/1.1"
    server_version = "PBRForge"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:            # noqa: N802
        self._redirect()

    def do_HEAD(self) -> None:           # noqa: N802
        self._redirect()

    def do_POST(self) -> None:           # noqa: N802
        self._redirect()

    def _redirect(self) -> None:
        https_port = self.app.config.https_port
        hostname = (self.headers.get("Host") or local_hostname()).split(":")[0]
        self.send_response(301)
        self.send_header("Location", f"https://{hostname}:{https_port}/"
                         + self.path.lstrip("/"))
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    @property
    def app(self) -> App:
        return self.server.app           # type: ignore[attr-defined]


@dataclass
class Running:
    main: Server
    redirect: Server | None
    beacon: Beacon | None
    thread: threading.Thread
    bound_host: str
    bound_port: int
    pid_recorded: bool = False

    def stop(self) -> None:
        if self.beacon:
            self.beacon.stop()
        if self.redirect:
            self.redirect.shutdown()
        self.main.shutdown()
        # Only clear the record if it is ours. A second server started on another
        # port in the same config directory overwrites the file, and this one
        # exiting must not delete the record of the one still serving.
        if self.pid_recorded:
            try:
                lifecycle.clear(self.main.app.config, pid=os.getpid())
            except Exception:                    # noqa: BLE001 - never fail on exit
                pass


def bind(host: str, port: int, *, span: int = 8) -> tuple[str, int]:
    """Bind to `port`, walking forward a few ports if it is taken.

    Walking forward is the right behaviour for a personal LAN tool: if 7788 is
    already in use, serving on 7789 is obviously better than refusing to start.
    The caller reports the real port, and discovery announces it.
    """
    last: OSError | None = None
    for candidate in range(port, port + max(1, span)):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
                return host, candidate
            finally:
                probe.close()
        except OSError as exc:
            last = exc
            if exc.errno in (48, 98, 10048):     # EADDRINUSE across platforms
                continue
            raise
    holder = who_holds_port(port)
    raise RuntimeError(
        f"port {port} is already in use"
        + (f" by {holder}" if holder else "")
        + (f", and so are the next {span - 1}" if span > 1 else "")
        + f"\n  fix      stop the other process, or start with --port {port + span}"
    ) from last


def _ssl_context(cfg: Config) -> ssl.SSLContext:
    if not cfg.cert_file.exists() or not cfg.key_file.exists():
        raise RuntimeError(
            f"TLS is enabled but the certificate is missing\n"
            f"  expected {cfg.cert_file}\n"
            f"  fix      run 'pbrforge tls init', or start with --no-tls"
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cfg.cert_file), keyfile=str(cfg.key_file))
    return ctx


def serve(app: App, *, host: str | None = None, port: int | None = None,
          span: int = 8, banner: bool = True, record_pid: bool = True) -> Running:
    """Start serving. Returns a `Running` handle; the caller decides about blocking.

    `record_pid=False` is for tests and for any second server in the same config
    directory: the PID file describes the one server a user can stop, and having a
    short-lived test server overwrite it would make `pbrforge stop` target the
    wrong thing.
    """
    cfg = app.config
    bind_host = host if host is not None else cfg.bind
    want_port = port if port is not None else cfg.serve_port

    resolved_host, resolved_port = bind(bind_host, want_port, span=span)
    httpd = Server((resolved_host, resolved_port), Handler, app)

    redirect: Server | None = None
    if cfg.tls:
        # Resolve the context BEFORE wrapping, so a missing certificate closes the
        # listening socket instead of leaving an orphaned port bound.
        try:
            context = _ssl_context(cfg)
        except Exception:
            httpd.server_close()
            raise
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        if cfg.port != resolved_port:
            try:
                r_host, r_port = bind(resolved_host, cfg.port, span=1)
                redirect = Server((r_host, r_port), RedirectHandler, app)
                threading.Thread(target=redirect.serve_forever,
                                 name="pbrforge-redirect", daemon=True).start()
            except RuntimeError:
                redirect = None

    beacon: Beacon | None = None
    if cfg.discovery:
        beacon = Beacon(app.ping_payload)
        beacon.start()

    thread = threading.Thread(target=httpd.serve_forever, name="pbrforge-http", daemon=True)
    thread.start()

    # Written only after the socket is bound, and only after the TLS wrap
    # succeeded, so the file never describes a server that is not actually up.
    recorded = False
    if record_pid:
        try:
            lifecycle.write(cfg, port=resolved_port, host=resolved_host,
                            scheme=cfg.scheme)
            recorded = True
        except OSError:
            pass          # a read-only config dir must not stop the server

    if banner:
        _print_banner(app, resolved_host, resolved_port)

    return Running(main=httpd, redirect=redirect, beacon=beacon, thread=thread,
                   bound_host=resolved_host, bound_port=resolved_port,
                   pid_recorded=recorded)


def _print_banner(app: App, host: str, port: int) -> None:
    cfg = app.config
    scheme = cfg.scheme
    print()
    print(f"  {APP_NAME} {__version__}  \u00b7  listening on {host}:{port}")
    print()
    for label, url in urls(port, scheme):
        print(f"    {label:<22} {url}")
    if cfg.auth:
        print()
        print(f"    Pairing code           {app.auth.pairing_code()}")
    else:
        print()
        print("    Pairing code           disabled (--no-auth). Anyone on this "
              "network can read and write the library.")
    print()
    print(f"    Library                {cfg.library}")
    print(f"    Cache                  {cfg.cache}")
    print(f"    Config                 {cfg.config_file}")
    if cfg.tls:
        print(f"    TLS                    on  (http://\u2026 redirects to https://\u2026)")
    else:
        print("    TLS                    off (LAN clients keep WebGL but lose secure-context APIs;")
        print("                           run 'pbrforge tls init' to enable it)")
    if not cfg.library.exists():
        print(f"    note: the library folder does not exist yet; it will be created on first use")
    print()
    print("    Ctrl+C to stop")
    # Ctrl+C only works in this console. A packaged install is normally started by
    # double-clicking a shortcut, so the console that owns the process is often not
    # the one the user is looking at -- and Task Manager shows the PyInstaller
    # bootloader, not the process actually holding the port. Naming the command
    # here is the only place a user will ever find it.
    print("    Or from any other window:  pbrforge stop")
    print()


def run_blocking(app: App, **kwargs: Any) -> int:
    running = serve(app, **kwargs)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  stopping\u2026")
    finally:
        running.stop()
    return 0


def ensure_port_available(cfg: Config) -> None:
    """Called before serving so a port clash is a clear message, not a traceback."""
    if port_in_use("127.0.0.1", cfg.serve_port):
        holder = who_holds_port(cfg.serve_port)
        raise RuntimeError(
            f"port {cfg.serve_port} is already in use"
            + (f" by {holder}" if holder else "")
            + "\n  fix      stop that process, or run 'pbrforge serve --port 7790'"
        )
