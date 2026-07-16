"""Safe projections for provider-backed optional Bridge features."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_PROVIDER_CAPABILITY_NAMES = (
    "image_generation",
    "web_search",
    "namespace_tools",
)


def provider_capabilities(state: Any) -> dict[str, bool | None]:
    """Return the bounded provider capability projection without probe details."""

    projected: dict[str, bool | None] = {
        name: None for name in _PROVIDER_CAPABILITY_NAMES
    }
    manager = getattr(state, "capabilities_manager", None)
    provider = getattr(manager, "provider_capabilities", None)
    if not callable(provider):
        return projected
    try:
        values = provider()
    except Exception:
        return projected
    if not isinstance(values, Mapping):
        return projected
    for name in _PROVIDER_CAPABILITY_NAMES:
        value = values.get(name)
        if value is None or type(value) is bool:
            projected[name] = value
    return projected


def supports_web_search(state: Any) -> bool:
    """Whether the managed App may accept a native web-search override."""

    profile = getattr(getattr(state, "storage", None), "runtime_profile", None)
    return (
        getattr(profile, "value", profile) == "home_assistant"
        and provider_capabilities(state)["web_search"] is True
    )


def readiness_capabilities(state: Any) -> tuple[str, ...]:
    """Combine static Bridge capabilities with verified provider features only."""

    capabilities = list(
        getattr(state, "feature_capabilities", ("api_v1", "legacy_v0"))
    )
    profile = getattr(getattr(state, "storage", None), "runtime_profile", None)
    if getattr(profile, "value", profile) != "home_assistant":
        return tuple(dict.fromkeys(capabilities))
    provider = provider_capabilities(state)
    if provider["web_search"] is True:
        capabilities.append("web_search_v1")
    if (
        provider["image_generation"] is True
        and provider["namespace_tools"] is True
    ):
        capabilities.append("image_generation_v1")
    return tuple(dict.fromkeys(capabilities))
