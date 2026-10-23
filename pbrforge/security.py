"""Local security primitives: encrypted vault backups, update signatures, audit.

This module is intentionally boring.  The threat model is a personal LAN tool,
not a multi-tenant service:

  * the vault protects a copy of the library and config at rest, so a backup
    copied to a USB stick, cloud drive or another PC is not readable without the
    passphrase;
  * every chunk is authenticated with AES-256-GCM, the key comes from scrypt,
    and the header is bound as additional authenticated data;
  * update signatures are Ed25519 over the exact installer bytes.  A signature
    is only useful if the public key is trusted, so the key lives in the source
    tree (``deploy/release-signing-public.pem``) and the private key never does;
  * nothing here contacts a network or stores a passphrase.

The format is deliberately self-describing and versioned.  A future format can
read this one without guessing.  Passphrases are never written to disk, logs or
error messages.

``cryptography`` is optional at *runtime* because the portable build can still
run the classical pipeline without it.  Vault and signature operations refuse
with a named fix when it is missing; they never fall back to a weak cipher.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import struct
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .core.errors import PbrForgeError

MAGIC = b"PBRFORGE-VAULT\x00"
VERSION = 1
KDF_SCRYPT = 1
SALT_LEN = 16
NONCE_PREFIX_LEN = 8
CHUNK_SIZE = 1024 * 1024
# 2**15 = 32 MiB of scrypt memory.  Stronger than interactive-login defaults,
# still under a second on the sort of laptop this tool targets.  Stored in the
# header, so an older vault remains readable if this is raised later.
DEFAULT_N = 2 ** 15
DEFAULT_R = 8
DEFAULT_P = 1
MAX_KDF_N = 2 ** 17
MAX_KDF_R = 32
MAX_KDF_P = 16
MAX_CHUNK_SIZE = 16 * 1024 * 1024

VAULT_EXT = ".pbrvault"


class SecurityError(PbrForgeError):
    """A security operation refused or failed for a reason the user can act on."""


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        from cryptography.exceptions import InvalidTag
    except Exception as exc:  # noqa: BLE001 - import failures are environment issues
        raise SecurityError(
            "the encryption backend is not available.\n"
            "  why      Python could not import the 'cryptography' package\n"
            "  fix      pip install cryptography\n"
            "  note     PBRForge refuses to substitute a weaker cipher"
        ) from exc
    return AESGCM, Scrypt, InvalidTag


def backend_available() -> tuple[bool, str]:
    try:
        _crypto()
        return True, "cryptography is available"
    except SecurityError as exc:
        return False, str(exc).splitlines()[0]


# ----------------------------------------------------------------- passphrase
_COMMON = {
    "password", "password1", "password123", "pbrforge", "letmein", "qwerty",
    "12345678", "123456789", "admin", "welcome", "iloveyou",
}


def validate_passphrase(passphrase: str) -> None:
    """Enforce a minimum that is worth calling encryption.

    Length is not the only measure of entropy, but it is the one this tool can
    verify without a dictionary or a network service.  A 12-character floor is
    deliberately modest; the UI suggests a generated four-word passphrase.
    """
    if not isinstance(passphrase, str):
        raise SecurityError("the passphrase must be text")
    if len(passphrase) < 12:
        raise SecurityError(
            f"the passphrase is {len(passphrase)} characters; at least 12 are required.\n"
            "  why      shorter passphrases are cheap to test offline against a stolen vault\n"
            "  fix      use four unrelated words, or a password manager's generated phrase"
        )
    if passphrase.strip().lower() in _COMMON:
        raise SecurityError("that passphrase is on the common-password list; choose another")


def _derive(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    validate_passphrase(passphrase)
    _AESGCM, Scrypt, _InvalidTag = _crypto()
    if n < 2 ** 14 or (n & (n - 1)):
        raise SecurityError("the vault KDF cost is invalid (n must be a power of two >= 16384)")
    if n > MAX_KDF_N:
        raise SecurityError("the vault KDF cost is implausibly high; refusing to allocate it")
    try:
        kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
        return kdf.derive(passphrase.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SecurityError(f"could not derive the vault key: {exc}") from exc


def _header(n: int, r: int, p: int, salt: bytes, nonce_prefix: bytes) -> bytes:
    # Fixed-width fields, so a reader never has to guess.  The header is the AAD
    # for every chunk; changing a cost or a salt invalidates the whole file.
    return (MAGIC + struct.pack(">BBIII", VERSION, KDF_SCRYPT, n, r, p)
            + struct.pack(">H", len(salt)) + salt
            + struct.pack(">B", len(nonce_prefix)) + nonce_prefix
            + struct.pack(">I", CHUNK_SIZE))


def _parse_header(blob_prefix: bytes) -> dict[str, Any]:
    if not blob_prefix.startswith(MAGIC):
        raise SecurityError("not a PBRForge vault: the magic header is missing")
    pos = len(MAGIC)
    if len(blob_prefix) < pos + 14:
        raise SecurityError("truncated vault header")
    version, kdf_id, n, r, p = struct.unpack_from(">BBIII", blob_prefix, pos)
    pos += 14
    if len(blob_prefix) < pos + 2:
        raise SecurityError("truncated vault header")
    (salt_len,) = struct.unpack_from(">H", blob_prefix, pos)
    pos += 2
    if salt_len > len(blob_prefix) - pos:
        raise SecurityError("truncated vault salt")
    salt = blob_prefix[pos:pos + salt_len]
    pos += salt_len
    if len(blob_prefix) < pos + 1:
        raise SecurityError("truncated vault header")
    (nonce_len,) = struct.unpack_from(">B", blob_prefix, pos)
    pos += 1
    if nonce_len > len(blob_prefix) - pos:
        raise SecurityError("truncated vault nonce prefix")
    nonce = blob_prefix[pos:pos + nonce_len]
    pos += nonce_len
    if len(blob_prefix) < pos + 4:
        raise SecurityError("truncated vault header")
    (chunk_size,) = struct.unpack_from(">I", blob_prefix, pos)
    pos += 4
    if version != VERSION:
        raise SecurityError(f"vault format {version} is not supported by this build")
    if kdf_id != KDF_SCRYPT:
        raise SecurityError(f"unknown vault KDF {kdf_id}")
    if (n > MAX_KDF_N or n < 2 ** 14 or (n & (n - 1))
            or r < 1 or r > MAX_KDF_R or p < 1 or p > MAX_KDF_P
            or salt_len != SALT_LEN or nonce_len != NONCE_PREFIX_LEN):
        raise SecurityError("vault header has implausible parameters")
    if (n * r * p) > (1 << 20):
        # Bound the total work, not just n.  A header is attacker-controlled until
        # the first tag verifies; it must not turn into an unbounded allocation.
        raise SecurityError("vault KDF work is implausibly high")
    if chunk_size < 64 * 1024 or chunk_size > MAX_CHUNK_SIZE:
        raise SecurityError("vault chunk size is invalid")
    return {"version": version, "kdf": kdf_id, "n": n, "r": r, "p": p,
            "salt": salt, "nonce": nonce, "chunk_size": chunk_size,
            "header_len": pos, "header": blob_prefix[:pos]}


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + struct.pack(">I", counter)


def encrypt_file(source: Path, target: Path, passphrase: str, *,
                 n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P) -> dict[str, Any]:
    """Encrypt `source` into `target` with chunked AES-256-GCM.

    A 4-byte length precedes each ciphertext, so decryption can stream without
    loading the archive.  Each chunk is authenticated separately and the counter
    is in its nonce and AAD; truncation, reordering and bit-flips all fail.
    """
    AESGCM, _Scrypt, _InvalidTag = _crypto()
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise SecurityError(f"nothing to encrypt: {source} does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(SALT_LEN)
    nonce_prefix = secrets.token_bytes(NONCE_PREFIX_LEN)
    header = _header(n, r, p, salt, nonce_prefix)
    key = _derive(passphrase, salt, n, r, p)
    aead = AESGCM(key)
    tmp = target.with_name(target.name + ".part")
    total = 0
    counter = 0
    try:
        with source.open("rb") as src, tmp.open("wb") as out:
            out.write(header)
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                aad = header + struct.pack(">I", counter)
                sealed = aead.encrypt(_nonce(nonce_prefix, counter), chunk, aad)
                if len(sealed) > 0xFFFFFFFF:
                    raise SecurityError("one vault chunk grew beyond the format limit")
                out.write(struct.pack(">I", len(sealed)))
                out.write(sealed)
                total += len(chunk)
                counter += 1
                if counter > 0xFFFFFFFF:
                    raise SecurityError("vault is too large for this format")
            if total == 0:
                # An empty payload still needs a content record.  The end marker
                # is only accepted after at least one chunk, which is what makes
                # a file whose plaintext is exactly the marker unambiguous.
                sealed = aead.encrypt(_nonce(nonce_prefix, counter), b"",
                                      header + struct.pack(">I", counter))
                out.write(struct.pack(">I", len(sealed)))
                out.write(sealed)
                counter += 1
            # An explicit end marker catches a file truncated exactly on a chunk
            # boundary.  Without it, a reader would happily accept a shorter zip.
            # The marker is a normal authenticated record; decryption recognises
            # its plaintext and requires EOF immediately after it.
            marker = aead.encrypt(_nonce(nonce_prefix, counter),
                                  b"pbrforge-vault-end",
                                  header + struct.pack(">I", counter))
            out.write(struct.pack(">I", len(marker)))
            out.write(marker)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(target), "bytes": target.stat().st_size,
            "plain_bytes": total, "chunks": counter + 1,
            "sha256": hash_file(target), "kdf": {"n": n, "r": r, "p": p}}


def decrypt_file(source: Path, target: Path, passphrase: str) -> dict[str, Any]:
    """Decrypt a vault, verifying every chunk's authentication tag."""
    AESGCM, _Scrypt, InvalidTag = _crypto()
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise SecurityError(f"vault {source} does not exist")
    with source.open("rb") as handle:
        prefix = handle.read(64 * 1024)
    meta = _parse_header(prefix)
    key = _derive(passphrase, meta["salt"], meta["n"], meta["r"], meta["p"])
    aead = AESGCM(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    counter = 0
    total = 0
    try:
        with source.open("rb") as src, tmp.open("wb") as out:
            src.seek(meta["header_len"])
            header = meta["header"]
            while True:
                raw_len = src.read(4)
                if len(raw_len) != 4:
                    raise SecurityError("vault ended before its final authenticated chunk")
                (length,) = struct.unpack(">I", raw_len)
                if length == 0:
                    raise SecurityError("vault contains an unexpected empty chunk")
                sealed = src.read(length)
                if len(sealed) != length:
                    raise SecurityError("vault is truncated")
                aad = header + struct.pack(">I", counter)
                try:
                    plain = aead.decrypt(_nonce(meta["nonce"], counter), sealed, aad)
                except InvalidTag as exc:
                    raise SecurityError(
                        "the vault could not be authenticated.\n"
                        "  why      the passphrase is wrong, or the file has been "
                        "changed since it was written\n"
                        "  fix      check the passphrase first; do not trust a "
                        "vault whose bytes changed"
                    ) from exc
                if counter and plain == b"pbrforge-vault-end":
                    # End marker is only valid as the last record.  Check that no
                    # bytes follow, otherwise a spliced second archive is hidden.
                    trailing = src.read(1)
                    if trailing:
                        raise SecurityError("vault has data after its end marker")
                    break
                out.write(plain)
                total += len(plain)
                counter += 1
                if counter > 0xFFFFFFFF:
                    raise SecurityError("vault is too large for this format")
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(target), "bytes": total, "chunks": counter + 1}


