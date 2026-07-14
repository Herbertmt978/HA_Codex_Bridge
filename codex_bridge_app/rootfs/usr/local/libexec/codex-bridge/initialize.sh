#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -Eeuo pipefail

if ! python /usr/local/libexec/codex-bridge/initialize_runtime.py; then
    bashio::log.error "Private Codex Bridge runtime initialization failed."
    exit 1
fi

# Only root-side initialization/discovery helpers may inherit Supervisor auth.
# The long-lived Bridge/Codex process constructs a clean environment itself.
unset SUPERVISOR_TOKEN
bashio::log.info "Private Codex Bridge runtime initialized."
