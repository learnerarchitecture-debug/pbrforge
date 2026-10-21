"""Configuration: one TOML file per install, environment variables always win.

Precedence, highest first:
  1. command-line flags
  2. PBRFORGE_* environment variables
  3. config.toml
  4. built-in defaults

The config file is written once on first run. It is never rewritten
automatically -- a tool that silently edits your config is a tool you stop
trusting -- so new keys are added with their default value at read time and
`pbrforge config --write` is an explicit action.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_SLUG = "pbrforge"

DEFAULT_CONFIG_TOML = """\
# PBRForge configuration
# Written once on first run. PBRFORGE_* environment variables override every
# value here, which is what makes scheduled tasks and one-off experiments easy.

[server]
bind          = "0.0.0.0"     # 0.0.0.0 = reachable from other PCs on the LAN
port          = 7788          # plain HTTP port (redirects to HTTPS if tls = true)
https_port    = 7443
tls           = false         # true needs certs:  pbrforge tls init
auth          = true          # pairing code required. Only set false on a trusted LAN.
discovery     = true          # answer UDP beacons so clients can find this server
open_browser  = false         # open the local browser on start
max_upload_mb = 512

[paths]
library = ""                  # empty = <Documents>/PBRForge/library
cache   = ""                  # empty = <config>/cache
temp    = ""                  # empty = system temp

[performance]
max_workers       = 0         # 0 = physical cores - 1
vram_budget_mb    = 0         # 0 = auto-detect
cpu_mem_budget_mb = 6144

[defaults]
preset        = "generic-pbr"
resolution    = 4096
deterministic = true
ai_enabled    = false

[updates]
check        = true         # ask GitHub once per day whether a newer release exists
auto_install = false        # false = show the update, let the user click Install
repo         = "learnerarchitecture-debug/pbrforge"

