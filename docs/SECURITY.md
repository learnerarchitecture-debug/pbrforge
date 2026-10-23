# PBRForge security

PBRForge is a **personal local tool**. The security model is proportioned to that: it hardens the
server against accidental LAN exposure, it encrypts the vault at rest, and it verifies release
updates. It is not a multi-tenant service, not a password manager and not a substitute for a
hardened workstation.

This document states what is implemented, how it fails, and what it does **not** promise.

---

## 1. Threat model

### In scope

| Threat | Control |
|---|---|
| Another device on the LAN reaching the studio | Pairing code + HMAC-signed session cookie; optional TLS |
| A malicious web page in your browser writing to the local API | Session-bound CSRF token; same-origin and Host checks; `SameSite=Strict` |
| DNS rebinding from a hostile page | Host allowlist (`localhost`, loopback, the configured bind addresses) |
| A stolen disk, laptop or cloud-synced backup folder | AES-256-GCM `.pbrvault` encryption with scrypt |
| A tampered or forged release installer | SHA-256 plus mandatory Ed25519 signature for v2.0+ auto-install |
| A runaway browser loop filling the disk | Per-client read/write rate limits, upload caps, path containment |
| A crafted filename escaping the library | Slug/path containment checks before any write |

### Out of scope

- **Malware, ransomware or a keylogger on the machine.** Encryption at rest does not protect a
  running session, and PBRForge cannot detect a compromised host.
- **A stolen unlocked session.** A paired cookie is a bearer credential. Revoke every client at once
  with `pbrforge code --rotate`.
- **Plugin code.** Plugins are local Python with your user rights; they are path-contained and
  manifest-checked but **not sandboxed**. Run only plugins you trust.
- **Physical access.** There is no TPM binding, no secure enclave and no disk-encryption integration.
- **Denial of service by a peer who already has the pairing code.** The rate limiter protects against
  accidents and casual misuse, not a determined LAN peer.

---

## 2. Encrypted vault

### What it protects

`pbrforge vault` produces a single `.pbrvault` file containing a ZIP of the configured library and
configuration (the same content as a backup). It is designed for offline copies, USB sticks and
cloud-synced folders where you want the bytes to be meaningless without the passphrase.

### Cryptographic design

| Property | Value |
|---|---|
| Cipher | AES-256-GCM via the `cryptography` package |
| Key derivation | scrypt, `n=32768`, `r=8`, `p=1`, random 16-byte salt (values are stored in the authenticated header) |
| Chunking | 1 MiB authenticated chunks; each chunk has its own nonce/counter and is length-framed |
| Header | Magic `PBRFORGE-VAULT\0`, version, KDF parameters, salt, nonce prefix and chunk size — all bound as additional authenticated data |
| End marker | An authenticated end record distinct from file content; an empty payload still contains an authenticated zero-length content chunk |
| Tamper response | Any modification, truncation, reordering or wrong passphrase fails with a named error. There is no cipher fallback and no partial plaintext |

### Passphrase rules

- Minimum 12 characters; a short list of common passwords is rejected.
- The passphrase is **never stored**. There is no recovery key, no hint, no escrow and no reset.
- **If you lose the passphrase, the vault is unrecoverable.** That is the honest consequence of not
  keeping a second copy somewhere.
- The UI suggests a generated multi-word passphrase; a four-word phrase is a reasonable target.

### Restoring

```powershell
python -m pbrforge vault restore .\backup.pbrvault --out .\restored
```

Restore always writes into a **new** output directory and refuses to merge into a non-empty folder
unless `--overwrite` is explicit. After restoring, keep the `.pbrvault` as the archive and work from
the extracted copy.

---

## 3. Release integrity and updates

- Release installers are accompanied by `SHA256SUMS.txt` and a detached `<asset>.sig` Ed25519
  signature.
- The public key is embedded in `pbrforge/update.py` and mirrored in
  `deploy/release-signing-public.pem`; the private key is never part of the repository.
