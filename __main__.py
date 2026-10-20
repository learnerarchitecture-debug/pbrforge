"""`python -m pbrforge` and the frozen executable both land here.

Two Windows-specific things happen before anything else, because both of them
turn a working tool into a confusing one:

  1. The console code page is not UTF-8 by default. The banner uses box characters
     and the doctor output uses check marks, so printing them raises
     UnicodeEncodeError in a default PowerShell window. Reconfiguring the streams
     is one line and removes a class of bug reports.

  2. A packaged executable that raises during import shows a Windows error box or
     nothing at all. Catching that here and printing the traceback means the user
     gets something they can paste into a bug report.
"""

from __future__ import annotations

import sys


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:      # noqa: BLE001 - redirected or already wrapped
            pass


def main() -> int:
    _utf8_console()
    try:
        # ABSOLUTE import, deliberately, and this is the whole reason it is not
        # written the obvious way.
        #
        # `python -m pbrforge` executes this file with `pbrforge` as its parent
        # package, so `from .cli import main` resolves. PyInstaller does not: it
        # runs the entry script as a TOP-LEVEL module with no parent package, so
        # the relative form dies with
        #
        #     ImportError: attempted relative import with no known parent package
        #
        # after the exe has already started and printed its banner -- which reads
        # as "the installer is broken" rather than "the packaging is wrong".
        #
        # An absolute import works in both cases, because `pbrforge` is a real
        # importable package either way: the parent of the package is on sys.path
        # for `-m`, and PyInstaller's pathex does the same job in the bundle.
        from pbrforge.cli import main as cli_main
    except ImportError:
        try:
            from .cli import main as cli_main          # direct-file fallback
        except Exception:
            import traceback
            print("PBRForge failed to start.", file=sys.stderr)
            print("", file=sys.stderr)
            print("The 'pbrforge' package could not be imported. Likely causes, in order:",
                  file=sys.stderr)
            print("  - the package directory is missing next to this executable", file=sys.stderr)
            print("  - run `pbrforge doctor` to check the install", file=sys.stderr)
            print("", file=sys.stderr)
            traceback.print_exc()
            # A packaged exe is usually launched by double-click from Explorer,
            # where the console window closes instantly on exit and the traceback
            # above is never seen. Pausing is the difference between a reportable
            # error and "it just does nothing".
            try:
                if sys.stdin is not None and sys.stdin.isatty():
                    input("Press Enter to close...")
            except Exception:      # noqa: BLE001
                pass
            return 1
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
