from __future__ import annotations

from typing import Any

from .models import ReadinessStateRecord, RuntimeProfile


def evaluate_readiness(
    state: Any,
    *,
    include_catalogue: bool = True,
) -> ReadinessStateRecord:
    """Return a safe, shared readiness projection for health and turn gates."""

    if state.storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
        return ReadinessStateRecord()

    reasons: list[str] = []
    if state.sandbox_ready is not True:
        reasons.append("sandbox_unavailable")

    runtime = state.codex_app_server
    if (
        state.runtime_startup_failed
        or runtime is None
        or getattr(runtime, "ready", False) is not True
    ):
        reasons.append("runtime_unavailable")

    build_version = state.build_info.codex_version
    runtime_version = getattr(runtime, "server_version", None)
    if (
        isinstance(runtime_version, str)
        and build_version is not None
        and runtime_version != build_version
    ):
        reasons.append("runtime_version_mismatch")

    if reasons:
        return ReadinessStateRecord(state="fatal", reasons=tuple(reasons))

    coordinator = state.auth_coordinator
    status_method = getattr(coordinator, "status", None)
    try:
        auth_status = status_method() if callable(status_method) else None
    except Exception:
        return ReadinessStateRecord(
            state="fatal",
            reasons=("runtime_unavailable",),
        )
    if getattr(auth_status, "auth_required", None) is True:
        return ReadinessStateRecord(
            state="auth_required",
            reasons=("authentication_required",),
        )

    if not include_catalogue:
        return ReadinessStateRecord()

    try:
        catalogue = state.model_catalog_probe.probe()
    except Exception:
        return ReadinessStateRecord(
            state="degraded_catalogue",
            reasons=("catalogue_stale",),
        )
    if catalogue.stale:
        return ReadinessStateRecord(
            state="degraded_catalogue",
            reasons=("catalogue_stale",),
        )
    return ReadinessStateRecord()
