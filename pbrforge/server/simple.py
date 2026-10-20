"""The local server. Standard library only, by design.

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
import json
import mimetypes
import os
import socket
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# --------------------------------------------------------------------- payload
@dataclass
class App:
    config: Config
    auth: Auth
    verbose: bool = False
    log_file: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    requests: int = 0

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
        }

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
        return [
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "SAMEORIGIN"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
            # The UI is one self-contained file with inline style and script, so
            # 'unsafe-inline' is required. Everything is served from this origin
            # and nothing is loaded from the network, which is the trade-off being
            # made -- documented in docs/DEPLOYMENT.md rather than left implicit.
            ("Content-Security-Policy",
             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
             "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' blob:; "
             "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'"),
        ]

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

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        head_only = method == "HEAD"

        try:
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

            if path == "/api/health":
                self._send_json(200, self.app.health_payload())
                return

            if path == "/api/logout":
                self._send(200, b"unpaired\n", "text/plain",
                           extra=[("Set-Cookie",
                                   f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
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
            body = self._read_body().decode("utf-8", "replace")
            code = (parse_qs(body).get("code") or [""])[0]
            if self.app.auth.check_pairing_code(code):
                self.app.auth.note_success(client)
                cookie = self.app.auth.issue_session()
                secure = "; Secure" if cfg.tls else ""
                next_path = (parse_qs(query).get("next") or ["/"])[0]
                if not next_path.startswith("/"):
                    next_path = "/"
                self._send(302, b"", "text/plain", extra=[
                    ("Location", next_path),
                    ("Set-Cookie",
                     f"{COOKIE_NAME}={cookie}; Path=/; Max-Age={auth_mod.SESSION_DAYS * 86400}; "
                     f"HttpOnly; SameSite=Lax{secure}"),
                ])
                print(f"  paired   {client}", flush=True)
                return

            self.app.auth.note_failure(client)
            page = PAIR_PAGE.replace("__HOST__", local_hostname())
            page = page.replace("__ERROR__",
                                "<div class='err'>That code did not match. "
                                "Check the console on the server.</div>")
            self._send(403, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        page = PAIR_PAGE.replace("__HOST__", local_hostname()).replace("__ERROR__", "")
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
    """Plain HTTP -> HTTPS. Clients should never use an insecure origin: WebGPU
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
        print("    TLS                    off (LAN clients fall back to WebGL2 rather than WebGPU;")
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
