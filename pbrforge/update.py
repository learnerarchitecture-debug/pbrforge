"""GitHub release checking and silent installation.

The network client is standard library; Ed25519 verification uses the bundled
`cryptography` package through `pbrforge.security`.

The tool is personal and local, so the update path is deliberately small and
inspectable:

  * one GET to the public GitHub Releases API, with the app's User-Agent;
  * semantic-version comparison against ``__version__``;
  * an explicit asset choice (the Inno Setup installer), never "whatever .exe is
    first";
  * SHA-256 and size verification, plus an Ed25519 signature over the exact
    installer bytes for v2.0 and later;
  * an Inno Setup silent install, launched only by an explicit click or an
    explicit config flag.

There is no telemetry, no background upload, and no fallback download from a
mirror. If GitHub cannot be reached, the check says so and the local build keeps
working. A failed update must never take a working tool offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import APP_SLUG, USER_AGENT, __version__
from .core.errors import LimitError

__all__ = [
    "Update", "parse_version", "is_newer", "check", "load_cached",
    "save_cached", "download", "install", "asset_name",
]

DEFAULT_REPO = "learnerarchitecture-debug/pbrforge"
API = "https://api.github.com/repos/{repo}/releases/latest"

# The release signing key is intentionally embedded, not loaded from the config
# directory.  An attacker who can replace a key file next to the app can sign a
# malicious installer; a key compiled into the binary has to be replaced along
# with the binary, which is the same trust boundary as the update code itself.
# The matching private key is never in this repository.
RELEASE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAUprD27Kb0eIq5Cnz4VjxPXBjU6GHM62sLUENft4H9RA=
-----END PUBLIC KEY-----
"""
MIN_SIGNED_VERSION = (2, 0, 0)


def parse_version(text: str) -> tuple[int, int, int, int]:
    """Return (major, minor, patch, prerelease_rank).

    A release tag like ``v0.2.0`` is stable (rank 1); ``v0.2.0-rc1`` sorts before
    it (rank 0). Unknown tags sort as 0.0.0 so they can never masquerade as an
    upgrade.
    """
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return (0, 0, 0, 0)
    suffix = (text or "")[match.end():].strip()
    prerelease = 0 if suffix.startswith("-") else 1
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)),
            prerelease)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def asset_name(version: str) -> str:
    return f"PBRForge-Setup-{version}.exe"


@dataclass
class Update:
    current: str
    latest: str
    tag: str
    available: bool
    html_url: str = ""
    published_at: str = ""
    notes: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    digest: str = ""
    signature_name: str = ""
    signature_url: str = ""
    signature_size: int = 0
    verified_signature: bool = False
    error: str = ""
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "latest": self.latest,
            "tag": self.tag,
            "available": self.available,
            "html_url": self.html_url,
            "published_at": self.published_at,
            "notes": self.notes,
            "asset_name": self.asset_name,
            "asset_url": self.asset_url,
            "asset_size": self.asset_size,
            "digest": self.digest,
            "signature_name": self.signature_name,
            "signature_url": self.signature_url,
            "signature_size": self.signature_size,
            "verified_signature": self.verified_signature,
            "error": self.error,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Update":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


def _request(url: str, *, timeout: float = 10.0) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def _choose_asset(release: dict[str, Any], version: str) -> dict[str, Any] | None:
    assets = [a for a in (release.get("assets") or [])
              if isinstance(a, dict) and a.get("name", "").lower().endswith(".exe")]
    if not assets:
        return None
    wanted = asset_name(version)
    for asset in assets:
        if asset.get("name") == wanted:
            return asset
    for asset in assets:
        name = asset.get("name", "").lower()
        if "setup" in name and "pbrforge" in name:
            return asset
    for asset in assets:
        if asset.get("name", "").lower() == "pbrforge.exe":
            return asset
    return None