# --------------------------------------------------------------------- archive
def _iter_files(root: Path, prefix: str) -> Iterable[tuple[Path, str]]:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        yield path, f"{prefix}/{rel}"


def create_backup(config_dir: Path, library_dir: Path, target: Path,
                  passphrase: str, *, include_tls: bool = True,
                  progress: Any = None) -> dict[str, Any]:
    """Zip config + library, then encrypt the zip.

    The temporary zip is created beside the destination and removed in a
    ``finally``.  It is never placed in the library, cache or a temp directory
    that outlives the call longer than necessary.
    """
    config_dir = Path(config_dir)
    library_dir = Path(library_dir)
    target = Path(target)
    if target.suffix.lower() != VAULT_EXT:
        target = target.with_suffix(VAULT_EXT)
    if target.exists():
        raise SecurityError(f"{target} already exists; choose a new filename")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = target.with_name(target.name + ".zip.part")
    files: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            excluded = {target.resolve(), tmp_zip.resolve()}
            for path, arc in _iter_files(config_dir, "config"):
                if path.resolve() in excluded:
                    continue
                if not include_tls and "tls/" in arc:
                    continue
                # Cache and runtime records are not backup material: they are
                # reproducible and can be large.
                if (arc.startswith("config/cache/") or arc.startswith("config/backups/")
                        or arc.endswith("update.json")):
                    continue
                archive.write(path, arc)
                files.append({"path": arc, "bytes": path.stat().st_size})
                if progress:
                    progress(len(files), path.name)
            for path, arc in _iter_files(library_dir, "library"):
                if path.resolve() in excluded:
                    continue
                archive.write(path, arc)
                files.append({"path": arc, "bytes": path.stat().st_size})
                if progress:
                    progress(len(files), path.name)
            manifest = {
                "app": "pbrforge",
                "format": "pbrforge-vault",
                "version": VERSION,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "config_dir": str(config_dir),
                "library_dir": str(library_dir),
                "files": files,
                "count": len(files),
            }
            archive.writestr("PBRFORGE_VAULT.json",
                             json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        info = encrypt_file(tmp_zip, target, passphrase)
        info.update({"files": len(files), "zip_bytes": tmp_zip.stat().st_size,
                     "vault": str(target)})
        return info
    finally:
        tmp_zip.unlink(missing_ok=True)


def inspect_vault(path: Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("rb") as handle:
        meta = _parse_header(handle.read(64 * 1024))
    return {"path": str(path), "bytes": path.stat().st_size,
            "version": meta["version"], "kdf": {"n": meta["n"], "r": meta["r"],
                                                "p": meta["p"]},
            "sha256": hash_file(path)}


def verify_vault(path: Path, passphrase: str) -> dict[str, Any]:
    """Authenticate the whole vault and validate the inner zip CRC.

    This is intentionally a full decrypt: a header check cannot tell a wrong
    passphrase from a good one, and claiming "valid" without reading the tags
    would be a lie.
    """
    with tempfile.TemporaryDirectory(prefix="pbrforge-vault-verify-") as tmp:
        out = Path(tmp) / "contents.zip"
        result = decrypt_file(path, out, passphrase)
        try:
            with zipfile.ZipFile(out) as archive:
                bad = archive.testzip()
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise SecurityError(
                "the vault authenticated but its payload is not a valid zip.\n"
                "  why      the file is internally inconsistent\n"
                "  fix      do not restore this copy; use another backup"
            ) from exc
        if bad:
            raise SecurityError(f"the vault payload is corrupt at {bad}")
        manifest = None
        if "PBRFORGE_VAULT.json" in names:
            try:
                manifest = json.loads(archive.read("PBRFORGE_VAULT.json"))
            except (KeyError, ValueError):
                manifest = None
        return {**result, "entries": len(names), "manifest": manifest,
                "valid": True}


def _safe_members(archive: zipfile.ZipFile, target: Path) -> list[zipfile.ZipInfo]:
    target = target.resolve()
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise SecurityError(f"vault contains an unsafe path: {info.filename!r}")
        if info.is_dir():
            continue
        # Reject symlinks encoded by Unix mode.  A zip symlink pointing at the
        # user's SSH key would otherwise be extracted as a link.
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise SecurityError(f"vault contains a symlink: {info.filename!r}")
        destination = (target / name).resolve()
        if not destination.is_relative_to(target):
            raise SecurityError(f"vault path escapes the destination: {info.filename!r}")
        members.append(info)
    return members


def restore_backup(path: Path, passphrase: str, output_dir: Path, *,
                   overwrite: bool = False) -> dict[str, Any]:
    """Decrypt and extract into a new folder.

    Never writes into the live config or library automatically.  The caller
    receives a folder with ``config/`` and ``library/`` and can inspect or move
    it.  This is slower than a "restore in place" button and much harder to
    regret.
    """
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise SecurityError(f"{output_dir} is not empty; pass --overwrite to use it")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pbrforge-vault-") as tmp:
        archive_path = Path(tmp) / "contents.zip"
        decrypt_file(path, archive_path, passphrase)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = _safe_members(archive, output_dir)
                for info in members:
                    archive.extract(info, output_dir)
        except zipfile.BadZipFile as exc:
            raise SecurityError("the vault payload is not a valid zip") from exc
    manifest_path = output_dir / "PBRFORGE_VAULT.json"
    manifest = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = None
    return {"path": str(output_dir), "files": len(list(output_dir.rglob("*"))),
            "manifest": manifest}


# ----------------------------------------------------------------- signatures
_SIG_MAGIC = "pbrforge-ed25519-v1"


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey, Ed25519PublicKey)
        from cryptography.hazmat.primitives import serialization
    except Exception as exc:  # noqa: BLE001
        raise SecurityError("Ed25519 support needs the 'cryptography' package") from exc
    return Ed25519PrivateKey, Ed25519PublicKey, serialization


def generate_signing_key(private_path: Path, public_path: Path) -> dict[str, str]:
    Ed25519PrivateKey, _Public, serialization = _ed25519()
    private_path = Path(private_path)
    public_path = Path(public_path)
    if private_path.exists() or public_path.exists():
        raise SecurityError("refusing to overwrite an existing signing key")
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(private_bytes)
    public_path.write_bytes(public_bytes)
    try:
        os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return {"private": str(private_path), "public": str(public_path)}


def sign_file(path: Path, private_key_path: Path) -> dict[str, Any]:
    """Return a detached signature document for `path`."""
    _Private, _Public, serialization = _ed25519()
    path = Path(path)
    if not path.is_file():
        raise SecurityError(f"cannot sign missing file {path}")
    try:
        key = serialization.load_pem_private_key(
            Path(private_key_path).read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001
        raise SecurityError(f"could not load the signing key: {exc}") from exc
    signature = key.sign(path.read_bytes())
    payload = {
        "format": _SIG_MAGIC,
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": hash_file(path),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }
    return payload


def verify_file_signature(path: Path, signature: dict[str, Any] | str | Path,
                          public_key_path: Path | None = None, *,
                          public_key_pem: bytes | str | None = None) -> bool:
    _Private, Ed25519PublicKey, serialization = _ed25519()
    path = Path(path)
    if isinstance(signature, (str, Path)):
        raw = Path(signature).read_text(encoding="utf-8")
        try:
            signature = json.loads(raw)
        except ValueError as exc:
            raise SecurityError("the signature file is not valid JSON") from exc
    if not isinstance(signature, dict) or signature.get("format") != _SIG_MAGIC:
        raise SecurityError("the signature file has an unknown format")
    try:
        if public_key_pem is not None:
            raw_key = (public_key_pem.encode("utf-8") if isinstance(public_key_pem, str)
                       else public_key_pem)
            key = serialization.load_pem_public_key(raw_key)
        elif public_key_path is not None:
            key = serialization.load_pem_public_key(Path(public_key_path).read_bytes())
        else:
            raise SecurityError("no release public key was supplied")
    except SecurityError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SecurityError(f"could not load the public key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise SecurityError("the configured public key is not Ed25519")
    if signature.get("sha256") and signature["sha256"] != hash_file(path):
        return False
    try:
        raw = base64.b64decode(str(signature.get("signature_b64") or ""), validate=True)
    except Exception:  # noqa: BLE001
        return False
    try:
        key.verify(raw, path.read_bytes())
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------- audit
_HSTS = "max-age=31536000; includeSubDomains"


def audit(config: Any) -> dict[str, Any]:
    """A small, truthful security posture report for CLI and UI."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})

    secret_ok = False
    detail = "no secret file yet"
    try:
        secret = Path(config.secret_file)
        if secret.is_file():
            mode = stat.S_IMODE(secret.stat().st_mode)
            secret_ok = (mode & 0o077) == 0 or os.name == "nt"
            detail = f"{secret} mode={oct(mode)}"
    except OSError as exc:
        detail = str(exc)
    add("session secret", secret_ok, detail,
        "run 'pbrforge code --rotate' to recreate it")

    add("pairing auth", bool(config.auth),
        "enabled" if config.auth else "disabled",
        "start without --no-auth and set auth = true")
    add("TLS", bool(config.tls),
        "enabled" if config.tls else "plain HTTP on the LAN",
        "run 'pbrforge tls init' then 'pbrforge serve --tls'")
    crypto_ok, crypto_detail = backend_available()
    add("encryption backend", crypto_ok, crypto_detail,
        "pip install cryptography, then rebuild the installer")
    cert_ok = bool(getattr(config, "cert_file", None)) and Path(config.cert_file).is_file()
    add("certificate", cert_ok or not config.tls,
        "present" if cert_ok else "not generated",
        "pbrforge tls init")
    checks.append({"name": "vault", "ok": crypto_ok,
                   "detail": "AES-256-GCM + scrypt " + (f"n={DEFAULT_N}" if crypto_ok else "unavailable"),
                   "fix": ""})
    checks.append({"name": "headers", "ok": True,
                   "detail": "CSP, HSTS when TLS is on, nosniff, frame and referrer policy",
                   "fix": ""})
    return {"ready": all(c["ok"] for c in checks), "checks": checks,
            "vault_extension": VAULT_EXT, "hsts": _HSTS,
            "passphrase_min": 12}


# ------------------------------------------------------------------- helpers
def hash_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            data = handle.read(chunk)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def secure_compare(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def default_backup_name(prefix: str = "pbrforge") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}{VAULT_EXT}"


def describe_backup(info: dict[str, Any]) -> str:
    kdf = info.get("kdf") or {}
    return (f"{info.get('path', '?')}\n"
            f"  bytes    {info.get('bytes', 0):,}\n"
            f"  files    {info.get('files', '?')}\n"
            f"  sha256   {info.get('sha256', '?')}\n"
            f"  kdf      scrypt n={kdf.get('n', '?')} r={kdf.get('r', '?')} "
            f"p={kdf.get('p', '?')}\n"
            f"  cipher   AES-256-GCM, authenticated chunks")
