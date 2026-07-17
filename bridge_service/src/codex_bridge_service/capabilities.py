"""Safe, typed access to Codex skills, plugins, and marketplaces.

The app-server owns Codex's configuration and package caches.  This module is
intentionally a narrow adapter: callers can select an existing HA workspace,
but can never pass a private App path or arbitrary JSON-RPC payload through to
Codex.
"""

from __future__ import annotations

from contextlib import contextmanager
from ipaddress import ip_address
import re
import json
from math import isfinite
import os
import socket
import time
from pathlib import Path
from threading import Condition, RLock
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from .codex_app_server import CodexAppServerError
from .runtime_gate import RuntimeGate, RuntimeGateError
from .storage import BridgeStorage, ProjectNotFoundError
from .workspace import (
    WorkspaceBoundaryError,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspaceExistsError,
)

_MAX_NAME_BYTES = 128
_MAX_TEXT_BYTES = 4096
_MAX_SKILLS = 512
# Keep projection bounded while accommodating the current Codex marketplace
# catalogue (which contains roughly 1,900 plugins).
_MAX_PLUGINS = 4096
_MAX_MARKETPLACES = 128
_PLUGIN_CATALOGUE_TIMEOUT_SECONDS = 60.0
_PROVIDER_CAPABILITIES_TTL_SECONDS = 5.0
_MAX_SKILL_DESCRIPTION = 4096
_MAX_SKILL_INSTRUCTIONS = 256 * 1024
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@-]{0,127}\Z", re.ASCII)
_CREDENTIAL = re.compile(
    r"(?:bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)"
    r"[A-Za-z0-9._~+/=-]{8,}",
    re.IGNORECASE,
)
_UNKNOWN_PROVIDER_CAPABILITIES: dict[str, bool | None] = {
    "image_generation": None,
    "web_search": None,
    "namespace_tools": None,
}


class CapabilitiesError(RuntimeError):
    """Base class for errors whose public message is safe and bounded."""

    code = "capabilities_error"


class CapabilitiesInvalidError(CapabilitiesError):
    code = "capabilities_invalid"


class CapabilitiesConflictError(CapabilitiesError):
    code = "capabilities_conflict"


class CapabilitiesUnavailableError(CapabilitiesError):
    code = "capabilities_unavailable"


class ImageGenerationPublicationLease:
    """One revocable, in-memory authority to publish a generated image.

    A lease is intentionally not durable and is valid only while its owning
    ``CapabilitiesManager`` still has the exact verified capability revision.
    Invalidating ChatGPT/provider capability state marks outstanding leases
    revoked before it waits for them to drain.  Callers therefore have a
    linearizable final check immediately before each irreversible persistence
    boundary, without keeping the runtime broker lock during image validation
    or disk I/O.
    """

    __slots__ = ("_manager", "_token")

    def __init__(self, manager: "CapabilitiesManager", token: int) -> None:
        self._manager = manager
        self._token = token

    def ensure_active(self) -> None:
        """Fail closed when sign-out or capability invalidation won the race."""

        self._manager._ensure_image_generation_lease_active(self._token)

    def release(self) -> None:
        """Release this bounded authority; safe to call repeatedly."""

        self._manager._release_image_generation_lease(self._token)


def _safe_text(value: object, *, limit: int = _MAX_TEXT_BYTES) -> str | None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        return None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
        return None
    if _CREDENTIAL.search(value):
        return "[redacted]"
    return value


def _safe_name(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_NAME_BYTES:
        raise CapabilitiesInvalidError(f"{label} is invalid")
    if _SAFE_NAME.fullmatch(value) is None:
        raise CapabilitiesInvalidError(f"{label} is invalid")
    return value


def _safe_ref(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > _MAX_NAME_BYTES
        or _SAFE_REF.fullmatch(value) is None
    ):
        raise CapabilitiesInvalidError("marketplace reference is invalid")
    return value


def _safe_plugin_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > _MAX_NAME_BYTES
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", value, re.ASCII) is None
    ):
        raise CapabilitiesInvalidError("plugin id is invalid")
    return value


