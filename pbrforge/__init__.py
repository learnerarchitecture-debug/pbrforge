"""PBRForge -- local, personal texture tool.

Design constraint that shapes this whole package: the *installable* build must run
with nothing but the Python standard library. Everything heavy (numpy, libvips,
Pillow, torch) is an optional layer that the pipeline stages use when present and
`doctor` reports on. That is what makes a single-file installer possible and what
makes `pbrforge serve` work on a machine that has never had pip run on it.
"""

__version__ = "0.2.0"
__all__ = ["__version__", "APP_NAME", "APP_SLUG", "USER_AGENT"]

APP_NAME = "PBRForge"
APP_SLUG = "pbrforge"
USER_AGENT = f"{APP_SLUG}/{__version__}"

# UDP port used by the LAN discovery beacon. Deliberately separate from the HTTP
# port: the beacon must be replyable by a server that is also serving HTTPS, and
# mixing them would mean the beacon dies whenever the HTTP port is reconfigured.
DISCOVERY_PORT = 7789
DISCOVERY_MAGIC = b"PBRFORGE?"
