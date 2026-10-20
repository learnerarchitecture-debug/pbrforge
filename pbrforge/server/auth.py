"""Pairing auth for a LAN server.

Threat model, stated plainly so the design can be judged against it:

  IN SCOPE   - A neighbour on the same Wi-Fi guessing the URL and poking around.
             - A guest on the network browsing your texture library out of
               curiosity, or uploading junk into the cache.
             - Port scanners and opportunistic crawlers hitting every host.
  OUT OF SCOPE - Anyone who already has the secret file. It lives in the user's
               config directory with user-only permissions.
             - Anyone with local code execution on the server machine.
             - DoS. A LAN peer can always saturate your uplink if it wants to.

So: no password database, no accounts, no roles. One pairing code that a person
types once per client PC, then a signed cookie. That is proportionate for a
personal tool and it removes the two failure modes that actually happen --
accidental exposure and credential fatigue.

Sessions are stateless: the cookie is `<expiry>.<hmac>`. Rotating the secret
(`pbrforge code --rotate`) therefore revokes every paired client at once, with no
session table to keep consistent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_DAYS = 90
COOKIE_NAME = "pbr_session"
PAIR_PATH = "/pair"

# Excludes I, L, O, U and 0/1: a pairing code gets read off a screen and typed on
# another machine, and those characters are where that goes wrong.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


class AuthError(RuntimeError):
    pass


@dataclass
class PairAttempt:
    count: int = 0
    locked_until: float = 0.0

    @property
    def locked(self) -> bool:
        return time.time() < self.locked_until


class Auth:
    """Owns the server secret, the pairing code and session signing."""

    MAX_FAILURES = 5
    LOCKOUT_SECONDS = 30.0

    def __init__(self, secret_file: Path, *, enabled: bool = True) -> None:
        self.secret_file = Path(secret_file)
        self.enabled = enabled
        self._attempts: dict[str, PairAttempt] = {}
        self._secret = self._load_or_create_secret()

    # ---------------------------------------------------------------- secret
    def _load_or_create_secret(self) -> bytes:
        if self.secret_file.exists():
            raw = self.secret_file.read_bytes().strip()
            if len(raw) >= 32:
                return raw
            # Present but wrong: regenerate rather than silently accepting a weak
            # secret, and say so, because silently rotating it logs everyone out.
            print(f"warning: {self.secret_file} is too short; regenerating "
                  f"(all paired clients will need to pair again)")
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret = base64.urlsafe_b64encode(os.urandom(48)).strip(b"=")
        self.secret_file.write_bytes(secret)
        try:
            os.chmod(self.secret_file, stat.S_IRUSR | stat.S_IWUSR)   # best effort on Windows
        except OSError:
            pass
        return secret

    @property
    def secret(self) -> bytes:
        return self._secret

    def rotate(self) -> None:
        self.secret_file.unlink(missing_ok=True)
        self._secret = self._load_or_create_secret()

    # -------------------------------------------------------------- pairing
    def pairing_code(self) -> str:
        """Stable 8-character code derived from the secret.

        Derived, not stored, so it is the same on every restart until you rotate.
        That matters: a code that changes on reboot means re-pairing every client
        after every power cut, which is how people end up disabling auth entirely.
        """
        digest = hmac.new(self._secret, b"pbrforge/pairing", hashlib.sha256).digest()
        chars = []
        for byte in digest:
            chars.append(_ALPHABET[byte % len(_ALPHABET)])
            if len(chars) == 8:
                break
        return "".join(chars[:4]) + "-" + "".join(chars[4:8])

    def check_pairing_code(self, supplied: str) -> bool:
        cleaned = "".join(c for c in (supplied or "").upper() if c in _ALPHABET)
        expected = self.pairing_code().replace("-", "")
        return hmac.compare_digest(cleaned, expected)

    # -------------------------------------------------------------- attempts
    def note_failure(self, client: str) -> PairAttempt:
        a = self._attempts.setdefault(client, PairAttempt())
        a.count += 1
        if a.count >= self.MAX_FAILURES:
            a.locked_until = time.time() + self.LOCKOUT_SECONDS
            a.count = 0
        return a

    def note_success(self, client: str) -> None:
        self._attempts.pop(client, None)

    def attempt(self, client: str) -> PairAttempt:
        return self._attempts.setdefault(client, PairAttempt())

    # -------------------------------------------------------------- sessions
    def issue_session(self, now: float | None = None) -> str:
        expiry = int((now or time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(self._secret, f"{expiry}".encode(), hashlib.sha256).hexdigest()[:32]
        return f"{expiry}.{sig}"

    def verify_session(self, cookie: str | None, now: float | None = None) -> bool:
        if not self.enabled:
            return True
        if not cookie or "." not in cookie:
            return False
        expiry_s, _, sig = cookie.partition(".")
        try:
            expiry = int(expiry_s)
        except ValueError:
            return False
        if expiry < (now or time.time()):
            return False
        expected = hmac.new(self._secret, f"{expiry}".encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)

    # ----------------------------------------------------------------- utils
    @staticmethod
    def parse_cookie(header: str | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in (header or "").split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                out[k.strip()] = v.strip()
        return out


AUTH_EXEMPT = {
    "/pair", "/api/ping", "/favicon.ico",
}


def is_exempt(path: str) -> bool:
    return path in AUTH_EXEMPT or path.startswith("/pair")
