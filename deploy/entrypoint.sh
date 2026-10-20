#!/bin/sh
# =============================================================================
#  entrypoint.sh -- start the container sensibly, whatever it was asked to do
#
#  Three behaviours, and each one exists because the alternative is a confusing
#  first run:
#
#   1. `serve` (the default) does the setup the user cannot do from outside the
#      container: create the config, generate the certificate if one is missing,
#      and print the URL and the pairing code where `docker compose logs` will
#      show them.
#
#   2. A one-shot command -- `doctor`, `url`, `code`, `selftest` -- runs and exits.
#      Without this, `docker compose run pbrforge code` would start a server and
#      never return, which makes the pairing code impossible to retrieve from a
#      headless container. That single case is the whole reason this file exists.
#
#   3. `sh` or anything else is passed through, so the container is debuggable.
#
#  A headless container has no console, so the pairing code has to be readable
#  from outside. Two ways, both fine:
#      docker compose logs pbrforge          the code is in the startup banner
#      docker compose run --rm pbrforge code  prints it on demand
# =============================================================================

set -eu

log() { printf '  %s\n' "$*"; }
hr()  { printf '  %s\n' "--------------------------------------------------------"; }

# --------------------------------------------------------------------- env
: "${PBRFORGE_CONFIG_DIR:=/config}"
: "${PBRFORGE_LIBRARY:=/library}"
: "${PBRFORGE_PORT:=7788}"
export PBRFORGE_CONFIG_DIR PBRFORGE_LIBRARY PBRFORGE_PORT

# ------------------------------------------------------------ sanity: volumes
# The single most common container mistake is forgetting to mount /config. The
# server then works perfectly and forgets every paired client on every restart,
# which reads as an authentication bug and is a missing volume. Detect it by
# checking whether /config is a mount point, and say so once.
if [ -d /config ] && ! mountpoint -q /config 2>/dev/null; then
    hr
    log "WARNING  /config is not a mount point."
    log "         The pairing secret and the HTTPS certificate are being written"
    log "         inside the container and will be LOST when it is replaced."
    log "         Every client will have to pair again after an upgrade."
    log ""
    log "         Fix: add a volume for /config. See deploy/docker-compose.yml."
    hr
fi

if [ ! -w "$PBRFORGE_CONFIG_DIR" ]; then
    hr
    log "FATAL  $PBRFORGE_CONFIG_DIR is not writable by uid $(id -u)."
    log "       On a NAS the share is usually owned by a different user."
    log "       Fix one of:"
    log "         - chown the host directory to uid 1000"
    log "         - set 'user: <uid>:<gid>' in docker-compose.yml"
    log "         - set PUID/PGID if your NAS compose template uses them"
    hr
    exit 1
fi

# ------------------------------------------------------------------ behaviour
cmd="${1:-serve}"

case "$cmd" in
    serve)
        # The certificate is generated on first start rather than at build time:
        # at build time the SANs would be baked to addresses the machine does not
        # have, and the certificate would be wrong for every deployment.
        if [ ! -f "$PBRFORGE_CONFIG_DIR/tls/ca.pem" ]; then
            log "No certificate yet. Creating one for this machine's addresses..."
            if python -m pbrforge tls init >/dev/null 2>&1; then
                log "Certificate ready. LAN clients keep WebGPU."
            else
                log "Could not create a certificate (the 'cryptography' package is"
                log "not in this image). Serving HTTP. Browsers on other machines"
                log "will fall back to WebGL2 for the preview; everything else is"
                log "identical. Rebuild with --build-arg WITH_TLS=1 for HTTPS."
            fi
        fi

        # The banner the user needs is printed by `serve` itself. It goes to
        # stdout, so `docker compose logs` shows the URL and the pairing code.
        exec python -m pbrforge "$@"
        ;;

    doctor|url|code|config|cache|selftest|version|tls|discover)
        # One-shot. No banner, no server, exit with its own status so a scripted
        # `docker compose run pbrforge doctor` can be tested for success.
        exec python -m pbrforge "$@"
        ;;

    sh|bash|/bin/sh|/bin/bash|python|python3)
        exec "$@"
        ;;

    *)
        # Anything else that looks like a pbrforge flag rather than a command:
        # `docker run pbrforge --port 9000` should work.
        case "$cmd" in
            -*) exec python -m pbrforge serve "$@" ;;
            *)  exec "$@" ;;
        esac
        ;;
esac
