FROM ghcr.io/home-assistant/base-python:3.14-alpine3.24@sha256:fb41585796deece71afd5a965e9a8aa1b5c2ceb4f020389d998fbe578dbbde6a

ARG TARGETARCH
ARG BUILD_ARCH=amd64

LABEL \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="1.6.3"

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
COPY runtime-site-packages/ /usr/local/lib/python3.14/site-packages/
COPY wheel/ /tmp/wheel/
COPY codex-release.json /tmp/codex-release.json
COPY assets/ /tmp/assets/
COPY rootfs/ /

RUN set -eux; \
    case "${BUILD_ARCH}:${TARGETARCH}" in amd64:amd64|aarch64:arm64) ;; *) exit 1 ;; esac; \
    /bin/bash -c 'source /usr/lib/bashio/bashio.sh; declare -F bashio::addon.ip_address >/dev/null'; \
    # Startup qualifies the explicitly enabled browser against this exact
    # Chromium package. ARM64 browser isolation is not yet qualified, so its
    # development image omits Chromium and cannot advertise browser tools.
    if [ "${BUILD_ARCH}" = "amd64" ]; then \
    wget -q -O /tmp/chromium-152.0.7977.82-r0.apk \
      https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-152.0.7977.82-r0.apk; \
    echo "01af27a66507b9762ecb86ad0fb74ec08b2613817cb0e9a2e6848221dcde53b0  /tmp/chromium-152.0.7977.82-r0.apk" | sha256sum -c -; \
    wget -q -O /tmp/chromium-common-152.0.7977.82-r0.apk \
      https://dl-cdn.alpinelinux.org/alpine/v3.24/community/x86_64/chromium-common-152.0.7977.82-r0.apk; \
    echo "96cab47f77a18f037f435c20f4a665a495395a57d42909bdefe9a561c5df5d8b  /tmp/chromium-common-152.0.7977.82-r0.apk" | sha256sum -c -; \
    apk add --no-cache /tmp/chromium-152.0.7977.82-r0.apk /tmp/chromium-common-152.0.7977.82-r0.apk; \
    rm /tmp/chromium-152.0.7977.82-r0.apk /tmp/chromium-common-152.0.7977.82-r0.apk; \
    test "$(/usr/bin/chromium-browser --product-version)" = "152.0.7977.82"; \
    fi; \
    addgroup -S codexbridge; \
    adduser -S -D -H -h /data -s /sbin/nologin -G codexbridge codexbridge; \
    python -m pip install --no-cache-dir --no-index --no-deps /tmp/wheel/codex_bridge_service-*.whl; \
    python /usr/local/libexec/codex-bridge/install_locked_assets.py \
      --arch "${BUILD_ARCH}" \
      --lock /tmp/codex-release.json \
      --lock-digest dd6718b1a595e539d0ce455b6271da9d30c435e35865908507b03c8e3668fb4d \
      --assets-dir /tmp/assets \
      --destination /usr/local/bin; \
    install -d -o root -g root -m 0755 /opt/codex/bin; \
    test ! -e /opt/codex/bin/bwrap; \
    install -o root -g root -m 0755 \
      /usr/local/libexec/codex-bridge/bwrap-wrapper.py \
      /opt/codex/bin/bwrap; \
    install -d -o root -g root -m 0755 /opt/codex-browser/bin; \
    install -o root -g root -m 0755 /usr/local/bin/bwrap /opt/codex-browser/bin/bwrap; \
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

ENV CODEX_BRIDGE_APP_VERSION="1.6.3" \
    CODEX_BRIDGE_VERSION="0.13.0" \
    CODEX_BRIDGE_CODEX_VERSION="0.156.1" \
    CODEX_BRIDGE_ARCH="${BUILD_ARCH}" \
    CODEX_BRIDGE_RELEASE_LOCK_DIGEST="dd6718b1a595e539d0ce455b6271da9d30c435e35865908507b03c8e3668fb4d" \
    CODEX_BRIDGE_SANDBOX_CONTRACT_VERSION="2"
