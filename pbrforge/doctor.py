"""Environment probe. Answers one question: can this machine run the thing?

Written to be useful on a *client* machine as well as the server, because the
first thing to check when a second PC cannot connect is what that PC thinks it has.

Every line is one of OK / optional / FAIL, and a FAIL always comes with a fix.
Nothing here raises: a doctor that crashes is not a doctor.
"""

from __future__ import annotations

import platform
import shutil
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, __version__
from .config import Config
from .core.cache import human_bytes
from .netutil import interfaces, port_in_use, primary_ip, who_holds_port

OK = "OK"
OPTIONAL = "optional"
FAIL = "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    important: bool = False       # print the advice even when the status is not FAIL

    @property
    def symbol(self) -> str:
        return {OK: "\u2713", OPTIONAL: "\u00b7", FAIL: "\u2717"}[self.status]


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "",
            important: bool = False) -> None:
        self.checks.append(Check(name, status, detail, fix, important))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def ready(self) -> bool:
        return not self.failures


def _module(name: str, import_name: str | None = None) -> tuple[bool, str]:
    try:
        mod = __import__(import_name or name)
        version = getattr(mod, "__version__", "")
        return True, str(version) if version else "present"
    except Exception:      # noqa: BLE001 - a broken optional import is not fatal
        return False, ""


