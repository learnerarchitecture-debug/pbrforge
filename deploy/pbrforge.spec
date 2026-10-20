# =============================================================================
#  pbrforge.spec -- PyInstaller build definition for the single-file executable
#
#  Why a .spec file rather than a pile of --flags in a .ps1:
#
#   1. The build is reproducible. A spec is the build, so "the installer is
#      broken" is always answerable by diffing this file.
#   2. The exclusions are the important part and they need comments. A naive
#      PyInstaller run on a machine that happens to have torch installed
#      produces a 2 GB executable. Nobody notices until they try to copy it.
#   3. `collect_data_files('pbrforge')` picks up ui/ automatically, so adding a
#      UI asset cannot be forgotten in the build script.
#
#  Build:  pyinstaller deploy/pbrforge.spec --noconfirm
#  Output: dist/pbrforge.exe   (~11-14 MB with the exclusions below)
# =============================================================================

import sys
from pathlib import Path

# PyInstaller injects these: SPECPATH, DISTPATH, WORKPATH, SPEC.
#
# SPECPATH is the directory holding this .spec, i.e. <repo>/deploy. Its PARENT
# (parents[0]) is the repository root. This was written as parents[1] first,
# which silently pointed PROJECT_ROOT one level too high -- at the directory
# *containing* the repo -- so PyInstaller looked for <repo>/__main__.py, did not
# find it, and failed. Everything else degraded quietly rather than loudly:
# `datas` skips missing files by design, so the docs were simply omitted and the
# build would have "succeeded" without a UI.
#
# That is why build-portable.ps1 runs the artefact instead of trusting the exit
# code. A build that produces a working exe with no UI inside is worse than a
# build that fails, because it ships.
PROJECT_ROOT = Path(SPECPATH).resolve().parent         # noqa: F821 - injected

block_cipher = None

# -----------------------------------------------------------------------------
# Data files.
#
# `ui/` is the entire application UI. If it is missing from the bundle the exe
# starts and serves a 500 -- so it is listed explicitly rather than relied on
# via package data, and the build script asserts it exists afterwards.
# -----------------------------------------------------------------------------
datas = [
    (str(PROJECT_ROOT / "ui"), "ui"),
]

# Documentation ships alongside the executable, not inside it: reading a README
# should not require running a binary. The installer copies these into the
# install directory; PyInstaller only needs the ones the exe itself reads.
for name in ("README.md", "INSTALLATION.md", "DEPLOYMENT.md", "LICENSE.txt"):
    path = PROJECT_ROOT / name
    if path.is_file():
        datas.append((str(path), "."))

# -----------------------------------------------------------------------------
# Exclusions. This list is the difference between an 11 MB download and a 2 GB
# one, and between an installer that works on a clean PC and one that fails at
# import time on a machine with a different NumPy.
#
# Rule: the frozen exe is the SERVER and the CLI. It is stdlib-only on purpose
# (see pbrforge/server/simple.py). The imaging stack is used from a venv when the
# pipeline stages need it. So everything heavy is excluded, and `pbrforge doctor`
# reports what is missing rather than the exe carrying it.
# -----------------------------------------------------------------------------
excludes = [
    # --- scientific / ML stacks: hundreds of MB, not used by the frozen path ---
    "numpy", "scipy", "pandas", "matplotlib", "sklearn", "sympy", "numba",
    "torch", "torchvision", "torchaudio", "tensorflow", "keras", "jax",
    "cv2", "imageio", "skimage", "PIL", "Pillow",
    "cupy", "pycuda", "onnx", "onnxruntime", "transformers", "diffusers",
    "xformers", "triton", "accelerate", "safetensors",
    # --- GUI toolkits: PyInstaller pulls these in via stray imports -----------
    "tkinter", "Tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "IPython", "jupyter", "jupyterlab", "notebook", "nbformat",
    # --- test and lint scaffolding -------------------------------------------
    "pytest", "unittest2", "nose", "hypothesis", "coverage", "pylint",
    "black", "flake8", "mypy",
    # --- other -----------------------------------------------------------------
    "setuptools", "pip", "wheel", "pkg_resources", "distutils",
    "sqlite3", "pydoc_data", "test", "tests", "lib2to3",
]

# `cryptography` is optional at runtime (only `pbrforge tls init` needs it) but
# it is small, has no compiled dependencies of its own in modern wheels, and
# bundling it means LAN HTTPS works out of the box on a machine that has never
# run pip. If the build machine lacks it the exe still builds and `doctor` says
# so -- it is a graceful degradation, not a build failure.
hiddenimports = [
    "pbrforge.server.simple",
    "pbrforge.server.auth",
    "pbrforge.server.discover",
    "pbrforge.core.png",
    "pbrforge.core.cache",
    "pbrforge.core.synth",
]

a = Analysis(
    [str(PROJECT_ROOT / "pbrforge" / "__main__.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="pbrforge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX trips Windows Defender heuristics. Not worth it
                        # for the ~30% it saves; a quarantined installer is a
                        # support burden that never ends.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # CONSOLE, deliberately: this is a LAN server, and the
                        # console is where the URLs and the pairing code are
                        # printed. A windowed build would hide the one thing the
                        # user needs to connect a second PC.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    # Version metadata: shows up in the exe's Properties tab, which is the first
    # thing anyone checks when they are not sure which build is installed.
    version=None,
)
