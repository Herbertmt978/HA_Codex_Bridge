#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -Eeuo pipefail
umask 077

readonly token_path="/data/bridge-token"
child_pid=""

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
        # Bashio retains the addon.* namespace even though Home Assistant now
        # presents these packages as Apps in the user interface.
        host="$(bashio::addon.hostname)"
        if run_child python /usr/local/libexec/codex-bridge/publish_discovery.py \
            --host "${host}"; then
            unset SUPERVISOR_TOKEN host child_pid
            bashio::log.info "Codex Bridge discovery published."
            trap - TERM INT
            exec env -i \
                PATH=/command:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
                /command/s6-sleep 2147483647
        fi
        unset host
        bashio::log.error "Codex Bridge discovery could not be published; retrying."
    else
        bashio::log.error "Codex Bridge is not ready for discovery; retrying."
    fi

    run_child env -i PATH=/command:/usr/bin:/bin /command/s6-sleep 30 || true
done