- `pbrforge update` always checks the SHA-256 and the byte size first.
- For versions **2.0.0 and later**, auto-install requires a valid signature. An unsigned or
  mismatched release is refused with a named reason and a manual-download fix.
- For the historical 0.2.0 release — which predates signing — the update is reported but the
  auto-install path follows the same signature rules.

The installer is **not Authenticode-signed**. Windows SmartScreen may therefore show an unknown
publisher warning on first run; that is expected and is separate from the update signature. The
signature protects the updater's chain of custody, not SmartScreen reputation.

---

## 4. Server controls

### Pairing and sessions

- One pairing code, derived from the server secret and displayed only in the server console.
- Five wrong attempts lock the client for 30 seconds.
- Sessions are stateless: `<expiry>.<HMAC-SHA256 trunced>`; the expiry is 90 days.
- Cookie flags: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age=90 days`, plus `Secure` when TLS
  is enabled.
- `pbrforge code --rotate` generates a new secret and thereby revokes every paired device at once.

### CSRF

- `GET /api/session` returns a session-bound, double-submit CSRF token.
- Every state-changing API request must send the token; the server rejects a missing or mismatched
  token with `403`.
- The token is bound to the session cookie, so it cannot be replayed from another session.

### Origin and Host checks

- Requests with a real `Origin` header must match the scheme and host of the server.
- A browser that reports `Origin: null` — some privacy-hardened Chromium shells and proxied form
  posts do this even for same-origin navigation — is accepted only when `Sec-Fetch-Site` is not
  `cross-site`. `Sec-Fetch-Site` is a forbidden header and cannot be forged by page JavaScript.
- A missing `Origin` is allowed because the CSRF token is the primary write control and the pairing
  flow has a code and rate limiter behind it.
- The `Host` header must match the configured bind address, `localhost`, `127.0.0.1` or the machine
  name. Anything else is rejected: this blocks DNS-rebinding attempts.

### Rate limits

- Reads: 120 requests per 60 seconds per client.
- Writes: 18 requests per 60 seconds per client.
- The server is single-worker by design; the write bucket exists to keep one browser from making the
  pipeline useless for everyone else.

### Headers and content security policy

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self';
  frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

**Trade-off, stated plainly:** the single-file UI uses inline `<script>` and `<style>`, so
`'unsafe-inline'` is required for scripts and styles. Everything else is locked down: no external
origins, no frames, no objects, no base URI. If the UI is ever split into external files, the inline
allowance can be removed; until then, it is honest to say the CSP prevents foreign code, not injected
inline code.

### TLS

- `pbrforge tls init` creates a local CA and leaf certificate; `pbrforge tls trust` reports how to
  install the CA on a client.
- TLS is optional. Plain HTTP is supported but loses browser secure-context APIs for LAN clients;
  the UI reports the reduced capability rather than pretending it is unavailable for a mysterious
  reason.
- TLS certificates are generated locally. Nothing is sent to a certificate authority, and no
  external service sees your hostname or IP.

---

## 5. Uploads, paths and data handling

- One fixed MIME/extension allowlist for pipeline input; the browser converts unsupported formats
  to PNG rather than the server guessing.
- Upload size cap from `server.max_upload_mb` (default 512 MB).
- All library and stage writes resolve through path-containment checks; a slug cannot escape the
  library root.
- Staging folders are per-job and promoted only after the pipeline completes.
- The only outbound network call is the optional GitHub release check. There is no telemetry, no
  analytics, no account and no cloud API.

---

## 6. Security audit command

```powershell
python -m pbrforge security
python -m pbrforge security --json
```

The audit reports TLS state, pairing state, secret-file permissions where the platform exposes them,
the vault key configuration, update-signature requirements and any obvious misconfiguration. It
reports risks; it does not silently change your configuration.

---

## 7. Reporting a problem

PBRForge is a personal project released for personal use. If you find a security problem, open an
issue on the repository with a minimal reproduction. Do not include real vaults, passphrases or
personal file contents.
