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

# Browser readiness is independent of the Codex tool sandbox. An unavailable
# optional browser must not prevent ordinary chats from starting.
enable_browser="$(bashio::config 'enable_browser')"
case "${enable_browser}" in
    true)
        if ! python /usr/local/libexec/codex-bridge/browser_attest.py; then
            bashio::log.warning "Browser isolation could not be verified; browser tools remain unavailable."
        fi
        ;;
    false|null|'')
        python /usr/local/libexec/codex-bridge/browser_attest.py --disabled
        ;;
    *)
        bashio::log.error "The enable_browser option must be a boolean."
        exit 1
        ;;
esac

# Stdio workers have a distinct, default-closed boundary. Only a successful
# root-side probe from this boot permits the non-root Bridge to advertise them.
enable_stdio_mcp="$(bashio::config 'enable_stdio_mcp')"
case "${enable_stdio_mcp}" in
    true)
        if ! python /usr/local/libexec/codex-bridge/stdio_attest.py; then
            bashio::log.warning "Stdio MCP isolation could not be verified; stdio servers remain unavailable."
        fi
        ;;
    false|null|'')
        python /usr/local/libexec/codex-bridge/stdio_attest.py --disabled
        ;;
    *)
        bashio::log.error "The enable_stdio_mcp option must be a boolean."
        exit 1
        ;;
esac

# Only root-side initialization/discovery helpers may inherit Supervisor auth.
# The long-lived Bridge/Codex process constructs a clean environment itself.
unset SUPERVISOR_TOKEN
bashio::log.info "Private Codex Bridge runtime initialized."
