FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24@sha256:51837e17a94b578cc00357c98482b61b53b27e37d910c97b86dd6448cfb11a6f

ARG TARGETARCH
ARG BUILD_ARCH=amd64

LABEL \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="1.13.24"

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
COPY runtime-site-packages/ /usr/local/lib/python3.14/site-packages/
COPY wheel/ /tmp/wheel/
COPY codex-release.json /tmp/codex-release.json
COPY assets/ /tmp/assets/
COPY rootfs/ /
COPY stdio-packages/ /opt/codex-stdio/packages/

RUN set -eux; \
    case "${BUILD_ARCH}:${TARGETARCH}" in amd64:amd64|aarch64:arm64) ;; *) exit 1 ;; esac; \
    # Scoped Git review requires the real Git executable in the App.
    apk add --no-cache git; \
    git --version >/dev/null; \
    /bin/bash -c 'source /usr/lib/bashio/bashio.sh; declare -F bashio::addon.ip_address >/dev/null'; \
    # Startup qualifies the explicitly enabled browser against this exact
    # Chromium package. ARM64 browser isolation is not yet qualified, so its
    # development image omits Chromium and cannot advertise browser tools.
    if [ "${BUILD_ARCH}" = "amd64" ]; then \
    wget -q -O /tmp/chromium-155.0.8059.84-r0.apk \
      https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-155.0.8059.84-r0.apk; \
    echo "cd414680bc60abafec2d5af30593e0b90c9089e8eb57d17c7b9eb5e0227b72bd  /tmp/chromium-155.0.8059.84-r0.apk" | sha256sum -c -; \
    wget -q -O /tmp/chromium-common-155.0.8059.84-r0.apk \
      https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-common-155.0.8059.84-r0.apk; \
    echo "73daf5c5d5c27dc594a04f8ba3b77443e51d492405ffaace27be07f6eed19dbc  /tmp/chromium-common-155.0.8059.84-r0.apk" | sha256sum -c -; \
    wget -q -O /tmp/chromium-angle-155.0.8059.84-r0.apk \
      https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-angle-155.0.8059.84-r0.apk; \
    echo "ee7971339dd8bedb5a1369d3481ef5033ec6a65b624cdea164eef51d619cbc33  /tmp/chromium-angle-155.0.8059.84-r0.apk" | sha256sum -c -; \
    apk add --no-cache /tmp/chromium-155.0.8059.84-r0.apk /tmp/chromium-common-155.0.8059.84-r0.apk /tmp/chromium-angle-155.0.8059.84-r0.apk; \
    rm /tmp/chromium-155.0.8059.84-r0.apk /tmp/chromium-common-155.0.8059.84-r0.apk /tmp/chromium-angle-155.0.8059.84-r0.apk; \
    test "$(/usr/bin/chromium-browser --product-version)" = "155.0.8059.84"; \
    fi; \
    addgroup -S codexbridge; \
    adduser -S -D -H -h /data -s /sbin/nologin -G codexbridge codexbridge; \
    python -m pip install --no-cache-dir --no-index --no-deps /tmp/wheel/codex_bridge_service-*.whl; \
    python /usr/local/libexec/codex-bridge/install_locked_assets.py \
      --arch "${BUILD_ARCH}" \
      --lock /tmp/codex-release.json \
      --lock-digest 2a3f6c78f217fd16165b9c28a06acebfb1324537a857526e4064bea235a98768 \
      --assets-dir /tmp/assets \
      --destination /usr/local/bin; \
    install -d -o root -g root -m 0755 /opt/codex/bin; \
    test ! -e /opt/codex/bin/bwrap; \
    install -o root -g root -m 0755 \
      /usr/local/libexec/codex-bridge/bwrap-wrapper.py \
      /opt/codex/bin/bwrap; \
    install -d -o root -g root -m 0755 /opt/codex-browser/bin; \
    install -o root -g root -m 0755 /usr/local/bin/bwrap /opt/codex-browser/bin/bwrap; \
    install -d -o root -g root -m 0755 /opt/codex-stdio/bin; \
    install -o root -g root -m 0755 /usr/local/bin/bwrap /opt/codex-stdio/bin/bwrap; \
    chown -R root:root /opt/codex-stdio/packages; \
    find /opt/codex-stdio/packages -type d -exec chmod 0555 {} +; \
    find /opt/codex-stdio/packages -type f -exec chmod 0444 {} +; \
    rm -rf /tmp/wheel /tmp/assets /tmp/codex-release.json /tmp/requirements-runtime.txt; \
    chmod 0755 /usr/local/bin/codex-app-entrypoint /usr/local/bin/codex-ha /usr/local/bin/sandbox-self-test \
      /usr/local/bin/bwrap /opt/codex/bin/bwrap; \
    chmod 0444 /usr/local/share/codex-bridge/sandbox-contract.json; \
    chmod 0755 /usr/local/libexec/codex-bridge/*.py; \
    chmod 0755 /usr/local/libexec/codex-bridge/*.sh; \
    chmod 0700 /usr/local/libexec/codex-bridge/publish_discovery.py; \
    find /etc/s6-overlay/s6-rc.d -type f \( -name up -o -name run \) -exec chmod 0755 {} +; \
    install -d -o codexbridge -g codexbridge -m 0700 /data/bridge /data/codex-home /config/workspaces

EXPOSE 8766

ENTRYPOINT ["/usr/local/bin/codex-app-entrypoint"]

ENV CODEX_BRIDGE_APP_VERSION="1.13.24" \
    CODEX_BRIDGE_VERSION="0.20.1" \
    CODEX_BRIDGE_CODEX_VERSION="0.166.0" \
    CODEX_BRIDGE_ARCH="${BUILD_ARCH}" \
    CODEX_BRIDGE_RELEASE_LOCK_DIGEST="2a3f6c78f217fd16165b9c28a06acebfb1324537a857526e4064bea235a98768" \
    CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION="2"