[ui]
theme  = "dark"
accent = "amber"
"""


def _as_path(value: object, fallback: Path) -> Path:
    """Resolve one path setting. Empty string and None both mean "use the default",
    because that is what an empty value in a config file obviously means to the
    person who typed it."""
    if value is None:
        return Path(fallback)
    text = str(value).strip()
    if not text:
        return Path(fallback)
    return Path(text).expanduser()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"PBRFORGE_{name}", default)


def default_config_dir() -> Path:
    """Per-user, per-OS config location.

    Deliberately NOT next to the install. The install directory gets replaced on
    upgrade; the config, the secret and the TLS material must survive that.
    """
    override = _env("CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return Path(base) / APP_SLUG
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_SLUG
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SLUG


def default_temp_dir() -> Path:
    """A scratch space we own, rather than the shared system temp root.

    The pipeline writes multi-gigabyte intermediates. Dropping those directly in
    %TEMP% means a disk-cleanup tool can delete them mid-job, and it means one
    failed run can fill the system drive without it being obvious which tool did it.
    """
    return Path(tempfile.gettempdir()) / APP_SLUG


def default_library_dir() -> Path:
    if sys.platform == "win32":
        docs = Path(os.path.expanduser("~")) / "Documents"
        return docs / "PBRForge" / "library"
    return Path.home() / "PBRForge" / "library"


def _parse_toml(text: str) -> dict[str, Any]:
    """Parse the subset of TOML this file uses.

    tomllib (3.11+) is used when available. The hand-rolled fallback exists so the
    server can start on a Python whose stdlib predates tomllib -- a real risk on a
    second PC where the user installed whatever Python they already had.
    """
    try:
        import tomllib  # type: ignore[import-not-found]
        return tomllib.loads(text)
    except ModuleNotFoundError:
        pass

    data: dict[str, Any] = {}
    section: dict[str, Any] = data
    section_name = ""
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            section = data.setdefault(section_name, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        section[key.strip()] = _coerce(value.strip())
    return data


def _coerce(value: str) -> Any:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p.strip()) for p in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _dump_toml(data: dict[str, Any]) -> str:
    """Minimal writer: scalars and one level of tables. Enough for our schema."""
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {_fmt(v)}")
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{k}]")
        for kk, vv in v.items():
            lines.append(f"{kk} = {_fmt(vv)}")
    return "\n".join(lines) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return '""'
    if isinstance(v, Path):
        v = str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(i) for i in v) + "]"
    return f'"{v}"'


@dataclass
class Config:
    # server
    bind: str = "0.0.0.0"
    port: int = 7788
    https_port: int = 7443
    tls: bool = False
    auth: bool = True
    discovery: bool = True
    open_browser: bool = False
    max_upload_mb: int = 512
    # paths -- `None` means "not configured", resolved by resolve_paths().
    # A dataclass cannot compute a default from another field, and `Path("")` is
    # `Path(".")` rather than something falsy, so "" in the config file would
    # silently mean "the current directory" instead of "use the default".
    library: Path | None = None
    cache: Path | None = None
    temp: Path | None = None
    # performance
    max_workers: int = 0
    vram_budget_mb: int = 0
    cpu_mem_budget_mb: int = 6144
    # defaults
    preset: str = "generic-pbr"
    resolution: int = 4096
    deterministic: bool = True
    ai_enabled: bool = False
    # updates
    update_check: bool = True
    update_auto_install: bool = False
    update_repo: str = "learnerarchitecture-debug/pbrforge"
    # ui
    theme: str = "dark"
    accent: str = "amber"

    # ---- resolved locations (not from the file) ----
    config_dir: Path = field(default_factory=default_config_dir)
    config_file: Path = field(default_factory=lambda: default_config_dir() / "config.toml")
    source: str = "defaults"          # where the effective config came from
    load_error: str = ""              # set when config.toml exists but would not parse

    def __post_init__(self) -> None:
        self.resolve_paths()

    # ---------------------------------------------------------------- loading
    def resolve_paths(self) -> None:
        """Fill in the paths that depend on the config directory.

        Called after construction *and* again after `load()` has applied the file
        and the environment, because the cache and temp defaults are relative to
        `config_dir`, which the loader may have redirected. Idempotent: once a path
        is a real Path it passes straight through.
        """
        self.library = _as_path(self.library, default_library_dir())
        self.cache = _as_path(self.cache, Path(self.config_dir) / "cache")
        self.temp = _as_path(self.temp, default_temp_dir())

    @classmethod
    def load(cls, config_dir: Path | None = None, overrides: dict[str, Any] | None = None) -> "Config":
        cdir = Path(config_dir) if config_dir else default_config_dir()
        cfg = cls()
        cfg.config_dir = cdir
        cfg.config_file = cdir / "config.toml"

        known = {f.name for f in fields(cls)}

        if cfg.config_file.exists():
            try:
                # utf-8-sig, not utf-8. Windows editors add a BOM readily -- PowerShell's
                # `Set-Content -Encoding utf8` always does, and Notepad used to -- and
                # tomllib rejects one. Reading as utf-8-sig strips it, and is otherwise
                # byte-for-byte identical to utf-8.
                raw = _parse_toml(cfg.config_file.read_text(encoding="utf-8-sig"))
                cfg.source = str(cfg.config_file)
            except Exception as exc:                      # noqa: BLE001 - config must never crash startup
                # Loud on purpose. Falling back to defaults is the only safe thing to do,
                # but doing it *silently* means a typo -- or a BOM -- runs the whole
                # application on settings the user did not choose, and the symptom shows
                # up much later as "my library is in the wrong place".
                cfg.load_error = (
                    f"{cfg.config_file} could not be read: {exc}\n"
                    f"every setting is now the default; none of your config took effect"
                )
                print(f"warning: {cfg.load_error}", file=sys.stderr)
                raw = {}
        else:
            raw = {}

        flat: dict[str, Any] = {}
        for section, values in raw.items():
            if isinstance(values, dict):
                flat.update(values)
            else:
                flat[section] = values

        # `[updates] check = true` is friendlier than repeating the section
        # name in every key; map the three short names onto their attributes.
        for alias, attr in (("check", "update_check"),
                            ("auto_install", "update_auto_install"),
                            ("repo", "update_repo")):
            if alias in flat:
                flat[attr] = flat.pop(alias)

        for key, value in flat.items():
            if key in known and key not in ("config_dir", "config_file", "source", "load_error"):
                setattr(cfg, key, value)

        # environment overrides
        env_map = {
            "BIND": "bind", "PORT": "port", "HTTPS_PORT": "https_port", "TLS": "tls",
            "AUTH": "auth", "DISCOVERY": "discovery", "OPEN_BROWSER": "open_browser",
            "MAX_UPLOAD_MB": "max_upload_mb", "LIBRARY": "library", "CACHE": "cache",
            "TEMP": "temp", "MAX_WORKERS": "max_workers",
            "VRAM_BUDGET_MB": "vram_budget_mb", "CPU_MEM_BUDGET_MB": "cpu_mem_budget_mb",
            "PRESET": "preset", "RESOLUTION": "resolution", "AI_ENABLED": "ai_enabled",
            "UPDATE_CHECK": "update_check", "UPDATE_AUTO_INSTALL": "update_auto_install",
            "UPDATE_REPO": "update_repo",
            "THEME": "theme", "ACCENT": "accent",
        }
        touched = False
        for env_key, attr in env_map.items():
            val = os.environ.get(f"PBRFORGE_{env_key}")
            if val is None:
                continue
            setattr(cfg, attr, _coerce(val))
            touched = True
        if touched:
            cfg.source = "environment"

        for key, value in (overrides or {}).items():
            if value is not None and key in known:
                setattr(cfg, key, value)

        # normalise types / fill derived paths
        cfg.port = int(cfg.port)
        cfg.https_port = int(cfg.https_port)
        cfg.tls = bool(cfg.tls)
        cfg.auth = bool(cfg.auth)
        cfg.resolve_paths()
        return cfg

    # ---------------------------------------------------------------- helpers
    @property
    def secret_file(self) -> Path:
        return self.config_dir / "secret.key"

    @property
    def sessions_note(self) -> Path:
        return self.config_dir / "SESSIONS.txt"

    @property
    def tls_dir(self) -> Path:
        return self.config_dir / "tls"

    @property
    def cert_file(self) -> Path:
        return self.tls_dir / "server.pem"

    @property
    def key_file(self) -> Path:
        return self.tls_dir / "server.key"

    @property
    def ca_file(self) -> Path:
        return self.tls_dir / "ca.pem"

    @property
    def serve_port(self) -> int:
        """The port clients actually use, given whether TLS is on."""
        return self.https_port if self.tls else self.port

    @property
    def scheme(self) -> str:
        return "https" if self.tls else "http"

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.library, self.cache, self.tls_dir):
            if d is None:
                continue
            Path(d).mkdir(parents=True, exist_ok=True)

    def write_default(self, path: Path | None = None, force: bool = False) -> Path:
        target = path or self.config_file
        if target.exists() and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return target

    def save(self, path: Path | None = None) -> Path:
        """Write the effective values as a canonical config.toml.

        ``config --set`` needs to persist a change, and the standard library has
        no TOML writer. This writes every known field into its section, so the
        file is complete and `Config.load` reproduces exactly this object. It
        does not preserve comments: rewriting a config with a machine is the one
        operation that cannot, and saying so is better than silently dropping
        them. `--set` prints the file it wrote.
        """
        target = Path(path or self.config_file)
        sections: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
            ("server", (("bind", "bind"), ("port", "port"),
                        ("https_port", "https_port"), ("tls", "tls"),
                        ("auth", "auth"), ("discovery", "discovery"),
                        ("open_browser", "open_browser"),
                        ("max_upload_mb", "max_upload_mb"))),
            ("paths", (("library", "library"), ("cache", "cache"),
                       ("temp", "temp"))),
            ("performance", (("max_workers", "max_workers"),
                             ("vram_budget_mb", "vram_budget_mb"),
                             ("cpu_mem_budget_mb", "cpu_mem_budget_mb"))),
            ("defaults", (("preset", "preset"), ("resolution", "resolution"),
                          ("deterministic", "deterministic"),
                          ("ai_enabled", "ai_enabled"))),
            ("updates", (("check", "update_check"),
                         ("auto_install", "update_auto_install"),
                         ("repo", "update_repo"))),
            ("ui", (("theme", "theme"), ("accent", "accent"))),
        )
        lines = [
            "# PBRForge configuration",
            "# Written by `pbrforge config --set`; edit by hand or rewrite with --set.",
            "",
        ]
        for section, keys in sections:
            lines.append(f"[{section}]")
            for key, attr in keys:
                lines.append(f"{key:<15} = {_fmt(getattr(self, attr))}")
            lines.append("")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        return target

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name in ("config_dir", "config_file", "source"):
                continue
            value = getattr(self, f.name)
            if isinstance(value, Path):
                value = str(value)
            out[f.name] = value
        return out
