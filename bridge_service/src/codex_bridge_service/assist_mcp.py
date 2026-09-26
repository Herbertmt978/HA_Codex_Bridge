"""Bounded, private MCP selection for an Assist-owned conversation."""

from __future__ import annotations

import re
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .workspace import WorkspaceBoundary, WorkspaceBoundaryError


McpServerName = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
AssistMcpServers = Annotated[list[McpServerName], Field(max_length=32)]


def validate_selection(value: object) -> list[str]:
    """Canonicalise identifiers without accepting URLs, credentials or duplicates."""

    if (
        not isinstance(value, list)
        or len(value) > 32
        or any(
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name, re.ASCII) is None
            for name in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError("Assist MCP selection is invalid")
    return sorted(value)


def require_assist_layers(result: object) -> None:
    """Refuse higher-priority or unknown config that could override isolation."""

    layers = result.get("layers") if isinstance(result, Mapping) else None
    if not isinstance(layers, list) or len(layers) > 64:
        raise ValueError("Assist configuration authority is unavailable")
    known = {"packagedDefaults", "mdm", "system", "enterpriseManaged", "user",
             "project", "sessionFlags", "legacyManagedConfigTomlFromFile",
             "legacyManagedConfigTomlFromMdm"}
    for layer in layers:
        if not isinstance(layer, Mapping) or not isinstance(layer.get("name"), Mapping):
            raise ValueError("Assist configuration authority is unavailable")
        source = layer["name"].get("type")
        config = layer.get("config")
        disabled = layer.get("disabledReason")
        if not isinstance(source, str) or source not in known or not isinstance(config, Mapping) or (
            disabled is not None and not isinstance(disabled, str)
        ):
            raise ValueError("Assist configuration authority is unavailable")
        if disabled is None and config and source in {
            "legacyManagedConfigTomlFromFile", "legacyManagedConfigTomlFromMdm",
        }:
            raise ValueError("Assist configuration authority is unavailable")


def private_execution_directory(private_root: Path, workspace: Path) -> Path:
    """Use an empty Bridge-owned cwd, outside all granted workspace folders."""

    root, granted = private_root.resolve(), workspace.resolve()
    if root.is_relative_to(granted) or granted.is_relative_to(root):
        raise WorkspaceBoundaryError()
    boundary = WorkspaceBoundary(root)
    try:
        if os.name == "nt":
            # Windows is validation-only for the HA profile. Native HAOS uses
            # descriptor-rooted creation, ownership and empty-directory checks.
            (root / "assist-runtime").mkdir(mode=0o700, exist_ok=True)
            cwd = boundary.resolve_relative("assist-runtime", must_exist=True, kind="directory")
            if next(cwd.iterdir(), None) is not None:
                raise WorkspaceBoundaryError()
        else:
            boundary.create_directory("assist-runtime")
            descriptor = boundary.open_directory_fd("assist-runtime")
            try:
                identity = os.fstat(descriptor)
                if identity.st_uid != os.geteuid() or stat.S_IMODE(identity.st_mode) & 0o077:
                    raise WorkspaceBoundaryError()
                with os.scandir(descriptor) as entries:
                    if next(entries, None) is not None:
                        raise WorkspaceBoundaryError()
            finally:
                os.close(descriptor)
            cwd = boundary.resolve_relative("assist-runtime", must_exist=True, kind="directory")
        return cwd
    finally:
        boundary.close()


def isolation_config(
    servers: dict[str, object], workspace: Path, *, execution_cwd: Path | None = None,
) -> dict[str, object]:
    """Exclude non-selected MCP sources and unattended extension mechanisms."""

    cwd = execution_cwd or workspace
    result = {
        "mcp_servers": servers,
        # Empty markers and an untrusted private cwd exclude workspace layers.
        # A private directory also fences native background config reloads,
        # which can otherwise discover project layers before restoring session
        # trust overrides. No project instructions/extensions enter Assist.
        "project_root_markers": [],
        "projects": {str(cwd.resolve()): {"trust_level": "untrusted"}},
        "project_doc_max_bytes": 0,
        "features.apps": False,
        "features.plugins": False,
        "features.remote_plugin": False,
        "features.code_mode": False,
        "features.code_mode_only": False,
        "features.context_management": False,
        "features.deferred_executor": False,
        "features.goals": False,
        "features.memories": False,
        "features.tool_suggest": False,
        "features.token_budget": False,
        "features.multi_agent": False,
        "features.multi_agent_v2": False,
        "features.enable_fanout": False,
        "features.image_generation": False,
        "features.hooks": False,
        "features.shell_tool": False,
        "features.unified_exec": False,
        "features.standalone_web_search": False,
        "features.request_permissions_tool": False,
        "features.skill_mcp_dependency_install": False,
        "features.current_time_reminder": False,
        "features.shell_snapshot": False,
        "features.view_image": False,
        "agents.enabled": False,
        "cloud.skills.enabled": False,
        "skills.include_instructions": False,
        "tools.experimental_request_user_input.enabled": False,
        "tools.update_plan.enabled": False,
    }
    if execution_cwd is not None:
        # Retain read-only access to the original dedicated project, without
        # loading its .codex configuration or granting writes/network access.
        result["permissions"] = {"ha_observe": {
            "filesystem": {":minimal": "read", str(workspace.resolve()): "read"},
            "network": {"enabled": False, "allow_local_binding": False, "allow_upstream_proxy": False},
        }}
    return result
