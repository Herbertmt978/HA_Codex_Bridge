FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24@sha256:f5a3334bf992c36fbf2d1a0fbd7138cc5689967c1f98e3982111195be273eb76

ARG TARGETARCH

LABEL \
    io.hass.type="app" \
    io.hass.arch="amd64" \
    io.hass.version="0.8.7"

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
COPY runtime-site-packages/ /usr/local/lib/python3.14/site-packages/
COPY wheel/ /tmp/wheel/
COPY codex-release.json /tmp/codex-release.json
COPY assets/ /tmp/assets/
COPY rootfs/ /

RUN set -eux; \
    test "${TARGETARCH}" = "amd64"; \
    /bin/bash -c 'source /usr/lib/bashio/bashio.sh; declare -F bashio::addon.ip_address >/dev/null'; \
    # Browser capability remains fail-closed until its separate AppArmor and
    # connection-time-egress proof exists, but the worker image itself must
    # use one reproducible Chromium package rather than a moving repository
    # default.  The exact Alpine 3.24 package is amd64-only by App contract.
    apk add --no-cache chromium=150.0.7871.124-r0; \
    test "$(/usr/bin/chromium-browser --product-version)" = "150.0.7871.124"; \
    addgroup -S codexbridge; \
    adduser -S -D -H -h /data -s /sbin/nologin -G codexbridge codexbridge; \
    python -m pip install --no-cache-dir --no-index --no-deps /tmp/wheel/codex_bridge_service-*.whl; \
    python /usr/local/libexec/codex-bridge/install_locked_assets.py \
      --arch "${TARGETARCH}" \
      --lock /tmp/codex-release.json \
      --lock-digest 0757937e7e7ecd07477f7ef7bd61d86edf43ac3181ef28353d139c6424580fed \
      --assets-dir /tmp/assets \
      --destination /usr/local/bin; \
    install -d -o root -g root -m 0755 /opt/codex/bin; \
    test ! -e /opt/codex/bin/bwrap; \
    install -o root -g root -m 0755 \
      /usr/local/libexec/codex-bridge/bwrap-wrapper.py \
      /opt/codex/bin/bwrap; \
    rm -rf /tmp/wheel /tmp/assets /tmp/codex-release.json /tmp/requirements-runtime.txt; \
    chmod 0755 /usr/local/bin/codex-ha /usr/local/bin/sandbox-self-test \
      /usr/local/bin/bwrap /opt/codex/bin/bwrap; \
    chmod 0444 /usr/local/share/codex-bridge/sandbox-contract.json; \
    chmod 0755 /usr/local/libexec/codex-bridge/*.py; \
    chmod 0755 /usr/local/libexec/codex-bridge/*.sh; \
    chmod 0700 /usr/local/libexec/codex-bridge/publish_discovery.py; \
    find /etc/s6-overlay/s6-rc.d -type f \( -name up -o -name run \) -exec chmod 0755 {} +; \
    install -d -o codexbridge -g codexbridge -m 0700 /data/bridge /data/codex-home /config/workspaces

EXPOSE 8766

ENV CODEX_BRIDGE_APP_VERSION="0.8.7" \
    CODEX_BRIDGE_VERSION="0.7.3" \
    CODEX_BRIDGE_CODEX_VERSION="0.144.5" \
    CODEX_BRIDGE_ARCH="amd64" \
    CODEX_BRIDGE_RELEASE_LOCK_DIGEST="0757937e7e7ecd07477f7ef7bd61d86edf43ac3181ef28353d139c6424580fed" \
    CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION="2"
