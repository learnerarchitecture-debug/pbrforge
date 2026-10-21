"""The command line.

Design rules, in priority order:

  1. `pbrforge` with no arguments does the most useful thing: start the server and
     print the URL to open. A tool whose entry point is a help screen is a tool
     that gets used once.
  2. Every error names the stage, the reason and the fix. No bare tracebacks for
     things the user can act on; `-v` still gives the traceback when it is a bug.
  3. Commands are verbs that match what the user is thinking: serve, url, code,
     doctor, selftest. Not `run-server-mode-a`.

argparse rather than click/typer, deliberately: it is stdlib, it handles
Windows' argument quoting quirks without surprises, and it adds nothing to the
single-file build's size.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .config import Config
from .core.cache import Cache, human_bytes


# --------------------------------------------------------------------- helpers
def _cfg(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    for attr in ("tls", "auth", "discovery", "open_browser"):
        value = getattr(args, attr, None)
        if value is not None:
            overrides[attr] = value
    if getattr(args, "port", None):
        overrides["port"] = args.port
        overrides["https_port"] = args.port
    if getattr(args, "library", None):
        overrides["library"] = args.library
    if getattr(args, "cache", None):
        overrides["cache"] = args.cache
    if getattr(args, "host", None):
        overrides["bind"] = args.host
    config_dir = Path(args.config_dir).expanduser() if getattr(args, "config_dir", None) else None
    return Config.load(config_dir=config_dir, overrides=overrides)


def _fail(message: str, code: int = 2) -> int:
    print(f"\n  error    {message}\n", file=sys.stderr)
    return code


def _pause_if_double_clicked() -> None:
    """Keep the console open when the exe is double-clicked.

    A packaged tool that flashes a window and vanishes is indistinguishable from
    one that crashed, so the console is held open -- but only when there is
    actually a human there to press the key.

    Getting this wrong is worse than not doing it at all, and this function was
    wrong. The first version decided purely on the console-process count, and
    `& pbrforge.exe version` from a build script satisfied that test, so an
    automated build of the packaged exe stopped dead on an input() prompt. A
    pause that cannot be distinguished from a hang is a far worse failure than a
    window that closes too quickly.

    Four independent conditions now, all of which must hold:
      1. stdout is a terminal   -- piping to a variable or a file is not a human
      2. stdin is a terminal    -- there is nothing to press Enter on otherwise
      3. PBRFORGE_NO_PAUSE unset
      4. this process is alone on its console -- i.e. launched from Explorer

    Condition 3 is the important one. A heuristic that can block an unattended
    script needs an override that cannot itself be misdetected, and the build
    scripts set it.
    """
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("PBRFORGE_NO_PAUSE"):
        return
    try:
        if not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
            return
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Owned by csrss rather than a console window => launched from Explorer.
        count = int(kernel32.GetConsoleProcessList(0, 0) or 0)
        if count <= 1:
            input("\n  Press Enter to close\u2026")
    except Exception:      # noqa: BLE001
        return


# -------------------------------------------------------------------- commands
def cmd_serve(args: argparse.Namespace) -> int:
    from .server.auth import Auth
    from .server.simple import App, run_blocking

    cfg = _cfg(args)
    cfg.ensure_dirs()

    if cfg.tls and not cfg.cert_file.exists():
        from .tls import backend, install_hint

        print(f"\n  TLS is enabled but no certificate exists at\n    {cfg.cert_file}\n")
        print("  Run this first:\n\n    pbrforge tls init\n")
        if not backend()[0]:
            print(f"  That needs:  {install_hint()}\n")
        print("  Or serve over plain HTTP with:  pbrforge serve --no-tls\n")
        return 2

    if not cfg.auth:
        print("\n  warning  auth is disabled -- anyone on this network can read and")
        print("           write the library. Use --no-auth only on a network you control.\n")

    app = App(config=cfg, auth=Auth(cfg.secret_file, enabled=cfg.auth),
              verbose=bool(args.verbose))

    if args.open_browser or cfg.open_browser:
        from .netutil import urls
        target = urls(cfg.serve_port, cfg.scheme, include_loopback=True)[0][1]
        import threading
        threading.Timer(0.8, lambda: webbrowser.open(target)).start()

    try:
        return run_blocking(app, host=args.host, port=args.port, span=args.span)
    except RuntimeError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\n  stopped\n")
        return 0


def cmd_url(args: argparse.Namespace) -> int:
    from . import lifecycle
    from .netutil import urls
    from .server.auth import Auth

    cfg = _cfg(args)
    auth = Auth(cfg.secret_file, enabled=cfg.auth)
    live = lifecycle.is_running(cfg)

    # When a server is up, its addresses win over the configured ones. `serve
    # --port 7804` overrides the config file, so answering from config alone prints
    # 7788 for a server listening on 7804 -- and printing two different ports in
    # one screenful is how a user concludes the tool does not know what it is
    # doing. The live port is the only one that can actually be opened.
    port = (live.port if live and live.port else cfg.serve_port)
    scheme = (live.scheme if live and live.scheme else cfg.scheme)

    if args.json:
        payload = {
            "app": "pbrforge",
            "version": __version__,
            "scheme": scheme,
            "port": port,
            "configured_port": cfg.serve_port,
            "running": bool(live),
            "pid": live.pid if live else None,
            "auth": cfg.auth,
            "pairing_code": auth.pairing_code() if cfg.auth else None,
            "urls": [{"label": label, "url": url} for label, url in urls(port, scheme)],
            "library": str(cfg.library),
        }
        print(json.dumps(payload, indent=2))
        return 0

    print()
    for label, url in urls(port, scheme):
        print(f"  {label:<22} {url}")
    if cfg.auth:
        print(f"  {'Pairing code':<22} {auth.pairing_code()}")
    print(f"  {'Library':<22} {cfg.library}")
    print()

    # Say whether anything is actually listening. The addresses above describe what
    # *would* work, which is how a user ends up staring at a browser that cannot
    # connect while the tool insists the URL is right.
    running, detail = lifecycle.status(cfg)
    if running:
        print(f"  Live now:  {detail}")
        if live and live.port and live.port != cfg.serve_port:
            # Name the cause only when it can actually be distinguished. The earlier
            # wording blamed "--port" for every mismatch, and the very common case is
            # a server started with --tls while this config has no tls line -- which
            # sent the reader looking for an argument that was never used.
            if live.port == cfg.https_port:
                why = "it was started with --tls, or with a config that sets tls = true"
            elif live.port == cfg.port:
                why = "it is serving plain HTTP while this config expects HTTPS"
            else:
                why = "it was started with a flag or config this command cannot see"
            print(f"             (it is on {live.port}; this config expects "
                  f"{cfg.serve_port} -- {why})")
    else:
        print(f"  Not running:  {detail}")
        print(f"                run 'pbrforge serve' first")
    print()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from . import lifecycle

    cfg = _cfg(args)
    stopped, detail = lifecycle.stop(cfg)

    if args.json:
        print(json.dumps({"stopped": stopped, "detail": detail}, indent=2))
        return 0

    print()
    print(f"  {detail}")
    print()
    if not stopped and args.strict:
        return 1
    return 0


def cmd_code(args: argparse.Namespace) -> int:
    from .server.auth import Auth
    cfg = _cfg(args)
    auth = Auth(cfg.secret_file, enabled=True)

    if args.rotate:
        before = auth.pairing_code()
        auth.rotate()
        print(f"\n  rotated  {before}  ->  {auth.pairing_code()}")
        print("  Every paired device must pair again.\n")
        return 0

    if args.json:
        print(json.dumps({"pairing_code": auth.pairing_code(),
                          "secret_file": str(cfg.secret_file)}))
        return 0

    print(f"\n  Pairing code  {auth.pairing_code()}\n")
    print("  Enter this once on each client PC at the /pair prompt.")
    print("  It stays the same until you run 'pbrforge code --rotate'.\n")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from .server.discover import discover, probe

    if args.host:
        found = probe(args.host, port=args.port, timeout=args.timeout)
        results = [found] if found else []
        errors: list[str] = []
        if not found:
            print(f"\n  no PBRForge server answered at {args.host}:{args.port}\n")
            print("  Check that 'pbrforge serve' is running there, and that UDP "
                  f"{args.port}")
            print("  is allowed through the firewall on that machine.\n")
            return 1
    else:
        print(f"\n  broadcasting on UDP {args.port}, waiting {args.timeout}s\u2026")
        outcome = discover(timeout=args.timeout, port=args.port)
        if outcome.errors:
            errors = outcome.errors
        else:
            errors = []
        results = outcome.found

    if args.json:
        print(json.dumps({
            "found": [{"host": a.host, "url": a.url, "version": a.version,
                       "auth": a.auth, "library": a.library} for a in results],
            "errors": errors,
        }, indent=2))
        return 0 if results else 1

    if not results:
        print("\n  No servers found.")
        print("  Other things to try:")
        print("    \u00b7 ask the person at the server for its IP and use --host <ip>")
        print("    \u00b7 check both machines are on the same subnet (not guest Wi-Fi)")
        print("    \u00b7 some routers block broadcast traffic between wireless clients;")
        print("      the server's address is printed in its console banner\n")
        return 1

    print()
    print(f"  {'HOST':<24} {'URL':<34} {'VERSION':<8} AUTH")
    for a in results:
        print(f"  {a.describe()}")
    print()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor
    cfg = _cfg(args)
    report = doctor.run(cfg, full=args.full)

    if args.json:
        print(json.dumps({
            "app": "pbrforge", "version": __version__, "ready": report.ready,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail,
                        "fix": c.fix} for c in report.checks],
        }, indent=2))
        return 0 if report.ready else 1

    print(doctor.render(report))
    if not report.ready:
        print("  Fix the FAIL lines above, then run 'pbrforge doctor' again.\n")
    return 0 if report.ready else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    from . import selftest
    results, root = selftest.run(quick=args.quick, keep=args.keep, verbose=args.verbose)
    print(selftest.render(results, root, kept=args.keep))
    return 0 if all(r.ok for r in results) else 1


def cmd_tls(args: argparse.Namespace) -> int:
    from . import tls
    cfg = _cfg(args)

    if args.action == "status":
        ok, why = tls.backend()
        st = tls.status(cfg)
        if args.json:
            print(json.dumps({
                "backend": {"available": ok, "detail": why},
                "certificate": {
                    "present": st.present,
                    "path": str(st.cert_path),
                    "days_left": st.days_left,
                    "names": st.names,
                    "missing_current_ips": st.missing_current_ips,
                    "healthy": st.healthy,
                },
                "tls_enabled_in_config": cfg.tls,
            }, indent=2))
            return 0

        print(f"\n  certificate backend  {why}")
        if not st.present:
            print("  certificate          not generated")
            print("\n  Run:  pbrforge tls init\n")
            return 1
        print(f"  certificate          {st.cert_path}")
        print(f"  expires              {st.not_after:%Y-%m-%d}  ({st.days_left} days)")
        print(f"  covers               {', '.join(st.names) or '(no SANs)'}")
        if st.missing_current_ips:
            print(f"  WARNING              does not cover {', '.join(st.missing_current_ips)}")
            print("                       clients using that address will see a cert warning")
            print("                       fix:  pbrforge tls init --force")
        print(f"  config               tls = {str(cfg.tls).lower()}")
        if not cfg.tls:
            print("\n  TLS is off. Run 'pbrforge serve --tls' or set tls = true in the")
            print("  config to use it. LAN clients on plain http lose WebGPU.\n")
        else:
            print()
        return 0

    if args.action == "trust":
        try:
            target = tls.write_trust_script(cfg)
        except tls.TlsError as exc:
            return _fail(str(exc))
        folder = target.parent
        print(f"\n  Wrote {folder}")
        print(f"    ca.pem            the CA to trust")
        print(f"    trust-ca.ps1      run this on each client PC")
        print(f"    READ-ME.txt       instructions for the other machine")
        print(f"\n  Copy that folder to the other PC and run trust-ca.ps1 there.")
        print(f"  Without it, browsers warn, and LAN clients fall back to WebGL2.\n")
        return 0

    # init
    made = tls.init(cfg, force=args.force, extra_hosts=args.host_name or [])
    if not made:
        print(f"\n  Certificate already exists at {cfg.cert_file}")
        print("  Use --force to regenerate (every client must re-trust afterwards).\n")
    else:
        print("\n  Generated:")
        for path in made:
            print(f"    {path}")
    st = tls.status(cfg)
    print(f"\n  Covers    {', '.join(st.names)}")
    print(f"  Expires   {st.not_after:%Y-%m-%d}  ({st.days_left} days)")
    print("\n  Next, on each client PC (once):")
    print(f"    pbrforge tls trust        \u2192 writes a folder to run on that machine")
    print("\n  Then start the server with TLS:")
    print("    pbrforge serve --tls\n")
    if not cfg.tls:
        print("  (or set tls = true in the config so it is always on)\n")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    cache = Cache(cfg.cache)

    if args.prune is not None:
        files, freed = cache.prune(older_than_days=args.prune)
        print(f"\n  pruned {files} files, freeing {human_bytes(freed)}"
              f"  (older than {args.prune:g} days)\n")
        if args.prune < 1:
            print("  note: pruning with a threshold under a day will discard artifacts")
            print("        you may still want; only useful when troubleshooting\n")
        return 0

    size = cache.size_bytes()
    print(f"\n  cache    {cfg.cache}")
    print(f"  size     {human_bytes(size)}")
    print(f"\n  prune older than 30 days:  pbrforge cache --prune 30\n")
    return 0


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))       # type: ignore[attr-defined]
        else:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"  could not open {path}: {exc}", file=sys.stderr)


def _wait_job(job: Any, *, show_stages: bool = True) -> None:
    last = None
    while not job._done.is_set():
        if show_stages and job.stage != last:
            print(f"  {job.progress * 100:5.1f}%  {job.stage:<14} "
                  f"{job.message[:96]}")
            last = job.stage
        job._done.wait(0.25)
    if show_stages and job.stage != last:
        print(f"  {job.progress * 100:5.1f}%  {job.stage:<14} "
              f"{job.message[:96]}")


def _print_job_result(job: Any) -> None:
    data = job.to_dict()
    if job.state == "failed":
        print(f"\n  failed   {job.name}")
        for line in (job.error or "the job failed").splitlines():
            print(f"  {line}" if line.startswith(" ") else f"  {line}")
        print(f"\n  staging  {job.folder or '(removed)'}\n")
        return
    qc = data.get("qc") or {}
    verdict = qc.get("verdict") if isinstance(qc, dict) else (qc or "—")
    print(f"\n  material {job.name}")
    print(f"  folder   {job.folder}")
    print(f"  qc       {verdict or '—'}")
    print(f"  time     {job.seconds:.1f} s")
    if job.folder:
        zip_path = next(Path(job.folder).glob("PBRForge-*.zip"), None)
        if zip_path:
            print(f"  zip      {zip_path}")
    print()


def cmd_make(args: argparse.Namespace) -> int:
    from .jobs import JobManager
    from .core.errors import LimitError

    cfg = _cfg(args)
    cfg.ensure_dirs()
    source = Path(args.input).expanduser()
    if not source.is_file():
        return _fail(f"{source} does not exist")
    manager = JobManager(cfg, workers=1)
    try:
        job = manager.submit_file(
            source,
            name=args.name or "",
            preset=args.preset,
            category=args.category,
            resolution=args.resolution,
            metal=args.metal,
            seamless=not args.no_seamless,
            delight=not args.no_delight,
            auto_levels=args.auto_levels,
            white_balance=args.white_balance,
            square=args.square,
            height_polarity=args.height,
        )
        if not args.json:
            print(f"\n  source   {source}")
            print(f"  name     {job.name}")
            print(f"  preset   {job.preset}  ·  {job.resolution}px  ·  "
                  f"worker {job.id}\n")
        _wait_job(job, show_stages=not args.json)
        if args.json:
            print(json.dumps(job.to_dict(), indent=2))
        else:
            _print_job_result(job)
        if args.open and job.folder:
            _open_path(Path(job.folder))
        return 0 if job.state == "done" else 1
    except LimitError as exc:
        return _fail(str(exc))
    finally:
        manager.shutdown()


def cmd_batch(args: argparse.Namespace) -> int:
    from .jobs import JobManager
    from .core.errors import LimitError

    cfg = _cfg(args)
    cfg.ensure_dirs()
    sources: list[Path] = []
    for raw in args.inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            sources.extend(sorted(path.glob("*.png")))
        elif path.is_file():
            sources.append(path)
        else:
            print(f"  skip     {path} (not found)", file=sys.stderr)
    if not sources:
        return _fail("no PNG files matched those paths")
    manager = JobManager(cfg, workers=1)
    results = []
    try:
        for index, source in enumerate(sources, start=1):
            if source.suffix.lower() != ".png":
                print(f"  [{index}/{len(sources)}] skip {source.name}: not a PNG")
                continue
            job = manager.submit_file(
                source, name=args.name_prefix + source.stem if args.name_prefix else "",
                preset=args.preset, category=args.category,
                resolution=args.resolution, metal=args.metal,
                seamless=not args.no_seamless, delight=not args.no_delight,
                auto_levels=args.auto_levels, white_balance=args.white_balance,
                square=args.square, height_polarity=args.height,
            )
            print(f"  [{index}/{len(sources)}] {source.name} → {job.id}")
            _wait_job(job, show_stages=bool(args.verbose))
            results.append(job.to_dict())
            if job.state == "done":
                print(f"       done {job.seconds:.1f}s  {job.folder}")
            else:
                print(f"       FAILED  {(job.error or '').splitlines()[0]}")
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            done = sum(1 for item in results if item["state"] == "done")
            print(f"\n  batch    {done}/{len(sources)} done  ·  "
                  f"{len(sources) - done} failed\n")
        return 0 if all(item["state"] == "done" for item in results) else 1
    except LimitError as exc:
        return _fail(str(exc))
    finally:
        manager.shutdown()


def cmd_library(args: argparse.Namespace) -> int:
    from .jobs import JobManager

    cfg = _cfg(args)
    manager = JobManager(cfg, workers=1)
    try:
        materials = manager.list_library()
        if args.json:
            print(json.dumps(materials, indent=2))
            return 0
        if not materials:
            print(f"\n  library  {cfg.library}")
            print("  no materials yet. Forge one with:  pbrforge make photo.png\n")
            return 0
        print(f"\n  library  {cfg.library}\n")
        print(f"  {'name':<30} {'qc':<6} {'preset':<18} {'size':>6}  folder")
        for item in materials:
            name = str(item.get("name") or item.get("slug") or "?")[:29]
            qc = str(item.get("verdict") or "—")[:5]
            preset = str(item.get("preset") or "—")[:17]
            size = item.get("resolution")
            size_text = f"{size}px" if size else "—"
            print(f"  {name:<30} {qc:<6} {preset:<18} {size_text:>6}  {item.get('slug')}")
        print()
        if args.open is not None:
            target = cfg.library / args.open if args.open else cfg.library
            if target.is_dir():
                _open_path(target)
            else:
                return _fail(f"{target} is not a library folder")
        return 0
    finally:
        manager.shutdown()


def cmd_update(args: argparse.Namespace) -> int:
    from . import update as update_mod
    from .core.errors import LimitError

    cfg = _cfg(args)
    info = update_mod.check(args.repo or cfg.update_repo)
    if args.json:
        print(json.dumps(info.to_dict(), indent=2))
    else:
        print(f"\n  current  {info.current}")
        print(f"  latest   {info.latest or '—'}")
        if info.error:
            print(f"  status   {info.error}")
        elif info.available:
            print(f"  status   update available  ·  {info.asset_name or 'no installer asset'}")
            if info.html_url:
                print(f"  release  {info.html_url}")
            if info.notes:
                print()
                for line in info.notes.splitlines()[:12]:
                    print(f"  {line}")
        else:
            print("  status   up to date")
        print()
    if not args.install:
        return 0
    if not info.available:
        return _fail(info.error or "there is no newer release to install")
    try:
        target = update_mod.install(info)
    except LimitError as exc:
        return _fail(str(exc))
    print(f"\n  installer started  {target}")
    print("  The server will stop when the installer replaces the files.\n")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _cfg(args)

    if args.set:
        from dataclasses import fields
        from .config import Config as _Config
        known = {f.name for f in fields(_Config)}
        aliases = {"check": "update_check", "auto_install": "update_auto_install",
                   "repo": "update_repo", "update.check": "update_check",
                   "update.auto_install": "update_auto_install",
                   "update.repo": "update_repo"}
        changed = []
        for assignment in args.set:
            if "=" not in assignment:
                return _fail(f"expected KEY=VALUE, got {assignment!r}")
            key, raw = assignment.split("=", 1)
            attr = aliases.get(key.strip().lower(), key.strip().lower())
            if attr not in known or attr in ("config_dir", "config_file", "source",
                                             "load_error"):
                return _fail(f"unknown setting {key!r}; run 'pbrforge config --json' "
                             f"to see names")
            current = getattr(cfg, attr)
            if isinstance(current, bool):
                value = raw.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int) and not isinstance(current, bool):
                try:
                    value = int(raw)
                except ValueError:
                    return _fail(f"{key} expects an integer, got {raw!r}")
            elif isinstance(current, Path):
                value = Path(raw).expanduser()
            else:
                value = raw
            setattr(cfg, attr, value)
            changed.append((attr, current, value))
        try:
            target = cfg.save()
        except OSError as exc:
            return _fail(f"could not write {cfg.config_file}: {exc}")
        print(f"\n  wrote  {target}")
        for attr, old, new in changed:
            print(f"  {attr:<22} {old}  →  {new}")
        print()
        return 0

    if args.write:
        target = cfg.write_default(force=args.force)
        verb = "wrote" if args.force or target == cfg.config_file else "exists"
        print(f"\n  {verb}  {target}\n")
        return 0

    if args.paths:
        for label, path in (("config", cfg.config_file), ("config dir", cfg.config_dir),
                            ("library", cfg.library), ("cache", cfg.cache),
                            ("temp", cfg.temp), ("tls", cfg.tls_dir)):
            print(f"  {label:<12} {path}")
        return 0

    if args.json:
        print(json.dumps(cfg.as_dict(), indent=2))
        return 0

    width = max(len(k) for k in cfg.as_dict())
    print(f"\n  effective configuration  (from {cfg.source})")
    print(f"  file                     {cfg.config_file}")
    print()
    for key, value in cfg.as_dict().items():
        print(f"  {key:<{width}}  {value}")
    print()
    print("  PBRFORGE_* environment variables override every value above.")
    print("  Example:  set PBRFORGE_PORT=7790 && pbrforge serve\n")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps({"app": APP_NAME, "version": __version__,
                          "python": sys.version.split()[0],
                          "frozen": bool(getattr(sys, "frozen", False)),
                          "executable": sys.executable}))
    else:
        kind = "packaged build" if getattr(sys, "frozen", False) else "source checkout"
        print(f"{APP_NAME} {__version__}  \u00b7  {kind}  \u00b7  "
              f"Python {sys.version.split()[0]}")
    return 0


# --------------------------------------------------------------------- parser
COMMANDS = ("serve", "make", "batch", "library", "stop", "doctor", "selftest",
            "url", "code", "discover", "tls", "cache", "config", "update",
            "version", "help")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pbrforge",
        description=f"{APP_NAME} \u2014 make texture images seamless and turn them into "
                    f"PBR materials.",
        epilog="Run 'pbrforge doctor' if anything misbehaves; it checks this machine "
               "and every path it can act on.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add_common(p: argparse.ArgumentParser) -> None:
        # --config-dir lives on the subcommands rather than on the top level so
        # that `pbrforge --config-dir X` (bare, meaning serve) and
        # `pbrforge serve --config-dir X` parse identically. Putting it on both
        # levels hits argparse's subparser-default-overwrites-parent bug.
        p.add_argument("--config-dir", metavar="DIR",
                       help="use DIR instead of the per-user config location")
        p.add_argument("--tls", dest="tls", action="store_true", default=None,
                       help="serve over HTTPS (needs 'pbrforge tls init')")
        p.add_argument("--no-tls", dest="tls", action="store_false",
                       help="serve over plain HTTP")
        p.add_argument("--no-auth", dest="auth", action="store_false", default=None,
                       help="DANGEROUS: skip the pairing code entirely")
        p.add_argument("--library", metavar="DIR", help="override the library folder")
        p.add_argument("--cache", metavar="DIR", help="override the cache folder")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("serve", help="run the local server (default command)")
    p.add_argument("--host", metavar="ADDR", help="bind address (default 0.0.0.0)")
    p.add_argument("--port", type=int, metavar="N", help="port (default 7788)")
    p.add_argument("--span", type=int, default=8, metavar="N",
                   help="try up to N ports if the first is taken (default 8)")
    p.add_argument("--open", dest="open_browser", action="store_true", default=None,
                   help="open a browser on this machine once serving")
    p.add_argument("--no-discovery", dest="discovery", action="store_false", default=None,
                   help="do not answer LAN discovery broadcasts")
    p.add_argument("-v", "--verbose", action="store_true", help="log every request")
    add_common(p)

    def add_job_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", metavar="TEXT", help="material name (default: file stem)")
        p.add_argument("--preset", default="generic-pbr", metavar="NAME",
                       help="export preset (default generic-pbr)")
        p.add_argument("--category", default="unknown",
                       choices=["unknown", "brick", "marble"],
                       help="material type; brick flips the height polarity")
        p.add_argument("--resolution", type=int, default=1024, metavar="PX",
                       help="working long edge, 256..4096 (default 1024)")
        p.add_argument("--metal", default="auto", choices=["auto", "none", "full"],
                       help="metalness decision (default auto = honest dielectric)")
        p.add_argument("--height", default="auto",
                       choices=["auto", "normal", "invert"],
                       help="bright pixels are high, or recessed")
        p.add_argument("--auto-levels", action="store_true",
                       help="stretch the histogram (off by default; can clip)")
        p.add_argument("--white-balance", action="store_true",
                       help="neutralise a colour cast")
        p.add_argument("--square", action="store_true", help="centre-crop to a square")
        p.add_argument("--no-seamless", action="store_true", help="skip the seam heal")
        p.add_argument("--no-delight", action="store_true",
                       help="skip illumination flattening")

    p = sub.add_parser("make", help="convert one PNG photo into a PBR material")
    p.add_argument("input", metavar="PHOTO.png")
    add_job_options(p)
    p.add_argument("--open", action="store_true", help="open the result folder")
    add_common(p)

    p = sub.add_parser("batch", help="convert every PNG in a folder")
    p.add_argument("inputs", nargs="+", metavar="FILE_OR_DIR")
    add_job_options(p)
    p.add_argument("--name-prefix", default="", metavar="TEXT",
                   help="prefix every material name")
    p.add_argument("-v", "--verbose", action="store_true", help="show every stage")
    add_common(p)

    p = sub.add_parser("library", help="list finished materials")
    p.add_argument("--open", nargs="?", const="", metavar="SLUG",
                   help="open the library, or one material folder")
    add_common(p)

    p = sub.add_parser("update", help="check GitHub for a newer release")
    p.add_argument("--check", action="store_true", help="check (the default action)")
    p.add_argument("--install", action="store_true",
                   help="download and run the installer (Windows)")
    p.add_argument("--repo", metavar="OWNER/NAME", help="override the GitHub repo")
    add_common(p)

    p = sub.add_parser("doctor", help="check whether this machine can run PBRForge")
    p.add_argument("--full", action="store_true", help="probe everything, including the GPU")
    add_common(p)

    p = sub.add_parser("selftest", help="prove the install works, end to end")
    p.add_argument("--quick", action="store_true", help="skip the slow texture tests")
    p.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    p.add_argument("-v", "--verbose", action="store_true", help="full tracebacks")
    add_common(p)

    p = sub.add_parser("url", help="print the addresses other PCs should use")
    add_common(p)

    p = sub.add_parser("stop", help="stop a server started on this machine")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when there was nothing to stop")
    add_common(p)
    # --json comes from add_common(), along with --config-dir. Duplicating it here
    # raises argparse.ArgumentError at import time, which takes down every command,
    # not just this one.

    p = sub.add_parser("code", help="show or rotate the pairing code")
    p.add_argument("--rotate", action="store_true",
                   help="generate a new secret, revoking every paired client")
    add_common(p)

    p = sub.add_parser("discover", help="find PBRForge servers on this network")
    p.add_argument("--host", metavar="IP", help="ask one specific machine instead of broadcasting")
    p.add_argument("--port", type=int, default=None, help="discovery UDP port (default 7789)")
    p.add_argument("--timeout", type=float, default=1.5, metavar="SEC",
                   help="how long to listen (default 1.5)")
    add_common(p)

    p = sub.add_parser("tls", help="create and manage the LAN certificate")
    p.add_argument("action", nargs="?", default="status",
                   choices=["init", "status", "trust"],
                   help="init = generate, status = inspect, trust = write the client folder")
    p.add_argument("--force", action="store_true", help="regenerate even if one exists")
    p.add_argument("--host-name", action="append", metavar="NAME",
                   help="extra DNS name to include (repeatable)")
    add_common(p)

    p = sub.add_parser("cache", help="inspect or prune the artifact cache")
    p.add_argument("--prune", nargs="?", const=30.0, type=float, metavar="DAYS",
                   help="delete artifacts older than DAYS (default 30)")
    p.add_argument("--size", action="store_true", help="print the cache size and exit")
    add_common(p)

    p = sub.add_parser("config", help="show or change the configuration file")
    p.add_argument("--write", action="store_true", help="write the default config file")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.add_argument("--paths", action="store_true", help="print the resolved paths only")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="persist one setting (repeatable); rewrites config.toml")
    add_common(p)

    p = sub.add_parser("version", help="print the version")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]

    # Bare `pbrforge` serves. It is the reason the tool exists, and a help screen
    # as the default action wastes the one moment a new user is paying attention.
    #
    # The rules, in full:
    #   (nothing)            -> serve
    #   -h / --help / --version -> as typed
    #   starts with a dash   -> serve, with the flags passed through
    #   a known command      -> as typed
    #   anything else        -> as typed, so argparse reports 'invalid choice'
    #                           instead of a confusing 'unrecognized arguments'
    if not raw:
        raw = ["serve"]
    elif raw[0] in ("-h", "--help", "--version"):
        pass
    elif raw[0] == "help":
        raw = ["--help"]
    elif raw[0].startswith("-"):
        raw = ["serve"] + raw

    parser = build_parser()
    args = parser.parse_args(raw)

    command = args.command or "serve"
    handlers = {
        "serve": cmd_serve, "make": cmd_make, "batch": cmd_batch,
        "library": cmd_library, "stop": cmd_stop, "doctor": cmd_doctor,
        "selftest": cmd_selftest, "url": cmd_url, "code": cmd_code,
        "discover": cmd_discover, "tls": cmd_tls, "cache": cmd_cache,
        "config": cmd_config, "update": cmd_update, "version": cmd_version,
    }
    handler = handlers.get(command)
    if handler is None:
        parser.print_help()
        return 2

    if command == "discover" and getattr(args, "port", None) is None:
        from . import DISCOVERY_PORT
        args.port = DISCOVERY_PORT

    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\n  interrupted\n")
        return 130
    except RuntimeError as exc:
        # RuntimeError is the convention for "the user can fix this".
        return _fail(str(exc))
    except Exception as exc:      # noqa: BLE001
        if os.environ.get("PBRFORGE_TRACEBACK"):
            raise
        print(f"\n  unexpected error in '{command}': {type(exc).__name__}: {exc}")
        print(f"  This is a bug. Re-run with PBRFORGE_TRACEBACK=1 for the traceback,")
        print(f"  or run 'pbrforge selftest -v' and report the output.\n")
        return 1
    finally:
        _pause_if_double_clicked()
