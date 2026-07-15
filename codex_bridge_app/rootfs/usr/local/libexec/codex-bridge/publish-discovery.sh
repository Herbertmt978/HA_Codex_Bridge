#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -Eeuo pipefail
umask 077

readonly token_path="/data/bridge-token"
child_pid=""
failure_status=0

terminate() {
    trap - TERM INT
    if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" 2>/dev/null; then
        kill -TERM "${child_pid}" 2>/dev/null || true
    fi
    if [[ -n "${child_pid}" ]]; then
        wait "${child_pid}" 2>/dev/null || true
    fi
    exit 0
}

run_child() {
    local status=0
    "$@" &
    child_pid=$!
    wait "${child_pid}" || status=$?
    child_pid=""
    return "${status}"
}

trap terminate TERM INT

while true; do
    if run_child env -i \
        PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
        python /usr/local/libexec/codex-bridge/wait_for_bridge.py \
        --url http://127.0.0.1:8766/ready \
        --token-file "${token_path}" \
        --timeout-seconds 60; then
        # The current Supervisor UI calls these packages Apps, while the pinned
        # Bashio runtime still exposes its compatible helper under addon.*.
        host="$(bashio::addon.ip_address)"
        if run_child python /usr/local/libexec/codex-bridge/publish_discovery.py \
            --host "${host}"; then
            unset SUPERVISOR_TOKEN host child_pid failure_status
            bashio::log.info "Codex Bridge discovery published."
            trap - TERM INT
            exec env -i \
                PATH=/command:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
                /command/s6-sleep 2147483647
        else
            failure_status=$?
            unset host
            case "${failure_status}" in
                2) bashio::log.error "Codex Bridge discovery configuration validation failed; retrying." ;;
                3) bashio::log.error "Codex Bridge discovery credential validation failed; retrying." ;;
                4) bashio::log.error "Codex Bridge discovery Supervisor response was rejected; retrying." ;;
                5) bashio::log.error "Codex Bridge discovery transport request failed; retrying." ;;
                6) bashio::log.error "Codex Bridge discovery storage update failed; retrying." ;;
                *) bashio::log.error "Codex Bridge discovery failed unexpectedly; retrying." ;;
            esac
            unset failure_status
        fi
    else
        bashio::log.error "Codex Bridge is not ready for discovery; retrying."
    fi

    run_child env -i PATH=/command:/usr/bin:/bin /command/s6-sleep 30 || true
done