def run(cfg: Config, *, full: bool = False) -> Report:
    r = Report()

    # ---------------------------------------------------------------- runtime
    v = sys.version_info
    if v >= (3, 11):
        r.add("Python", OK, f"{v.major}.{v.minor}.{v.micro}  ({sys.executable})")
    elif v >= (3, 9):
        r.add("Python", OK, f"{v.major}.{v.minor}.{v.micro}  (tomllib absent; a fallback parser is used)")
    else:
        r.add("Python", FAIL, f"{v.major}.{v.minor}.{v.micro} is too old",
              "install Python 3.11 or newer from python.org")

    r.add("Platform", OK, f"{platform.system()} {platform.release()}  {platform.machine()}")

    # The Microsoft Store build is an MSIX package, and MSIX gives it a private,
    # copy-on-write view of AppData\Roaming: writes are redirected into the package
    # store, reads fall through to the real location. So a source run can read a
    # file the installed build wrote and then write its own shadow of it, after
    # which the two silently disagree. The certificate and secret live there, which
    # makes the symptom "a valid certificate is reported as self-signed".
    exe = sys.executable.lower()
    if "windowsapps" in exe or "windows store" in exe:
        r.add("Python build", OPTIONAL,
              "Microsoft Store build detected",
              "this build keeps a private copy of %APPDATA%\\pbrforge: it writes there "
              "and reads from there first, falling through to the real folder only when "
              "it has no copy.\n"
              "So config.toml edits can be half-seen, and two tls\\ca.pem files can "
              "coexist, one of which will not match the server.\n"
              "Prefer the python.org installer. Or set PBRFORGE_CONFIG_DIR to a path "
              "outside AppData, which both builds will then share.",
              important=True)
    else:
        r.add("Python build", OK, "not a sandboxed store build")

    if sys.platform == "win32":
        try:
            import ctypes
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())     # type: ignore[attr-defined]
        except Exception:      # noqa: BLE001
            is_admin = False
        r.add("Elevation", OK if is_admin else OPTIONAL,
              "running as Administrator" if is_admin else "running as a normal user",
              "" if is_admin else "needed only to add the firewall rule at install time")

    # ------------------------------------------------------------ core stack
    r.add("stdlib server", OK, "http.server / ssl / zlib \u2014 always available")

    # Present modules get their own line.  Only `cryptography` is a real
    # dependency in 2.0: it backs the vault, release signatures and TLS
    # certificates.  Nothing else in this list is used by the shipped pipeline,
    # so doctor must not imply that installing numpy or torch unlocks a feature.
    optional = [
        ("cryptography", "cryptography", "vault, release signatures and LAN TLS",
         "pip install \"cryptography>=42\""),
    ]
    missing: list[str] = []
    for label, import_name, purpose, command in optional:
        found, version = _module(label, import_name)
        if found:
            r.add(label, OK, version)
        else:
            missing.append(f"{label} ({purpose}: {command})")

    if missing:
        r.add(f"not installed ({len(missing)})", OPTIONAL,
              ", ".join(m.split(" (")[0] for m in missing),
              "\n         ".join(missing),
              important=True)
        # The first line of each item is the label; the rest are indented under it
        # by render(), so the advice reads as a list rather than a paragraph.

    r.add("GPU", OK, "WebGL preview runs in the browser; no Python GPU stack is used",
          "")

    # TLS prefers the in-process cryptography backend, but openssl is a real
    # fallback for source runs and minimal builds. Look where Git for Windows
    # actually puts it, not only on PATH.
    from .tls import openssl_path
    ossl = openssl_path()
    on_path = shutil.which("openssl")
    if ossl:
        r.add("openssl", OK, ossl + ("" if ossl == on_path else "   (not on PATH)"),
              "" if ossl == on_path
              else "found anyway; add its folder to PATH if you want to call it yourself")
    else:
        r.add("openssl", OPTIONAL,
              "not found, on PATH or in the usual install locations",
              "optional; TLS certificate generation then needs 'cryptography'",
              important=True)

    # ----------------------------------------------------------------- paths
    try:
        cfg.config_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.config_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        r.add("Config dir", OK, str(cfg.config_dir))
    except OSError as exc:
        r.add("Config dir", FAIL, f"{cfg.config_dir}: {exc.strerror or exc}",
              "set PBRFORGE_CONFIG_DIR to a writable location")

    for label, path in (("Library", cfg.library), ("Cache", cfg.cache)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free
            r.add(label, OK, f"{path}   {human_bytes(free)} free")
        except OSError as exc:
            r.add(label, FAIL, f"{path}: {exc.strerror or exc}",
                  "check the path exists and is writable; network shares need to be "
                  "mounted for the account running the server")

    # A config file that exists but did not parse is a FAIL, not a note. The app is
    # running on defaults the user did not choose, and that surfaces much later as
    # "why is my library in the wrong place" rather than as a config error.
    if cfg.load_error:
        r.add("Config file", FAIL, f"{cfg.config_file} exists but could not be parsed",
              cfg.load_error.replace("\n", "\n")
              + "\nfix the typo, or delete the file to use the defaults deliberately")
    else:
        # Say where the values came from, but not as a tautology: when the file was
        # read, cfg.source *is* the file path, and printing it twice read as
        # "C:\...\config.toml   (from C:\...\config.toml)".
        shown = str(cfg.config_file)
        if cfg.source != shown:
            shown += f"   (from {cfg.source})"
        r.add("Config file", OK if cfg.config_file.exists() else OPTIONAL, shown,
              "" if cfg.config_file.exists() else "run 'pbrforge config --write' to create one")

    # -------------------------------------------------------------- network
    ifaces = interfaces()
    if ifaces:
        for iface in ifaces[:4]:
            r.add("LAN address", OK, f"{iface.ip}   ({iface.name})")
    else:
        r.add("LAN address", FAIL, "no non-loopback IPv4 address found",
              "check the network adapter; other PCs cannot reach this machine without one")
    r.add("Primary IP", OK, primary_ip())

    # ----------------------------------------------------------------- server
    # Asked before the port check, because "port 7788 is already in use" is not a
    # problem when the thing using it is the server the user just started -- and
    # reporting it as an amber warning is how someone ends up killing a healthy
    # server to clear a warning that was never about a fault.
    from . import lifecycle

    running = lifecycle.is_running(cfg)
    if running is not None:
        where = f", port {running.port}" if running.port else ""
        r.add("Server", OK, f"running: pid {running.pid}{where}",
              "stop it with 'pbrforge stop'", important=True)
    else:
        _, detail = lifecycle.status(cfg)
        if detail.startswith("unknown:"):
            r.add("Server", OPTIONAL, detail,
                  f"delete {lifecycle.pid_file(cfg)} and start again", important=True)
        else:
            r.add("Server", OK, "not running", "", important=True)

    holder = who_holds_port(cfg.serve_port)
    if holder and running is not None and f"pid {running.pid}" in holder:
        r.add(f"Port {cfg.serve_port}", OK, f"serving (pid {running.pid})")
    elif holder:
        r.add(f"Port {cfg.serve_port}", OPTIONAL, f"already in use by {holder}",
              f"start with 'pbrforge serve --port {cfg.serve_port + 1}'", important=True)
    else:
        r.add(f"Port {cfg.serve_port}", OK, "free")

    # ------------------------------------------------------------------- tls
    if cfg.tls:
        from .tls import status as tls_status
        st = tls_status(cfg)
        if not st.present:
            r.add("TLS", FAIL, "enabled in config but no certificate exists",
                  "run 'pbrforge tls init', or start with 'pbrforge serve --no-tls'")
        elif st.missing_current_ips:
            r.add("TLS", OPTIONAL,
                  f"certificate does not cover {', '.join(st.missing_current_ips)}",
                  "run 'pbrforge tls init --force' after a network change", important=True)
        elif st.days_left < 1:
            r.add("TLS", FAIL, "certificate has expired",
                  "run 'pbrforge tls init --force'")
        else:
            r.add("TLS", OK, f"valid for {st.days_left} more days")
    else:
        r.add("TLS", OPTIONAL, "off — LAN clients lose secure-context APIs",
              "run 'pbrforge tls init' to enable HTTPS for those APIs on clients")

    if cfg.auth:
        r.add("Auth", OK, "pairing code required")
    else:
        r.add("Auth", OPTIONAL, "DISABLED \u2014 anyone on this network can read and write the library",
              "remove --no-auth unless you are certain the network is trusted", important=True)

    if cfg.bind in ("127.0.0.1", "localhost", "::1"):
        r.add("Bind address", OPTIONAL, f"{cfg.bind} \u2014 not reachable from other PCs",
              "use --host 0.0.0.0 for LAN access", important=True)
    else:
        r.add("Bind address", OK, f"{cfg.bind} (reachable on the LAN)")

    # ------------------------------------------------------------- firewall
    if sys.platform == "win32":
        # With TLS on there are two listening ports, not one: the plain port exists
        # only to redirect to the https one, and a bookmark saved before TLS was
        # turned on still points at it. Testing serve_port alone would report a
        # half-open setup as healthy and then let that bookmark time out with no
        # explanation -- the exact symptom this check exists to pre-empt.
        ports = [cfg.serve_port]
        if cfg.tls and cfg.port != cfg.serve_port:
            ports.append(cfg.port)
        states = {p: _firewall_rule_exists(f"PBRForge TCP {p}") for p in ports}
        listing = ", ".join(str(p) for p in ports)

        if all(v is True for v in states.values()):
            r.add("Firewall", OK, f"inbound rule present for TCP {listing}")
        elif any(v is False for v in states.values()):
            missing = [p for p in ports if states[p] is False]
            rules = "\n".join(
                f'         netsh advfirewall firewall add rule name="PBRForge TCP {p}" '
                f'dir=in action=allow protocol=TCP localport={p} profile=private'
                for p in missing)
            r.add("Firewall", OPTIONAL,
                  "no inbound rule found for TCP " + ", ".join(str(p) for p in missing),
                  f"run deploy\\allow-firewall.cmd as Administrator, or\n{rules}",
                  important=True)
        else:
            r.add("Firewall", OPTIONAL, "could not be queried",
                  "add the rule manually if clients cannot connect")

    return r


def _firewall_rule_exists(name: str) -> bool | None:
    import subprocess
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:      # noqa: BLE001
        return None
    combined = (out.stdout + out.stderr).lower()
    if "no rules match" in combined:
        return False
    if "localport" in combined:
        return True
    return None


def render(report: Report) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {APP_NAME} {__version__}  \u00b7  doctor")
    lines.append("")
    width = max((len(c.name) for c in report.checks), default=10)
    for c in report.checks:
        detail = f"  {c.detail}" if c.detail else ""
        lines.append(f"  {c.symbol} {c.name:<{width}}{detail}")
        # A FAIL always explains itself. An OPTIONAL item explains itself only when
        # the advice is worth acting on -- otherwise seven missing packages turn a
        # clean report into a wall of text that nobody reads a second time.
        if c.fix and (c.status == FAIL or c.important):
            label = "fix" if c.status == FAIL else "note"
            first, *rest = c.fix.split("\n")
            lines.append(f"  {'':<{width}}  {label}     {first}")
            for extra in rest:
                lines.append(f"  {'':<{width}}           {extra.strip()}")
    lines.append("")
    ok = sum(1 for c in report.checks if c.status == OK)
    opt = sum(1 for c in report.checks if c.status == OPTIONAL)
    bad = len(report.failures)
    verdict = "ready to run" if report.ready else "not ready \u2014 see the FAIL lines above"
    lines.append(f"  {ok} ok \u00b7 {opt} optional \u00b7 {bad} failed  \u2192  {verdict}")
    lines.append("")
    return "\n".join(lines)
