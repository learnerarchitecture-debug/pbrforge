"""Self-signed TLS for the LAN server.

This is not a nicety. It is load-bearing for the multi-PC setup, because of the
secure-context rule:

    WebGPU, the async clipboard API and service workers are only available on a
    SECURE ORIGIN. `http://localhost` counts as secure. `http://192.168.1.100`
    does NOT. There is no flag to override it in a browser.

So a LAN client served over plain HTTP silently loses WebGPU and gets the WebGL2
fallback. That is survivable -- the preview still works -- but it is a real
capability loss, and the fix is a certificate the client trusts.

What we generate:
    ca.key      the local CA private key      (never leaves the server machine)
    ca.pem      the local CA certificate      (installed on each client, once)
    server.key  leaf key
    server.pem  leaf certificate + the CA     (what the server serves)

The CA being local and per-install is deliberate: it means nothing is trusted
beyond your own machines, and rotating it is a two-command operation.

Generation uses whichever backend is available, in this order:

    1. `cryptography`    -- in-process, no external tool, the fast path
    2. the `openssl` CLI -- found on PATH or in the well-known Windows install
                            locations (Git for Windows ships one)

The second backend is not a nicety either. A PyInstaller build is stdlib-only by
design, and although the spec bundles `cryptography` when the *build machine*
happens to have it, a build made without it must still be able to generate a
certificate -- otherwise TLS would be impossible on exactly the deployment the
feature exists for, and every LAN client would silently lose WebGPU. openssl is
present on virtually every machine that already has Git, and Git for Windows
ships it at usr\\bin\\openssl.exe whether or not it is on PATH.

If neither backend is available, HTTP still works and `doctor` says why.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .netutil import interfaces, local_hostname

CA_DAYS = 3650
LEAF_DAYS = 825          # the current browser maximum for a publicly-trusted cert;
                         # private CAs are not bound by it, but staying inside the
                         # limit avoids surprises if a client ever enforces it.
KEY_SIZE = 2048


class TlsError(RuntimeError):
    pass


def _crypto_available() -> tuple[bool, str]:
    """Is the in-process backend importable? (False, reason) if not."""
    try:
        import cryptography
    except ModuleNotFoundError:
        return False, "cryptography is not installed"
    except Exception as exc:      # noqa: BLE001
        return False, f"cryptography is installed but broken: {exc}"
    try:
        return True, f"cryptography {cryptography.__version__}"
    except Exception as exc:      # noqa: BLE001
        return False, f"cryptography present but unusable: {exc}"


def backend() -> tuple[bool, str]:
    """(available, description). Called by `doctor` and before any generation.

    Reports on *generation*, which is the only part of the TLS feature that needs
    a backend at all. Serving an existing certificate needs nothing but the
    standard library, so a machine with neither backend can still run HTTPS if
    the certificate came from somewhere else.
    """
    ok, why = _crypto_available()
    if ok:
        return True, why
    exe = openssl_path()
    if exe:
        ver = _openssl_version(exe)
        return True, f"openssl {ver} at {exe}" if ver else f"openssl at {exe}"
    return False, why


def install_hint() -> str:
    """One line, for embedding in an error message."""
    return "pip install cryptography, or install Git for Windows (which ships openssl)"


def sans_for_server() -> tuple[list[str], list[str]]:
    """(dns_names, ip_addresses) that the certificate must cover.

    Includes every current LAN address, not just the primary one. A laptop that
    moves between Wi-Fi and a dock gets a different IP, and a certificate that
    only covers one of them produces a browser warning the moment the network
    changes -- which is the kind of thing that gets a security feature switched off.
    """
    dns = {"localhost", local_hostname(), f"{local_hostname()}.local"}
    ips = {"127.0.0.1", "::1"}
    for iface in interfaces():
        ips.add(iface.ip)
    return sorted(dns), sorted(ips)


def _load_crypto() -> tuple:
    ok, why = _crypto_available()
    if not ok:
        raise TlsError(
            f"cannot generate certificates: {why}\n"
            f"  fix      {install_hint()}\n"
            f"  note     HTTPS is optional. Without it LAN clients use the WebGL2 "
            f"preview instead of WebGPU; everything else works over HTTP."
        )
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    return x509, hashes, serialization, rsa


# ---------------------------------------------------------------------------
# backend 2: the openssl CLI
# ---------------------------------------------------------------------------

def openssl_path() -> str | None:
    """Locate an openssl executable.

    PATH first, then the well-known Windows locations. Git for Windows is the one
    that matters in practice: it ships openssl 3.x at usr\\bin\\openssl.exe, and the
    machine almost certainly already has Git. That single fact is what lets the
    packaged, dependency-free build offer HTTPS at all.
    """
    found = shutil.which("openssl")
    if found:
        return found
    if sys.platform != "win32":
        return None

    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    relatives = (
        r"Git\usr\bin\openssl.exe",
        r"Git\mingw64\bin\openssl.exe",
        r"OpenSSL-Win64\bin\openssl.exe",
        r"OpenSSL-Win32\bin\openssl.exe",
        r"OpenSSL\bin\openssl.exe",
        r"Programs\Git\usr\bin\openssl.exe",        # per-user Git install
        r"Programs\OpenSSL-Win64\bin\openssl.exe",
    )
    for root in roots:
        if not root:
            continue
        for rel in relatives:
            cand = Path(root) / rel
            if cand.exists():
                return str(cand)
    return None


def _openssl_version(exe: str) -> str:
    """'3.5.7' out of 'OpenSSL 3.5.7 9 Jun 2026 (...)'. '' if it cannot be read."""
    try:
        proc = subprocess.run([exe, "version"], capture_output=True, text=True,
                              timeout=15)
    except Exception:      # noqa: BLE001
        return ""
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not text:
        return ""
    parts = text.splitlines()[0].split()
    return parts[1] if len(parts) > 1 and parts[0] == "OpenSSL" else parts[0]


def _version_tuple(text: str) -> tuple[int, ...]:
    out = []
    for part in text.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _openssl(exe: str, args: list[str]) -> str:
    """Run openssl, raising TlsError carrying its own stderr when it fails."""
    env = dict(os.environ)
    # Git for Windows ships an MSYS build, and MSYS rewrites arguments that look
    # like Unix paths. `-subj /O=PBRForge/CN=...` is exactly such an argument, and
    # a rewritten one produces a certificate whose subject is a Windows path. Two
    # different MSYS generations use two different variables, so set both.
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    env["MSYS_NO_PATHCONV"] = "1"
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True,
                              timeout=180, env=env)
    except OSError as exc:
        raise TlsError(f"could not run {exe}: {exc}") from exc
    if proc.returncode != 0:
        err = ((proc.stderr or "") + (proc.stdout or "")).strip()
        raise TlsError(
            f"openssl exited {proc.returncode} during '{args[0]} {args[1]}'\n"
            f"  exe      {exe}\n"
            f"  said     {err[:600] or '(nothing)'}"
        )
    return proc.stdout or ""


def _posix(path: Path) -> str:
    """Forward slashes: accepted by Windows everywhere, and the MSYS build of
    openssl is noticeably happier with them than with backslashes."""
    return str(path).replace("\\", "/")


def _init_openssl(cfg: Config, dns: list[str], ips: list[str], exe: str) -> list[Path]:
    """Generate the same four files as the cryptography backend, using openssl."""
    ver = _openssl_version(exe)
    if ver and _version_tuple(ver) < (1, 1, 1):
        raise TlsError(
            f"openssl {ver} is too old to use: {exe}\n"
            f"  fix      pip install cryptography (that backend needs no external tool)\n"
            f"  note     generation passes extensions with -addext, added in openssl 1.1.1"
        )

    tls_dir = cfg.tls_dir
    tls_dir.mkdir(parents=True, exist_ok=True)
    ca_key = tls_dir / "ca.key"

    # ---- CA ----
    _openssl(exe, [
        "req", "-x509", "-newkey", f"rsa:{KEY_SIZE}", "-sha256", "-nodes",
        "-days", str(CA_DAYS),
        "-keyout", _posix(ca_key),
        "-out", _posix(cfg.ca_file),
        "-subj", f"/O=PBRForge/CN=PBRForge Local CA ({local_hostname()})",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    ])

    # ---- leaf ----
    # A CSR and a separate signing step, because `req -x509` would self-sign and a
    # self-signed leaf is not what a client that trusts the CA expects to see.
    csr = tls_dir / "server.csr"
    leaf_only = tls_dir / "server.leaf.pem"
    ext = tls_dir / "leaf.ext"
    _openssl(exe, [
        "req", "-newkey", f"rsa:{KEY_SIZE}", "-sha256", "-nodes",
        "-keyout", _posix(cfg.key_file),
        "-out", _posix(csr),
        "-subj", f"/O=PBRForge/CN={local_hostname()}",
    ])
    # The SAN list is the whole point of the exercise: an IP address is never
    # covered by the common name, so a certificate without these entries fails by
    # name for every LAN client no matter how carefully it was trusted.
    san = ",".join([f"DNS:{d}" for d in dns] + [f"IP:{ip}" for ip in ips])
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName={san}\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="utf-8",
    )
    _openssl(exe, [
        "x509", "-req", "-in", _posix(csr),
        "-CA", _posix(cfg.ca_file), "-CAkey", _posix(ca_key), "-CAcreateserial",
        "-out", _posix(leaf_only),
        "-days", str(LEAF_DAYS), "-sha256",
        "-extfile", _posix(ext),
    ])

    # Server cert = leaf + CA, byte-identical in layout to the other backend's, so
    # nothing downstream has to know which one ran.
    cfg.cert_file.write_bytes(leaf_only.read_bytes() + cfg.ca_file.read_bytes())

    for junk in (csr, leaf_only, ext, tls_dir / "ca.srl"):
        try:
            junk.unlink()
        except OSError:
            pass

    for secret in (ca_key, cfg.key_file):
        try:
            os.chmod(secret, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    return [cfg.ca_file, ca_key, cfg.cert_file, cfg.key_file]


def init(cfg: Config, *, force: bool = False, extra_hosts: list[str] | None = None) -> list[Path]:
    """Generate the CA and the leaf certificate. Idempotent unless `force`.

    Backend-agnostic on purpose: the caller does not need to know whether
    `cryptography` or the openssl CLI did the work, only that the four files now
    exist and are arranged the same way.
    """
    cfg.tls_dir.mkdir(parents=True, exist_ok=True)
    if cfg.ca_file.exists() and cfg.cert_file.exists() and not force:
        return []

    dns, ips = sans_for_server()
    for extra in (extra_hosts or []):
        try:
            ipaddress.ip_address(extra)
            ips.append(extra)
        except ValueError:
            dns.append(extra)
    dns = sorted(set(dns))
    ips = sorted(set(ips))

    have_crypto, why = _crypto_available()
    if have_crypto:
        return _init_cryptography(cfg, dns, ips)

    exe = openssl_path()
    if exe:
        return _init_openssl(cfg, dns, ips, exe)

    raise TlsError(
        f"cannot generate certificates: {why}, and no openssl was found\n"
        f"  fix      pip install cryptography\n"
        f"  or       install Git for Windows (which ships openssl) or OpenSSL itself\n"
        f"  note     HTTPS is optional. Without it LAN clients use the WebGL2 preview\n"
        f"           instead of WebGPU; everything else works over plain HTTP."
    )


def _init_cryptography(cfg: Config, dns: list[str], ips: list[str]) -> list[Path]:
    x509, hashes, serialization, rsa = _load_crypto()
    from cryptography.hazmat.primitives import serialization as ser

    # Each backend creates its own output directory rather than trusting the
    # caller to have done it. `init()` does call mkdir, but a backend that cannot
    # stand alone fails with FileNotFoundError the moment anything calls it
    # directly -- which is exactly what the self-test does.
    cfg.tls_dir.mkdir(parents=True, exist_ok=True)

    made: list[Path] = []
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    # ---- CA ----
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    ca_name = x509.Name([
        x509.NameAttribute(x509.NameOID.COMMON_NAME, f"PBRForge Local CA ({local_hostname()})"),
        x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "PBRForge"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))     # tolerate clock skew
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    # ---- leaf ----
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    subject = x509.Name([
        x509.NameAttribute(x509.NameOID.COMMON_NAME, local_hostname()),
        x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "PBRForge"),
    ])
    alt = [x509.DNSName(name) for name in dns] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    def write_pem(path: Path, data: bytes, secret: bool = False) -> None:
        path.write_bytes(data)
        if secret:
            try:
                import os
                import stat
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        made.append(path)

    write_pem(cfg.ca_file, ca_cert.public_bytes(ser.Encoding.PEM))
    write_pem(cfg.tls_dir / "ca.key",
              ca_key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                                   ser.NoEncryption()), secret=True)
    # Server cert = leaf + CA, so clients that need the intermediate chain get it.
    write_pem(cfg.cert_file,
              leaf_cert.public_bytes(ser.Encoding.PEM) + ca_cert.public_bytes(ser.Encoding.PEM))
    write_pem(cfg.key_file,
              leaf_key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                                     ser.NoEncryption()), secret=True)
    return made


@dataclass
class Status:
    present: bool
    cert_path: Path
    not_after: dt.datetime | None = None
    days_left: int = 0
    names: list[str] = None          # type: ignore[assignment]
    missing_current_ips: list[str] = None      # type: ignore[assignment]
    self_signed: bool = True

    def __post_init__(self) -> None:
        self.names = self.names or []
        self.missing_current_ips = self.missing_current_ips or []

    @property
    def healthy(self) -> bool:
        return self.present and self.days_left > 0 and not self.missing_current_ips


def status(cfg: Config) -> Status:
    """Inspect the existing certificate, including whether it still covers this
    machine's current addresses -- the failure mode nobody predicts."""
    if not cfg.cert_file.exists():
        return Status(present=False, cert_path=cfg.cert_file)
    try:
        from cryptography import x509
    except ModuleNotFoundError:
        return Status(present=True, cert_path=cfg.cert_file)

    pem = cfg.cert_file.read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    try:
        expiry = cert.not_valid_after_utc
    except AttributeError:            # cryptography < 42
        expiry = cert.not_valid_after.replace(tzinfo=dt.timezone.utc)   # type: ignore[attr-defined]

    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        # get_values_for_type returns two different kinds of thing, which is not
        # obvious and cost an AttributeError: 'str' object has no attribute 'value'
        # the first time this ran. DNSName yields plain strings; IPAddress yields
        # ipaddress objects. str() is correct for both, and .value is correct for
        # neither. Anything that compares against the certificate must go through
        # this list, so it is deliberately a flat bag of names and addresses.
        names = [str(n) for n in san.get_values_for_type(x509.DNSName)]
        names += [str(n) for n in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass

    _, current_ips = sans_for_server()
    missing = [ip for ip in current_ips if ip not in names]

    return Status(
        present=True,
        cert_path=cfg.cert_file,
        not_after=expiry,
        days_left=(expiry - dt.datetime.now(dt.timezone.utc)).days,
        names=names,
        missing_current_ips=missing,
    )


TRUST_SCRIPT = """# trust-ca.ps1 -- run this ONCE on each client PC to trust this PBRForge server.
#
# Without this, the browser shows a certificate warning, and a warning you click
# through every time is a security control you have effectively disabled.
#
#   -Machine   install for all users (needs an Administrator prompt)
#   (default)  install for the current user only (no admin needed)
#
# Generated by PBRForge on {host} at {when}.

param([switch]$Machine)

$ErrorActionPreference = 'Stop'
$certPath = Join-Path $PSScriptRoot 'ca.pem'

if (-not (Test-Path $certPath)) {{
    Write-Error "ca.pem not found next to this script. Copy both files from the server."
}}
$cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($certPath)

$store = if ($Machine) {{ 'LocalMachine' }} else {{ 'CurrentUser' }}
$location = "Cert:\\$store\\Root"

$existing = Get-ChildItem $location | Where-Object {{ $_.Thumbprint -eq $cert.Thumbprint }}
if ($existing) {{
    Write-Host "Already trusted ($($cert.Thumbprint))." -ForegroundColor Green
    exit 0
}}

Import-Certificate -FilePath $certPath -CertStoreLocation $location | Out-Null
Write-Host "Installed the PBRForge CA into $location." -ForegroundColor Green
Write-Host "Restart your browser, then open {url}"
"""


def write_trust_script(cfg: Config) -> Path:
    """Emit a client-side trust script next to a copy of the CA.

    Shipped as a folder you copy to a USB stick or a share, because the client
    machine is by definition not this machine.
    """
    from . import __version__
    from .netutil import primary_ip

    out_dir = cfg.tls_dir / "client-trust"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not cfg.ca_file.exists():
        raise TlsError(
            "no CA to distribute -- the certificate has not been generated yet\n"
            "  fix      run 'pbrforge tls init' first"
        )
    (out_dir / "ca.pem").write_bytes(cfg.ca_file.read_bytes())

    # The trust folder exists to make HTTPS work, so the instructions must name the
    # https URL. cfg.serve_port looks like the right thing to use here and is not:
    # it returns the *plain* port until tls is switched on in the config, and this
    # folder is generated before that switch. The old code would therefore have
    # sent every client to the insecure address it is trying to get them off.
    ip = primary_ip()
    https_url = f"https://{ip}:{cfg.https_port}"
    http_url = f"http://{ip}:{cfg.port}"

    script = TRUST_SCRIPT.format(
        host=local_hostname(),
        when=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        url=https_url,
    )
    target = out_dir / "trust-ca.ps1"
    target.write_text(script, encoding="utf-8-sig")     # BOM: PowerShell 5.1 needs it

    # Written as a real triple-quoted block, not a run of concatenated one-liners.
    # The previous version looked like a concatenation but sat inside an f-string,
    # so every line kept its source indentation and its leading quote character --
    # the READ-ME would have opened with a stray " and stayed indented eight spaces
    # throughout. It also referenced an undefined {ipport} and raised NameError.
    (out_dir / "READ-ME.txt").write_text(
        f"""PBRForge client trust
=====================

Copy this whole folder to the other PC, then on THAT machine:

  1. Double-click trust-ca.ps1
     (or right-click it and choose 'Run with PowerShell')
  2. Restart the browser -- an already-open tab keeps its old certificate state
  3. Open {https_url}

Why this is needed
------------------
WebGPU, the clipboard API and service workers only work on a secure origin:
https, or localhost. Over plain http on a LAN address the browser refuses them,
and the 3D preview falls back to WebGL2, which is slower and plainer. Trusting
this CA makes {ip} a secure origin, and you get the full UI.

Untrusted instead? You lose WebGPU and nothing else. The server also listens on
{http_url}, and while TLS is on that address redirects to the https one -- so a
client that skips this step still reaches the tool, just in WebGL2.

Where the certificate comes from
--------------------------------
ca.pem in this folder, generated on the server by 'pbrforge tls init'. It is a
local CA: it signs this server's certificate and nothing else. It is not a public
certificate authority and cannot vouch for any other site.

To undo: open certmgr.msc, find 'PBRForge Local CA' under Trusted Root
Certification Authorities, and delete it.
""",
        encoding="utf-8",
    )
    return target


def ssl_context(cfg: Config):
    import ssl as _ssl
    if not cfg.cert_file.exists() or not cfg.key_file.exists():
        raise TlsError(
            f"certificate missing\n  expected {cfg.cert_file}\n"
            f"  fix      run 'pbrforge tls init'"
        )
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cfg.cert_file), keyfile=str(cfg.key_file))
    return ctx
