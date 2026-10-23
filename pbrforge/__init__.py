"""PBRForge -- local, personal texture tool.

Design constraint that shapes this whole package: the imaging pipeline, server,
CLI and PNG codec are standard-library Python. The single runtime dependency is
`cryptography`, used only for the encrypted vault, Ed25519 release verification
and local TLS certificates; the Windows installer bundles it. Heavy imaging and
ML stacks are deliberately absent. That is what keeps the portable build small
and makes `pbrforge serve` work on a machine that has never had pip run on it.
"""

__version__ = "2.0.0"
__all__ = ["__version__", "APP_NAME", "APP_SLUG", "USER_AGENT"]

APP_NAME = "PBRForge"
APP_SLUG = "pbrforge"
USER_AGENT = f"{APP_SLUG}/{__version__}"

# UDP port used by the LAN discovery beacon. Deliberately separate from the HTTP
# port: the beacon must be replyable by a server that is also serving HTTPS, and
# mixing them would mean the beacon dies whenever the HTTP port is reconfigured.
DISCOVERY_PORT = 7789
DISCOVERY_MAGIC = b"PBRFORGE?"
