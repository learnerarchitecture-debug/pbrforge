# =============================================================================
#  Dockerfile -- PBRForge as a container, for a NAS or a Linux box
#
#  This is the alternative to the portable .exe, and it exists for one specific
#  situation: the machine that is always on is not a Windows PC. A Synology, a
#  QNAP, a Raspberry Pi, a small home server. Those run Docker; they do not run
#  a PyInstaller Windows binary.
#
#  Build:
#      docker build -f deploy/Dockerfile -t pbrforge:0.1.0 .
#  Run:
#      see deploy/docker-compose.yml, which is the supported way -- the CLI
#      invocation has too many volumes to get right by hand.
#
#  WHAT THIS IMAGE IS
#
#  The server and the CLI. That is the whole thing today. The pipeline stages
#  (seamless, delight, derive, repack) are not implemented yet, so the image does
#  not pretend to carry an imaging stack -- see the commented block at the bottom
#  for how to add one when the stages land. Shipping a 2 GB image with NumPy and
#  OpenCV that nothing imports would be worse than shipping a 90 MB one that does
#  exactly what it says.
#
#  THE DISCOVERY CAVEAT, STATED UP FRONT
#
#  UDP broadcast discovery (port 7789) does NOT work reliably from a bridged
#  container: the broadcast never leaves the bridge network, so `pbrforge
#  discover` on a client PC finds nothing. This is a property of Docker
#  networking, not of PBRForge. Two honest options:
#
#      network_mode: host      -- discovery works, no port mapping at all.
#                                 Linux only; not available on Docker Desktop.
#      bridge + a fixed URL    -- discovery does not work and is not needed.
#                                 Tell every client the address once. This is
#                                 what docker-compose.yml defaults to.
#
#  The port mapping below is kept even though host networking ignores it: the
#  compose file documents both, and a Dockerfile that only works one way is a
#  trap.
# =============================================================================

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="PBRForge" \
      org.opencontainers.image.description="Seamless texture and PBR material server for a local network" \
      org.opencontainers.image.source="https://github.com/learnerarchitecture-debug/pbrforge" \
      org.opencontainers.image.licenses="MIT"

# ---------------------------------------------------------------- build args
# The runtime is stdlib-only. `cryptography` is installed because in the
# container case HTTPS actually matters: the whole point of a NAS server is LAN
# clients, and a LAN client on http:// gets no WebGPU. Without a certificate the
# image would work and the preview would be quietly second-class.
#
# Set WITH_TLS=0 to build the smaller, HTTP-only image.
ARG WITH_TLS=1

# Deliberately NOT installed: numpy, Pillow, opencv, pyvips, torch. See the
# header. `pbrforge doctor` inside the container reports them as optional and
# absent, which is the truth.
ARG EXTRA_PACKAGES=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ------------------------------------------------------------------ the code
# Copied before anything else so a code change does not invalidate the pip layer.
COPY pbrforge/ /app/pbrforge/
COPY ui/ /app/ui/
COPY docs/ /app/docs/

# ------------------------------------------------------------- optional deps
RUN set -eux; \
    if [ "$WITH_TLS" = "1" ]; then \
        pip install --no-cache-dir "cryptography>=42" ; \
    fi; \
    if [ -n "$EXTRA_PACKAGES" ]; then \
        pip install --no-cache-dir $EXTRA_PACKAGES ; \
    fi; \
    python -m compileall -q /app/pbrforge ; \
    python -c "import sys; sys.path.insert(0,'/app'); import pbrforge; print('pbrforge', pbrforge.__version__)"

# ------------------------------------------------------------------- storage
# The config volume holds the pairing secret and the TLS material. If it is not
# persisted, every container restart rotates the secret and every paired client
# is logged out -- which looks like a bug and is a missing volume.
ENV PBRFORGE_CONFIG_DIR=/config \
    PBRFORGE_LIBRARY=/library \
    PBRFORGE_BIND=0.0.0.0 \
    PBRFORGE_PORT=7788 \
    PBRFORGE_HTTPS_PORT=7443

RUN mkdir -p /config /library && chmod 700 /config

VOLUME ["/config", "/library"]

# 7788 http, 7443 https, 7789/udp discovery (host networking only).
EXPOSE 7788 7443
EXPOSE 7789/udp

# --------------------------------------------------------------------- health
# /api/ping is actionable, so it is used rather than a TCP connect. A container
# whose socket is open but whose app is wedged would pass a connect check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7788/api/ping',timeout=4).status==200 else 1)"

COPY deploy/entrypoint.sh /usr/local/bin/pbrforge-entrypoint
RUN chmod +x /usr/local/bin/pbrforge-entrypoint

# Not root. The container only needs to write /config and /library, both of which
# are volumes; there is no reason for this process to be able to write the image.
#
# This does mean the volumes must be writable by uid 1000. On a NAS that usually
# means setting the share's owner, or `user:` in compose. DEPLOYMENT.md has the
# symptom ("permission denied writing /config") and the fix for each NAS vendor.
RUN useradd --uid 1000 --home-dir /app --shell /usr/sbin/nologin pbrforge \
    && chown -R pbrforge:pbrforge /app /config /library
USER pbrforge

ENTRYPOINT ["/usr/local/bin/pbrforge-entrypoint"]
CMD ["serve"]

# =============================================================================
#  Adding the imaging stack later
#
#  When the pipeline stages land, they need NumPy, Pillow and (ideally) pyvips.
#  Add them here rather than in the runtime image by default:
#
#      pip install --no-cache-dir numpy pillow
#
#  libvips is a system library, not a Python package. For the full set:
#
#      RUN apt-get update && apt-get install -y --no-install-recommends \
#              libvips42 && rm -rf /var/lib/apt/lists/*
#
#  That is roughly +40 MB (numpy+pillow) or +90 MB with libvips. Worth it only
#  once something imports them.
# =============================================================================