def _resolve_host(host: str) -> tuple[str, ...]:
    """Best-effort resolver kept injectable so validation tests do not use DNS."""

    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return tuple(
        dict.fromkeys(
            record[4][0]
            for record in records
            if isinstance(record[4], tuple) and record[4]
        )
    )


class CapabilitiesManager:
    """Constrain app-server capability APIs to one selected workspace."""

    def __init__(
        self,
        storage: BridgeStorage,
        app_server: Any,
        runtime_gate: RuntimeGate | None = None,
        *,
        resolver: Callable[[str], tuple[str, ...]] = _resolve_host,
        clock: Callable[[], float] = time.monotonic,
        provider_capabilities_ttl_seconds: float = _PROVIDER_CAPABILITIES_TTL_SECONDS,
    ) -> None:
        self.storage = storage
        self.app_server = app_server
        self.runtime_gate = runtime_gate
        self._resolver = resolver
        if (
            isinstance(provider_capabilities_ttl_seconds, bool)
            or not isinstance(provider_capabilities_ttl_seconds, (int, float))
            or not isfinite(provider_capabilities_ttl_seconds)
            or provider_capabilities_ttl_seconds <= 0
        ):
            raise ValueError("provider capability TTL must be positive")
        self._clock = clock
        self._provider_capabilities_ttl_seconds = float(
            provider_capabilities_ttl_seconds
        )
        self._provider_capabilities_lock = RLock()
        self._provider_capabilities_condition = Condition(
            self._provider_capabilities_lock
        )
        self._provider_capabilities_generation: int | None = None
        self._provider_capabilities_cached_at: float | None = None
        self._provider_capabilities_cache = dict(_UNKNOWN_PROVIDER_CAPABILITIES)
        self._provider_capabilities_revision = 0
        self._image_generation_lease_next_token = 0
        self._image_generation_leases: dict[int, tuple[int, int]] = {}

    def provider_capabilities(self) -> dict[str, bool | None]:
        """Return a short-lived provider-capability probe per generation.

        This diagnostics-only read preserves uncertainty: unavailable, malformed,
        and stale responses are all unknown rather than negative capabilities.
        Only a verified response is cached.  A transient failure must be able to
        recover without forcing an app-server restart merely to change generation.
        """

        with self._provider_capabilities_lock:
            generation = self._app_server_generation()
            if generation is None:
                return dict(_UNKNOWN_PROVIDER_CAPABILITIES)
            if generation == self._provider_capabilities_generation:
                # A verified negative result must not live forever: auth can
                # become available without restarting the app-server. Unknown
                # failures are deliberately never marked as cached, so every
                # call remains immediately retryable.
                try:
                    cached_at = self._provider_capabilities_cached_at
                    if (
                        cached_at is not None
                        and self._clock() - cached_at
                        < self._provider_capabilities_ttl_seconds
                    ):
                        return dict(self._provider_capabilities_cache)
                except (RuntimeError, OSError, ValueError, TypeError):
                    pass
            try:
                result = self.app_server.read_model_provider_capabilities()
                current_generation = self._app_server_generation()
                if (
                    current_generation is not None
                    and getattr(result, "generation", None) == current_generation
                ):
                    projected = self._project_provider_capabilities(result)
                    if all(type(value) is bool for value in projected.values()):
                        if (
                            self._provider_capabilities_generation
                            != current_generation
                            or projected != self._provider_capabilities_cache
                        ):
                            self._provider_capabilities_revision += 1
                        self._provider_capabilities_generation = current_generation
                        self._provider_capabilities_cache = projected
                        self._provider_capabilities_cached_at = self._clock()
                        return dict(projected)
            except (
                CodexAppServerError,
                RuntimeError,
                OSError,
                ValueError,
                AttributeError,
                TypeError,
            ):
                pass
            return dict(_UNKNOWN_PROVIDER_CAPABILITIES)

    def invalidate_provider_capabilities(self) -> None:
        """Revoke image publication before discarding a changed identity.

        The revision flip is visible before this method waits for current image
        publication leases.  A worker blocked in validation/storage therefore
        sees revocation at its next persistence check and cannot emit an
        artifact event after sign-out returns.
        """

        with self._provider_capabilities_condition:
            self._provider_capabilities_revision += 1
            self._provider_capabilities_generation = None
            self._provider_capabilities_cached_at = None
            self._provider_capabilities_cache = dict(_UNKNOWN_PROVIDER_CAPABILITIES)
            while self._image_generation_leases:
                self._provider_capabilities_condition.wait()

    def authorize_image_generation(self, expected_generation: int) -> int | None:
        """Verify image generation for one exact app-server generation.

        The probe may block, so the runtime broker calls this before dispatching
        the owning turn.  A result is authoritative only when both native image
        generation and namespace tools were positively advertised by the same
        app-server generation that will own that turn.
        """

        if type(expected_generation) is not int or expected_generation < 1:
            return None
        capabilities = self.provider_capabilities()
        if not (
            capabilities["image_generation"] is True
            and capabilities["namespace_tools"] is True
        ):
            return None
        with self._provider_capabilities_lock:
            revision = self._provider_capabilities_revision
            if self.is_image_generation_authorized(expected_generation, revision):
                return revision
        return None

    def is_image_generation_authorized(
        self,
        expected_generation: int,
        expected_revision: int,
    ) -> bool:
        """Read a previously verified image-generation authority without I/O."""

        if (
            type(expected_generation) is not int
            or expected_generation < 1
            or type(expected_revision) is not int
            or expected_revision < 1
        ):
            return False
        with self._provider_capabilities_lock:
            return self._is_image_generation_authorized_locked(
                expected_generation,
                expected_revision,
            )

    def acquire_image_generation_publication_lease(
        self,
        expected_generation: int,
        expected_revision: int,
    ) -> ImageGenerationPublicationLease | None:
        """Acquire one revocable authority spanning image validation and save.

        The lease must always be released by the caller.  It is deliberately
        acquired only for an already-authorized completion item, so a slow
        provider image never blocks normal capability probes or runtime state.
        """

        with self._provider_capabilities_condition:
            if not self._is_image_generation_authorized_locked(
                expected_generation,
                expected_revision,
            ):
                return None
            self._image_generation_lease_next_token += 1
            token = self._image_generation_lease_next_token
            self._image_generation_leases[token] = (
                expected_generation,
                expected_revision,
            )
            return ImageGenerationPublicationLease(self, token)

    def _ensure_image_generation_lease_active(self, token: int) -> None:
        with self._provider_capabilities_lock:
            authority = self._image_generation_leases.get(token)
            if authority is None or not self._is_image_generation_authorized_locked(
                *authority
            ):
                raise CapabilitiesUnavailableError(
                    "image generation capability is no longer authorized"
                )

    def _release_image_generation_lease(self, token: int) -> None:
        with self._provider_capabilities_condition:
            if self._image_generation_leases.pop(token, None) is not None:
                self._provider_capabilities_condition.notify_all()

    def _is_image_generation_authorized_locked(
        self,
        expected_generation: int,
        expected_revision: int,
    ) -> bool:
        return (
            self._app_server_generation() == expected_generation
            and self._provider_capabilities_generation == expected_generation
            and self._provider_capabilities_revision == expected_revision
            and self._provider_capabilities_cached_at is not None
            and self._provider_capabilities_cache["image_generation"] is True
            and self._provider_capabilities_cache["namespace_tools"] is True
        )

    def _app_server_generation(self) -> int | None:
        try:
            generation = self.app_server.generation
        except (RuntimeError, OSError, ValueError, AttributeError):
            return None
        if type(generation) is not int or generation < 0:
            return None
        return generation

    @staticmethod
    def _project_provider_capabilities(value: object) -> dict[str, bool | None]:
        values = {
            "image_generation": getattr(value, "image_generation", None),
            "web_search": getattr(value, "web_search", None),
            "namespace_tools": getattr(value, "namespace_tools", None),
        }
        if any(type(item) is not bool for item in values.values()):
            return dict(_UNKNOWN_PROVIDER_CAPABILITIES)
        return values

    def workspace_cwd(self, workspace_path: str) -> tuple[str, str]:
        boundary = self.storage.workspace_boundary
        if boundary is None:
            raise CapabilitiesInvalidError("workspace capabilities are unavailable")
        try:
            normalized = boundary.normalize(workspace_path, allow_root=True)
            path = boundary.resolve_relative(
                normalized,
                must_exist=True,
                kind="directory",
            )
        except (WorkspaceBoundaryError, OSError, ValueError):
            raise CapabilitiesInvalidError("workspace path is invalid") from None
        return normalized, str(path)

    def resolve_workspace(
        self, *, workspace_path: str | None = None, project_id: str | None = None
    ) -> tuple[str, str]:
        if (workspace_path is None) == (project_id is None):
            raise CapabilitiesInvalidError("provide exactly one workspace selector")
        if project_id is not None:
            if (
                not isinstance(project_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id) is None
            ):
                raise CapabilitiesInvalidError("project id is invalid")
            try:
                project = self.storage.load_project(project_id)
            except ProjectNotFoundError:
                raise CapabilitiesInvalidError("project is unavailable") from None
            workspace_path = project.root_path
        assert workspace_path is not None
        return self.workspace_cwd(workspace_path)

    def list_skills(
        self, workspace_path: str, *, force_reload: bool = False
    ) -> dict[str, Any]:
        normalized, cwd = self.workspace_cwd(workspace_path)
        result = self._request(
            "skills/list",
            {"cwds": [cwd], "forceReload": bool(force_reload)},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise CapabilitiesUnavailableError()
        entries: list[dict[str, Any]] = []
        for raw_entry in result["data"][:1]:
            if not isinstance(raw_entry, dict):
                continue
            skills = raw_entry.get("skills")
            if not isinstance(skills, list):
                continue
            projected = [
                self._project_skill(item, normalized) for item in skills[:_MAX_SKILLS]
            ]
            projected = [item for item in projected if item is not None]
            errors = []
            raw_errors = raw_entry.get("errors")
            if isinstance(raw_errors, list):
                for raw_error in raw_errors[:16]:
                    if not isinstance(raw_error, dict):
                        continue
                    message = _safe_text(raw_error.get("message"), limit=512)
                    if message:
                        errors.append(message)
            entries.append({"cwd": normalized, "skills": projected, "errors": errors})
        return {"data": entries}

    def set_skill(
        self,
        workspace_path: str,
        *,
        enabled: bool,
        name: str | None = None,
        relative_path: str | None = None,
    ) -> dict[str, bool]:
        _normalized, cwd = self.workspace_cwd(workspace_path)
        if (name is None) == (relative_path is None):
            raise CapabilitiesInvalidError("provide exactly one skill selector")
        params: dict[str, Any] = {"enabled": bool(enabled)}
        if name is not None:
            params["name"] = _safe_name(name, label="skill name")
        else:
            assert relative_path is not None
            boundary = self.storage.workspace_boundary
            assert boundary is not None
            try:
                relative = boundary.normalize(relative_path)
                candidate = boundary.resolve_relative(
                    relative, must_exist=True, kind="file"
                )
                try:
                    candidate.relative_to(Path(cwd))
                except ValueError:
                    raise WorkspaceInputError() from None
            except (WorkspaceBoundaryError, OSError, ValueError):
                raise CapabilitiesInvalidError("skill path is invalid") from None
            params["path"] = str(candidate)
        with self._mutation():
            result = self._request("skills/config/write", params)
        if (
            not isinstance(result, dict)
            or type(result.get("effectiveEnabled")) is not bool
        ):
            raise CapabilitiesUnavailableError()
        return {"effective_enabled": result["effectiveEnabled"]}

    def create_skill(
        self,
        *,
        workspace_path: str | None = None,
        project_id: str | None = None,
        name: str,
        description: str,
        instructions: str,
    ) -> dict[str, Any]:
        normalized, cwd = self.resolve_workspace(
            workspace_path=workspace_path, project_id=project_id
        )
        skill_name = _safe_name(name, label="skill name")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description.encode("utf-8")) > _MAX_SKILL_DESCRIPTION
        ):
            raise CapabilitiesInvalidError("skill description is invalid")
        if (
            not isinstance(instructions, str)
            or len(instructions.encode("utf-8")) > _MAX_SKILL_INSTRUCTIONS
        ):
            raise CapabilitiesInvalidError("skill instructions are invalid")
        if any(
            ord(char) < 32 and char not in "\r\n\t"
            for char in description + instructions
        ):
            raise CapabilitiesInvalidError(
                "skill content contains unsupported control characters"
            )
        frontmatter = (
            "---\nname: "
            + json.dumps(skill_name, ensure_ascii=True)
            + "\ndescription: "
            + json.dumps(description, ensure_ascii=True)
            + "\n---\n\n"
        )
        raw = (frontmatter + instructions).encode("utf-8")
        if len(raw) > _MAX_SKILL_INSTRUCTIONS:
            raise CapabilitiesInvalidError("skill is too large")
        relative = (
            f"{normalized}/.agents/skills/{skill_name}/SKILL.md"
            if normalized != "."
            else f".agents/skills/{skill_name}/SKILL.md"
        )
        boundary = self.storage.workspace_boundary
        assert boundary is not None
        skill_dir = relative.rsplit("/", 1)[0]
        with self._mutation():
            try:
                boundary.create_directory(skill_dir)
                with boundary.create_file_exclusive(relative) as stream:
                    view = memoryview(raw)
                    while view:
                        written = stream.write(view)
                        if not isinstance(written, int) or written <= 0:
                            raise OSError("skill write failed")
                        view = view[written:]
                    stream.flush()
                    os.fsync(stream.fileno())
            except WorkspaceExistsError:
                raise CapabilitiesConflictError() from None
            except (WorkspaceBoundaryError, OSError):
                raise CapabilitiesUnavailableError() from None
            self._refresh_skills(cwd)
        return {
            "name": skill_name,
            "workspace_path": normalized,
            "size_bytes": len(raw),
        }

    def delete_skill(
        self,
        *,
        workspace_path: str | None = None,
        project_id: str | None = None,
        name: str,
    ) -> None:
        normalized, cwd = self.resolve_workspace(
            workspace_path=workspace_path, project_id=project_id
        )
        skill_name = _safe_name(name, label="skill name")
        relative = (
            f"{normalized}/.agents/skills/{skill_name}/SKILL.md"
            if normalized != "."
            else f".agents/skills/{skill_name}/SKILL.md"
        )
        skill_dir = relative.rsplit("/", 1)[0]
        boundary = self.storage.workspace_boundary
        assert boundary is not None
        with self._mutation():
            try:
                boundary.unlink_regular_file(relative)
                boundary.remove_empty_directory(skill_dir)
            except WorkspaceNotFoundError:
                raise CapabilitiesInvalidError("skill is not managed") from None
            except (WorkspaceBoundaryError, OSError):
                raise CapabilitiesUnavailableError() from None
            self._refresh_skills(cwd)

    def _refresh_skills(self, cwd: str) -> None:
        try:
            self._request("skills/list", {"cwds": [cwd], "forceReload": True})
        except CapabilitiesUnavailableError:
            # The file operation succeeded; a stale cache must not turn it into
            # an apparent failed mutation. The next list call can force reload.
            return

    def list_plugins(
        self,
        workspace_path: str,
        *,
        installed_only: bool = False,
    ) -> dict[str, Any]:
        normalized, cwd = self.workspace_cwd(workspace_path)
        method = "plugin/installed" if installed_only else "plugin/list"
        params = {"cwds": [cwd]}
        # The native plugin catalogue can be several MiB and takes materially
        # longer to produce on a cold Codex cache. Keep this longer deadline
        # scoped to catalogue reads; ordinary app-server requests retain the
        # client's 30-second default.
        result = self._request(
            method,
            params,
            timeout_seconds=_PLUGIN_CATALOGUE_TIMEOUT_SECONDS,
        )
        if not isinstance(result, dict) or not isinstance(
            result.get("marketplaces"), list
        ):
            raise CapabilitiesUnavailableError()
        marketplaces = self._project_marketplaces(result["marketplaces"])
        return {"cwd": normalized, "marketplaces": marketplaces}

    def install_plugin(
        self, plugin_name: str, marketplace_name: str | None = None
    ) -> dict[str, Any]:
        plugin = _safe_name(plugin_name, label="plugin name")
        params: dict[str, Any] = {"pluginName": plugin}
        if marketplace_name is not None:
            params["remoteMarketplaceName"] = _safe_name(
                marketplace_name,
                label="marketplace name",
            )
        with self._mutation():
            result = self._request("plugin/install", params)
        if not isinstance(result, dict):
            raise CapabilitiesUnavailableError()
        auth_policy = _safe_text(result.get("authPolicy"), limit=64)
        needing_auth = result.get("appsNeedingAuth")
        count = len(needing_auth) if isinstance(needing_auth, list) else 0
        return {
            "plugin_name": plugin,
            "auth_policy": auth_policy,
            "apps_needing_auth": min(count, 16),
        }

    def uninstall_plugin(self, plugin_id: str) -> dict[str, str]:
        plugin = _safe_plugin_id(plugin_id)
        with self._mutation():
            self._request("plugin/uninstall", {"pluginId": plugin})
        return {"plugin_id": plugin}

    def add_marketplace(
        self,
        source: str,
        *,
        ref_name: str | None = None,
        sparse_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        source = self._safe_marketplace_source(source)
        params: dict[str, Any] = {"source": source}
        if ref_name is not None:
            params["refName"] = _safe_ref(ref_name)
        if sparse_paths is not None:
            if len(sparse_paths) > 8:
                raise CapabilitiesInvalidError("too many sparse paths")
            clean_paths: list[str] = []
            for value in sparse_paths:
                if (
                    not isinstance(value, str)
                    or not value
                    or value.startswith(("/", "\\"))
                    or ".." in value.replace("\\", "/").split("/")
                ):
                    raise CapabilitiesInvalidError("sparse path is invalid")
                clean_paths.append(value[:256])
            params["sparsePaths"] = clean_paths
        with self._mutation():
            result = self._request("marketplace/add", params)
        if not isinstance(result, dict):
            raise CapabilitiesUnavailableError()
        name = _safe_name(result.get("marketplaceName"), label="marketplace name")
        return {
            "marketplace_name": name,
            "already_added": result.get("alreadyAdded") is True,
        }

    def remove_marketplace(self, marketplace_name: str) -> dict[str, str]:
        name = _safe_name(marketplace_name, label="marketplace name")
        with self._mutation():
            self._request("marketplace/remove", {"marketplaceName": name})
        return {"marketplace_name": name}

    def upgrade_marketplace(
        self, marketplace_name: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if marketplace_name is not None:
            params["marketplaceName"] = _safe_name(
                marketplace_name, label="marketplace name"
            )
        with self._mutation():
            result = self._request("marketplace/upgrade", params)
        if not isinstance(result, dict):
            raise CapabilitiesUnavailableError()
        selected = result.get("selectedMarketplaces")
        return {
            "selected_marketplaces": [
                item
                for item in selected[:_MAX_MARKETPLACES]
                if isinstance(item, str) and _SAFE_NAME.fullmatch(item)
            ]
            if isinstance(selected, list)
            else [],
            "error_count": min(
                len(result.get("errors", []))
                if isinstance(result.get("errors"), list)
                else 0,
                16,
            ),
        }

    def _project_skill(
        self, value: object, normalized_cwd: str
    ) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        name = value.get("name")
        if not isinstance(name, str) or len(name.encode("utf-8")) > _MAX_NAME_BYTES:
            return None
        safe_name = _safe_text(name, limit=_MAX_NAME_BYTES)
        if safe_name is None:
            return None
        path = value.get("path")
        public_path: str | None = None
        if isinstance(path, str):
            try:
                candidate = (
                    self.storage.workspace_boundary.relative_from_path(path)
                    if self.storage.workspace_boundary is not None
                    else None
                )
                if candidate is not None and (
                    normalized_cwd == "."
                    or candidate == normalized_cwd
                    or candidate.startswith(normalized_cwd.rstrip("/") + "/")
                ):
                    public_path = candidate
            except (WorkspaceBoundaryError, OSError, ValueError):
                public_path = None
        return {
            "name": safe_name,
            "description": _safe_text(value.get("description"), limit=2048) or "",
            "short_description": _safe_text(value.get("shortDescription"), limit=512),
            "enabled": value.get("enabled") is True,
            "scope": _safe_text(value.get("scope"), limit=32),
            "path": public_path,
        }

    def _project_marketplaces(self, values: object) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            return []
        output: list[dict[str, Any]] = []
        remaining_plugins = _MAX_PLUGINS
        for value in values[:_MAX_MARKETPLACES]:
            if not isinstance(value, dict):
                continue
            name = value.get("name")
            if not isinstance(name, str) or _SAFE_NAME.fullmatch(name) is None:
                continue
            plugins = value.get("plugins")
            projected_plugins: list[dict[str, Any]] = []
            if isinstance(plugins, list) and remaining_plugins:
                for plugin in plugins[:remaining_plugins]:
                    projected = self._project_plugin(plugin)
                    if projected is not None:
                        projected_plugins.append(projected)
            remaining_plugins -= len(projected_plugins)
            output.append({"name": name, "plugins": projected_plugins})
        return output

    def _project_plugin(self, value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        plugin_id = value.get("id")
        name = value.get("name")
        if not isinstance(plugin_id, str) or not isinstance(name, str):
            return None
        if (
            len(plugin_id.encode("utf-8")) > _MAX_NAME_BYTES
            or len(name.encode("utf-8")) > _MAX_NAME_BYTES
        ):
            return None
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", plugin_id, re.ASCII)
            is None
        ):
            return None
        safe_name = _safe_text(name, limit=_MAX_NAME_BYTES)
        if safe_name is None:
            return None
        return {
            "id": plugin_id,
            "name": safe_name,
            "description": _safe_text(
                (value.get("interface") or {}).get("shortDescription")
                if isinstance(value.get("interface"), dict)
                else None,
                limit=1024,
            ),
            "enabled": value.get("enabled") is True,
            "installed": value.get("installed") is True,
            "version": _safe_text(value.get("version"), limit=64),
            "local_version": _safe_text(value.get("localVersion"), limit=64),
            "marketplace_name": _safe_text(value.get("marketplaceName"), limit=128),
        }

    def _safe_marketplace_source(self, value: object) -> str:
        if not isinstance(value, str) or len(value.encode("utf-8")) > 512:
            raise CapabilitiesInvalidError("marketplace source is invalid")
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise CapabilitiesInvalidError("marketplace source is invalid")
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"}:
            raise CapabilitiesInvalidError("marketplace source is invalid")
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise CapabilitiesInvalidError("marketplace source is invalid")
        # This detects private DNS answers at submission time only. DNS can
        # rebind later, so production egress policy remains the final SSRF
        # boundary; failures are tolerated to keep DNS outages non-fatal.
        try:
            answers = self._resolver(host)
        except (OSError, ValueError):
            answers = ()
        for answer in answers:
            try:
                resolved = ip_address(answer)
            except ValueError:
                continue
            if not resolved.is_global:
                raise CapabilitiesInvalidError("marketplace source is invalid")
        return value

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        try:
            if timeout_seconds is None:
                return self.app_server.request(method, params)
            return self.app_server.request(
                method,
                params,
                timeout_seconds=timeout_seconds,
            )
        except (CodexAppServerError, RuntimeError, OSError, ValueError):
            raise CapabilitiesUnavailableError() from None

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        lease = None
        if self.runtime_gate is not None:
            try:
                lease = self.runtime_gate.acquire_config_mutation()
            except RuntimeGateError:
                raise CapabilitiesConflictError() from None
        try:
            yield
        finally:
            if lease is not None:
                lease.release()