def _choose_signature(release: dict[str, Any], asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not asset:
        return None
    wanted = str(asset.get("name") or "") + ".sig"
    for candidate in release.get("assets") or []:
        if isinstance(candidate, dict) and candidate.get("name") == wanted:
            return candidate
    return None


def check(repo: str = DEFAULT_REPO, *, timeout: float = 10.0) -> Update:
    """Ask GitHub for the latest release. Never raises for network conditions."""
    url = API.format(repo=repo)
    try:
        with urllib.request.urlopen(_request(url, timeout=timeout),
                                    timeout=timeout) as response:
            raw = response.read(2_000_000)
        release = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return Update(__version__, __version__, "", False,
                          error="no GitHub release exists yet")
        return Update(__version__, __version__, "", False,
                      error=f"GitHub returned HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Update(__version__, __version__, "", False,
                      error=f"cannot reach GitHub: {exc}")
    except (ValueError, UnicodeError) as exc:
        return Update(__version__, __version__, "", False,
                      error=f"GitHub returned invalid JSON: {exc}")

    tag = str(release.get("tag_name") or "")
    latest = tag.lstrip("vV") or str(release.get("name") or "")
    version = ".".join(str(v) for v in parse_version(latest)[:3])
    asset = _choose_asset(release, version)
    notes = str(release.get("body") or "").strip()
    if len(notes) > 6000:
        notes = notes[:6000].rstrip() + "\n\n[release notes truncated]"
    update = Update(
        current=__version__,
        latest=version,
        tag=tag,
        available=is_newer(version, __version__),
        html_url=str(release.get("html_url") or ""),
        published_at=str(release.get("published_at") or ""),
        notes=notes,
    )
    if update.available and asset is None:
        update.available = True
        update.error = ("the latest release has no Windows installer asset; "
                        "build deploy\\build-installer.ps1 or download the source")
    if asset is not None:
        update.asset_name = str(asset.get("name") or "")
        update.asset_url = str(asset.get("browser_download_url") or "")
        update.asset_size = int(asset.get("size") or 0)
        update.digest = str(asset.get("digest") or "")
        signature = _choose_signature(release, asset)
        if signature is not None:
            update.signature_name = str(signature.get("name") or "")
            update.signature_url = str(signature.get("browser_download_url") or "")
            update.signature_size = int(signature.get("size") or 0)
        elif parse_version(version)[:3] >= MIN_SIGNED_VERSION:
            update.error = ("this release has no Ed25519 signature; "
                            "PBRForge will not auto-install it")
    return update


def save_cached(path: Path, update: Update) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(update.to_dict(), indent=2, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
        tmp.replace(path)
    except OSError:
        pass


def load_cached(path: Path, *, max_age_s: float = 24 * 3600) -> Update | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        update = Update.from_dict(payload)
    except (OSError, ValueError, TypeError):
        return None
    if time.time() - update.checked_at > max_age_s:
        return None
    return update


def download(update: Update, dest_dir: Path, *,
             progress: Callable[[int, int], None] | None = None) -> Path:
    if not update.asset_url:
        raise LimitError(
            "this release has no downloadable Windows installer.\n"
            "  why      the GitHub release exists, but its setup .exe asset does "
            "not\n"
            "  fix      build one with deploy\\build-installer.ps1, or open the "
            "release page and use the source archive")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if update.asset_name.lower().endswith(".exe") else ".bin"
    target = dest_dir / f"PBRForge-Setup-{update.latest}{suffix}"
    part = target.with_name(target.name + ".part")
    digest = update.digest.lower()
    expected = digest.split("sha256:", 1)[1] if digest.startswith("sha256:") else ""

    hasher = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(_request(update.asset_url, timeout=30),
                                    timeout=30) as response, \
                part.open("wb") as handle:
            length = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, length or update.asset_size)
        if update.asset_size and received != update.asset_size:
            raise LimitError(
                f"the downloaded installer is {received} bytes, expected "
                f"{update.asset_size}.\n"
                f"  why      a truncated download would fail halfway through an "
                f"install\n"
                f"  fix      retry, or download the asset from the release page")
        if expected and hasher.hexdigest().lower() != expected:
            raise LimitError(
                "the downloaded installer's SHA-256 does not match the release.\n"
                "  why      the bytes differ from the ones GitHub published\n"
                "  fix      do not run it; retry the download")
        requires_signature = parse_version(update.latest)[:3] >= MIN_SIGNED_VERSION
        if update.signature_url:
            sig_part = part.with_name(part.name + ".sig")
            try:
                with urllib.request.urlopen(_request(update.signature_url,
                                                     timeout=15), timeout=15) as response:
                    sig_raw = response.read(256 * 1024)
                if update.signature_size and len(sig_raw) != update.signature_size:
                    raise LimitError(
                        "the release signature is truncated.\n"
                        "  why      its size does not match GitHub's metadata\n"
                        "  fix      retry the update check")
                try:
                    signature = json.loads(sig_raw.decode("utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise LimitError(
                        "the release signature is not valid JSON; refusing to run "
                        "the installer") from exc
                from . import security
                if not security.verify_file_signature(
                        part, signature, public_key_pem=RELEASE_PUBLIC_KEY_PEM):
                    raise LimitError(
                        "the installer's Ed25519 signature did not verify.\n"
                        "  why      the download is not the file the release key signed\n"
                        "  fix      do not run it; download from the official release page")
                update.verified_signature = True
            finally:
                sig_part.unlink(missing_ok=True)
        elif requires_signature:
            raise LimitError(
                "this release does not publish an Ed25519 signature.\n"
                "  why      PBRForge 2.0+ requires a signature before auto-install\n"
                "  fix      download the installer from the release page and verify "
                "SHA256SUMS.txt manually, or ask the publisher to sign the asset")
        part.replace(target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return target


def install(update: Update, *, dest_dir: Path | None = None) -> Path:
    """Download and launch the Inno Setup installer silently. Returns its path."""
    if sys.platform != "win32":
        raise LimitError(
            "silent auto-install is Windows-only.\n"
            "  why      the release asset is an Inno Setup .exe\n"
            "  fix      download the release manually on this platform")
    if not update.available:
        raise LimitError("there is no newer release to install")
    target = download(update, dest_dir or Path(os.environ.get("TEMP", ".")) /
                      f"{APP_SLUG}-update")
    # /CLOSEAPPLICATIONS lets Inno's Restart Manager close a running PBRForge;
    # the installer's PrepareToInstall also calls `pbrforge stop`, belt and
    # braces, so an upgrade cannot fail because the old server holds the port.
    try:
        subprocess.Popen([
            str(target),
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
        ], close_fds=True)
    except OSError as exc:
        raise LimitError(
            f"could not start the installer: {exc}\n"
            f"  why      Windows refused to launch {target.name}\n"
            f"  fix      run it manually from {target}")
    return target
