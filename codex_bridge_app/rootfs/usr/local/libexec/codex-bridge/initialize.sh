#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -Eeuo pipefail

if ! python /usr/local/libexec/codex-bridge/initialize_runtime.py; then
    bashio::log.error "Private Codex Bridge runtime initialization failed."
    exit 1
fi

# A failed proof deliberately leaves no attestation.  The Bridge still starts
# so its authenticated readiness endpoint can report sandbox_unavailable.
if ! /usr/local/bin/sandbox-self-test; then
    bashio::log.warning "Codex tool isolation could not be attested; readiness remains fatal: sandbox_unavailable."
fi

# Only root-side initialization/discovery helpers may inherit Supervisor auth.
# The long-lived Bridge/Codex process constructs a clean environment itself.
unset SUPERVISOR_TOKEN
bashio::log.info "Private Codex Bridge runtime initialized."
