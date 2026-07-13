import json
import subprocess
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from typing import Any, TextIO

from . import __version__
from .codex_process import codex_command_prefix, codex_subprocess_environment
from .models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    SUPPORTED_MODELS,
    SUPPORTED_THINKING_LEVELS,
    CodexModelCatalogRecord,
    CodexModelRecord,
)


class ModelCatalogError(RuntimeError):
    pass


_MAX_MODEL_CATALOG_PAGES = 100


class AppServerModelCatalogProbe:
    """Catalog projection served by the application's single app-server."""

    def __init__(
        self,
        client: Any,
        *,
        cache_ttl_seconds: float = 600.0,
        timeout_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("model catalogue timeout must be positive")
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._cached: CodexModelCatalogRecord | None = None
        self._cached_at = 0.0
        self._generation: int | None = None

    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        with self._lock:
            generation = getattr(self._client, "generation", None)
            now = monotonic()
            if (
                self._cached is not None
                and generation == self._generation
                and now - self._cached_at < self._cache_ttl_seconds
                and (not refresh_stale or not self._cached.stale)
            ):
                return self._cached
            try:
                deadline = monotonic() + self._timeout_seconds
                config = self._request(
                    "config/read",
                    {"includeLayers": False},
                    deadline=deadline,
                )
                cursor: str | None = None
                data: list[Any] = []
                seen: set[str] = set()
                for _page_number in range(_MAX_MODEL_CATALOG_PAGES):
                    params: dict[str, Any] = {"includeHidden": False, "limit": 100}
                    if cursor is not None:
                        params["cursor"] = cursor
                    page = self._request("model/list", params, deadline=deadline)
                    if not isinstance(page, dict):
                        raise ModelCatalogError(
                            "Codex app-server returned an invalid model catalogue"
                        )
                    data.extend(CodexModelCatalogProbe._model_list_items(page))
                    next_cursor = page.get("nextCursor")
                    if (
                        not isinstance(next_cursor, str)
                        or not next_cursor
                        or next_cursor in seen
                    ):
                        break
                    seen.add(next_cursor)
                    cursor = next_cursor
                else:
                    raise ModelCatalogError(
                        "Codex app-server returned too many model catalogue pages"
                    )
                if not isinstance(config, dict):
                    raise ModelCatalogError(
                        "Codex app-server returned an invalid model catalogue"
                    )
                if getattr(self._client, "generation", None) != generation:
                    raise ModelCatalogError(
                        "Codex app-server generation changed during model discovery"
                    )
                result = CodexModelCatalogProbe._build_catalog(config, {"data": data})
            except (ModelCatalogError, OSError, RuntimeError, ValueError) as exc:
                if (
                    self._cached is not None
                    and self._cached.models
                    and self._cached.source != "fallback"
                ):
                    result = self._cached.model_copy(
                        update={
                            "source": "last-known-good",
                            "stale": True,
                            "error": CodexModelCatalogProbe._error_message(exc),
                        }
                    )
                else:
                    result = CodexModelCatalogProbe._fallback_catalog(exc)
            self._cached = result
            self._cached_at = now
            self._generation = generation if type(generation) is int else None
            return result

    def _request(self, method: str, params: dict[str, Any], *, deadline: float) -> Any:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ModelCatalogError("Codex model discovery timed out")
        return self._client.request(
            method,
            params,
            timeout_seconds=remaining,
        )


class CodexModelCatalogProbe:
    def __init__(
        self,
        codex_command: str = "codex",
        *,
        codex_home: Path | str | None = None,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: float = 600.0,
    ) -> None:
        self.codex_command = codex_command
        self.codex_home = Path(codex_home) if codex_home is not None else None
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache_lock = Lock()
        self._cached_catalog: CodexModelCatalogRecord | None = None
        self._cached_at: float | None = None

    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        requested_at = monotonic()
        with self._cache_lock:
            now = monotonic()
            if (
                self._cached_catalog is not None
                and self._cached_at is not None
                and (
                    self._cached_at >= requested_at
                    or (
                        now - self._cached_at < self.cache_ttl_seconds
                        and (not refresh_stale or not self._cached_catalog.stale)
                    )
                )
            ):
                return self._cached_catalog
            try:
                catalog = self._discover_from_app_server()
            except (ModelCatalogError, OSError, ValueError) as exc:
                if (
                    self._cached_catalog is not None
                    and self._cached_catalog.models
                    and self._cached_catalog.source != "fallback"
                ):
                    catalog = self._cached_catalog.model_copy(
                        update={
                            "source": "last-known-good",
                            "stale": True,
                            "error": self._error_message(exc),
                        }
                    )
                else:
                    catalog = self._fallback_catalog(exc)
            self._cached_catalog = catalog
            self._cached_at = monotonic()
            return catalog

    @staticmethod
    def _fallback_catalog(error: Exception) -> CodexModelCatalogRecord:
        return CodexModelCatalogRecord(
            source="fallback",
            models=[
                CodexModelRecord(
                    model=model,
                    display_name=model,
                    is_default=model == DEFAULT_MODEL,
                    default_thinking_level=DEFAULT_THINKING_LEVEL,
                    thinking_levels=list(SUPPORTED_THINKING_LEVELS),
                )
                for model in SUPPORTED_MODELS
            ],
            default_model=DEFAULT_MODEL,
            default_thinking_level=DEFAULT_THINKING_LEVEL,
            refreshed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            stale=True,
            error=CodexModelCatalogProbe._error_message(error),
        )

    @staticmethod
    def _error_message(_error: Exception) -> str:
        # Exceptions may contain command lines, paths, remote payloads, or
        # credentials. The catalogue is returned through status APIs, so only
        # expose a stable recovery category.
        return "Codex model discovery is temporarily unavailable."

    def _discover_from_app_server(self) -> CodexModelCatalogRecord:
        environment = codex_subprocess_environment(self.codex_home)
        process = subprocess.Popen(
            [*self._command_prefix(), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise ModelCatalogError("Codex app-server did not expose stdio")

        messages: Queue[dict[str, Any] | None] = Queue()
        Thread(target=self._read_messages, args=(process.stdout, messages), daemon=True).start()
        deadline = monotonic() + self.timeout_seconds
        try:
            self._send(
                process.stdin,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "ha-codex-bridge", "version": __version__},
                        "capabilities": {},
                    },
                },
            )
            self._wait_for_response(messages, 1, deadline)
            self._send(process.stdin, {"method": "initialized"})
            self._send(
                process.stdin,
                {"id": 2, "method": "config/read", "params": {"includeLayers": False}},
            )
            config_result = self._wait_for_response(messages, 2, deadline)
            request_id = 3
            cursor: str | None = None
            raw_models: list[Any] = []
            seen_cursors: set[str] = set()
            for _page_number in range(_MAX_MODEL_CATALOG_PAGES):
                params: dict[str, Any] = {"includeHidden": False, "limit": 100}
                if cursor is not None:
                    params["cursor"] = cursor
                self._send(
                    process.stdin,
                    {"id": request_id, "method": "model/list", "params": params},
                )
                page = self._wait_for_response(messages, request_id, deadline)
                page_models = self._model_list_items(page)
                raw_models.extend(page_models)
                next_cursor = page.get("nextCursor")
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                request_id += 1
            else:
                raise ModelCatalogError(
                    "Codex app-server returned too many model catalogue pages"
                )
            models_result = {"data": raw_models}
            return self._build_catalog(config_result, models_result)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _command_prefix(self) -> list[str]:
        return codex_command_prefix(self.codex_command)

    @staticmethod
    def _read_messages(stream: TextIO, messages: Queue[dict[str, Any] | None]) -> None:
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    messages.put(message)
        finally:
            messages.put(None)

    @staticmethod
    def _send(stream: TextIO, message: dict[str, Any]) -> None:
        stream.write(json.dumps(message, separators=(",", ":")) + "\n")
        stream.flush()

    @staticmethod
    def _wait_for_response(
        messages: Queue[dict[str, Any] | None],
        request_id: int,
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ModelCatalogError("Codex model discovery timed out")
            try:
                message = messages.get(timeout=remaining)
            except Empty as exc:
                raise ModelCatalogError("Codex model discovery timed out") from exc
            if message is None:
                raise ModelCatalogError("Codex app-server closed before model discovery completed")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ModelCatalogError(f"Codex app-server rejected {request_id}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ModelCatalogError("Codex app-server returned an invalid response")
            return result

    @staticmethod
    def _model_list_items(result: dict[str, Any]) -> list[Any]:
        for field in ("data", "items"):
            items = result.get(field)
            if isinstance(items, list):
                return items
        raise ModelCatalogError("Codex app-server returned no model list")

    @staticmethod
    def _reasoning_efforts(raw_options: Any) -> list[str]:
        if not isinstance(raw_options, list):
            return []
        efforts: list[str] = []
        seen_efforts: set[str] = set()
        for option in raw_options:
            effort: Any
            if isinstance(option, str):
                effort = option
            elif isinstance(option, dict):
                effort = option.get("reasoningEffort")
            else:
                continue
            if not isinstance(effort, str):
                continue
            effort = effort.strip()
            if not effort or effort in seen_efforts:
                continue
            seen_efforts.add(effort)
            efforts.append(effort)
        return efforts

    @staticmethod
    def _build_catalog(
        config_result: dict[str, Any],
        models_result: dict[str, Any],
    ) -> CodexModelCatalogRecord:
        config = config_result.get("config")
        if not isinstance(config, dict):
            config = {}
        configured_model = config.get("model") if isinstance(config.get("model"), str) else None
        configured_thinking = (
            config.get("model_reasoning_effort")
            if isinstance(config.get("model_reasoning_effort"), str)
            else None
        )
        raw_models = CodexModelCatalogProbe._model_list_items(models_result)

        models: list[CodexModelRecord] = []
        seen_models: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or raw_model.get("hidden") is True:
                continue
            model = raw_model.get("model")
            if not isinstance(model, str) or not model.strip():
                continue
            model = model.strip()
            if model in seen_models:
                continue
            seen_models.add(model)
            thinking_levels = CodexModelCatalogProbe._reasoning_efforts(
                raw_model.get("supportedReasoningEfforts")
            )
            if (
                model == configured_model
                and configured_thinking
                and configured_thinking not in thinking_levels
            ):
                thinking_levels.append(configured_thinking)
            advertised_default = raw_model.get("defaultReasoningEffort")
            if not isinstance(advertised_default, str) or not advertised_default.strip():
                advertised_default = None
            default_thinking_level = (
                advertised_default
                or (configured_thinking if model == configured_model else None)
                or ("medium" if "medium" in thinking_levels else None)
                or (thinking_levels[0] if thinking_levels else "medium")
            )
            input_modalities = raw_model.get("inputModalities")
            if not isinstance(input_modalities, list):
                input_modalities = []
            models.append(
                CodexModelRecord(
                    model=model,
                    display_name=str(raw_model.get("displayName") or model),
                    description=(
                        raw_model.get("description")
                        if isinstance(raw_model.get("description"), str)
                        else None
                    ),
                    is_default=(
                        model == configured_model
                        if configured_model is not None
                        else bool(raw_model.get("isDefault"))
                    ),
                    default_thinking_level=default_thinking_level,
                    thinking_levels=thinking_levels,
                    input_modalities=[
                        modality
                        for modality in input_modalities
                        if isinstance(modality, str)
                    ],
                )
            )
        if not models:
            raise ModelCatalogError("Codex app-server returned an empty model list")

        if configured_model and all(model.model != configured_model for model in models):
            models.insert(
                0,
                CodexModelRecord(
                    model=configured_model,
                    display_name=configured_model,
                    description="Model configured in Codex but not present in the current picker catalogue.",
                    is_default=True,
                    default_thinking_level=configured_thinking or "medium",
                    thinking_levels=[configured_thinking] if configured_thinking else [],
                    catalogued=False,
                ),
            )

        default_model = configured_model or next(
            (model.model for model in models if model.is_default),
            models[0].model,
        )
        default_record = next((model for model in models if model.model == default_model), models[0])
        return CodexModelCatalogRecord(
            source="codex-app-server",
            models=models,
            default_model=default_model,
            default_thinking_level=configured_thinking or default_record.default_thinking_level,
            configured_model=configured_model,
            configured_thinking_level=configured_thinking,
            refreshed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
