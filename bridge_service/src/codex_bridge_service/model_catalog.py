import json
import shutil
import subprocess
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
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
_MAX_MODEL_CATALOG_MODELS = 100
_MAX_MODEL_CATALOG_BYTES = 4 * 1_048_576
_MAX_APP_SERVER_MESSAGE_CHARS = 1_048_576
_MAX_PENDING_APP_SERVER_MESSAGES = 128
_MAX_BUNDLED_CATALOG_BYTES = 1_048_576
_MAX_BUNDLED_CATALOG_MODELS = 500
_MAX_MODEL_ID_CHARS = 200
_MAX_MODEL_DISPLAY_NAME_CHARS = 200
_MAX_MODEL_DESCRIPTION_CHARS = 1_000
_MAX_REASONING_LEVELS = 16
_MAX_REASONING_LEVEL_CHARS = 64
_MAX_INPUT_MODALITIES = 16
_MAX_INPUT_MODALITY_CHARS = 64
_DEFAULT_STALE_RETRY_TTL_SECONDS = 15.0


def _validate_positive_finite(value: object, *, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be positive")


def _validate_nonnegative_finite(value: object, *, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be non-negative")


class AppServerModelCatalogProbe:
    """Catalog projection served by the application's single app-server."""

    def __init__(
        self,
        client: Any,
        *,
        cache_ttl_seconds: float = 600.0,
        timeout_seconds: float = 5.0,
        codex_command: str = "codex",
        codex_home: Path | str | None = None,
        stale_retry_ttl_seconds: float = _DEFAULT_STALE_RETRY_TTL_SECONDS,
    ) -> None:
        _validate_positive_finite(
            timeout_seconds, name="model catalogue timeout"
        )
        _validate_nonnegative_finite(
            cache_ttl_seconds, name="model catalogue cache TTL"
        )
        _validate_positive_finite(
            stale_retry_ttl_seconds,
            name="model catalogue stale retry TTL",
        )
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._stale_retry_ttl_seconds = stale_retry_ttl_seconds
        self._bundled_probe = CodexModelCatalogProbe(
            codex_command=codex_command,
            codex_home=codex_home,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            stale_retry_ttl_seconds=stale_retry_ttl_seconds,
        )
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
                and now - self._cached_at
                < (
                    self._stale_retry_ttl_seconds
                    if self._cached.stale
                    else self._cache_ttl_seconds
                )
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
                aggregate_bytes = 0
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
                    page_items = CodexModelCatalogProbe._model_list_items(page)
                    aggregate_bytes += CodexModelCatalogProbe._catalog_page_size(page)
                    if (
                        aggregate_bytes > _MAX_MODEL_CATALOG_BYTES
                        or len(data) + len(page_items) > _MAX_MODEL_CATALOG_MODELS
                    ):
                        raise ModelCatalogError(
                            "Codex app-server returned an oversized model catalogue"
                        )
                    data.extend(page_items)
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
            except (
                ModelCatalogError,
                OSError,
                RuntimeError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                if (
                    self._cached is not None
                    and self._cached.models
                    and self._cached.source
                    in {"codex-app-server", "last-known-good"}
                ):
                    result = self._cached.model_copy(
                        update={
                            "source": "last-known-good",
                            "stale": True,
                            "error": CodexModelCatalogProbe._error_message(exc),
                        }
                    )
                else:
                    try:
                        result = self._bundled_probe._discover_from_bundled()
                        result = result.model_copy(
                            update={
                                "error": CodexModelCatalogProbe._bundled_error_message()
                            }
                        )
                    except (
                        ModelCatalogError,
                        OSError,
                        ValueError,
                        subprocess.SubprocessError,
                    ):
                        result = CodexModelCatalogProbe._fallback_catalog(exc)
            self._cached = result
            self._cached_at = monotonic()
            self._generation = generation if type(generation) is int else None
            return result

    def invalidate(self) -> None:
        """Expire the catalogue while retaining it as a last-known-good fallback."""

        with self._lock:
            self._cached_at = 0.0
            self._generation = None

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
        stale_retry_ttl_seconds: float = _DEFAULT_STALE_RETRY_TTL_SECONDS,
    ) -> None:
        _validate_positive_finite(
            timeout_seconds, name="model catalogue timeout"
        )
        _validate_nonnegative_finite(
            cache_ttl_seconds, name="model catalogue cache TTL"
        )
        _validate_positive_finite(
            stale_retry_ttl_seconds,
            name="model catalogue stale retry TTL",
        )
        self.codex_command = codex_command
        self.codex_home = Path(codex_home) if codex_home is not None else None
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stale_retry_ttl_seconds = stale_retry_ttl_seconds
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
                        now - self._cached_at
                        < (
                            self.stale_retry_ttl_seconds
                            if self._cached_catalog.stale
                            else self.cache_ttl_seconds
                        )
                        and (not refresh_stale or not self._cached_catalog.stale)
                    )
                )
            ):
                return self._cached_catalog
            try:
                catalog = self._discover_from_app_server()
            except (
                ModelCatalogError,
                OSError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                if (
                    self._cached_catalog is not None
                    and self._cached_catalog.models
                    and self._cached_catalog.source
                    in {"codex-app-server", "last-known-good"}
                ):
                    catalog = self._cached_catalog.model_copy(
                        update={
                            "source": "last-known-good",
                            "stale": True,
                            "error": self._error_message(exc),
                        }
                    )
                else:
                    try:
                        catalog = self._discover_from_bundled()
                        catalog = catalog.model_copy(
                            update={"error": self._bundled_error_message()}
                        )
                    except (
                        ModelCatalogError,
                        OSError,
                        ValueError,
                        subprocess.SubprocessError,
                    ):
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

    @staticmethod
    def _bundled_error_message() -> str:
        return (
            "Live Codex model discovery is temporarily unavailable; "
            "using the bundled catalogue."
        )

    def _discover_from_bundled(self) -> CodexModelCatalogRecord:
        """Read the signed CLI's local catalogue without network access."""

        command_prefix = self._command_prefix()
        target = Path(self.codex_command)
        if target.is_absolute() or target.parent != Path("."):
            if not target.is_file():
                raise ModelCatalogError("Codex bundled command is unavailable")
        elif shutil.which(command_prefix[0]) is None:
            raise ModelCatalogError("Codex bundled command is unavailable")
        environment = codex_subprocess_environment(self.codex_home)
        process = subprocess.Popen(
            [*command_prefix, "debug", "models", "--bundled"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            text=False,
        )
        if process.stdout is None:
            raise ModelCatalogError("Codex bundled catalogue did not expose stdout")
        deadline = monotonic() + min(self.timeout_seconds, 2.0)
        try:
            output = self._read_bounded_stdout(process, deadline)
            remaining = deadline - monotonic()
            if process.poll() is None:
                if remaining <= 0:
                    raise ModelCatalogError("Codex bundled model discovery timed out")
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    raise ModelCatalogError(
                        "Codex bundled model discovery timed out"
                    ) from exc
            if process.returncode != 0:
                raise ModelCatalogError("Codex bundled model discovery failed")
            if (
                not isinstance(output, bytes)
                or len(output) > _MAX_BUNDLED_CATALOG_BYTES
            ):
                raise ModelCatalogError(
                    "Codex bundled catalogue exceeded its size limit"
                )
            try:
                payload = json.loads(output.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ModelCatalogError("Codex bundled catalogue was invalid") from exc
            return self._build_bundled_catalog(payload)
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _read_bounded_stdout(process: Any, deadline: float) -> bytes:
        stream = process.stdout
        if stream is None:
            raise ModelCatalogError("Codex bundled catalogue did not expose stdout")
        chunks: list[bytes] = []
        messages: Queue[bytes | Exception | None] = Queue(maxsize=1)

        def read_stream() -> None:
            total = 0
            try:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        messages.put(b"".join(chunks))
                        return
                    if not isinstance(chunk, bytes):
                        messages.put(
                            ModelCatalogError("Codex bundled catalogue was invalid")
                        )
                        return
                    total += len(chunk)
                    if total > _MAX_BUNDLED_CATALOG_BYTES:
                        messages.put(
                            ModelCatalogError(
                                "Codex bundled catalogue exceeded its size limit"
                            )
                        )
                        return
                    chunks.append(chunk)
            except Exception:  # pragma: no cover - OS pipe failures vary
                messages.put(
                    ModelCatalogError("Codex bundled catalogue was unavailable")
                )

        Thread(target=read_stream, daemon=True).start()
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ModelCatalogError("Codex bundled model discovery timed out")
            try:
                result = messages.get(timeout=min(remaining, 0.1))
            except Empty:
                continue
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise ModelCatalogError("Codex bundled catalogue was unavailable")
            return result

    @classmethod
    def _build_bundled_catalog(cls, payload: Any) -> CodexModelCatalogRecord:
        if not isinstance(payload, dict):
            raise ModelCatalogError("Codex bundled catalogue was invalid")
        raw_models = payload.get("models")
        if (
            not isinstance(raw_models, list)
            or len(raw_models) > _MAX_BUNDLED_CATALOG_MODELS
        ):
            raise ModelCatalogError("Codex bundled catalogue was invalid")
        models: list[CodexModelRecord] = []
        seen_models: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or raw_model.get("visibility") != "list":
                continue
            model = cls._bounded_text(
                raw_model.get("slug"),
                max_chars=_MAX_MODEL_ID_CHARS,
            )
            if model is None:
                continue
            if model in seen_models:
                continue
            seen_models.add(model)
            thinking_levels: list[str] = []
            raw_efforts = raw_model.get("supported_reasoning_levels")
            if isinstance(raw_efforts, list):
                for raw_effort in raw_efforts:
                    effort = (
                        raw_effort.get("effort")
                        if isinstance(raw_effort, dict)
                        else None
                    )
                    effort = cls._bounded_text(
                        effort,
                        max_chars=_MAX_REASONING_LEVEL_CHARS,
                    )
                    if effort is not None and effort not in thinking_levels:
                        thinking_levels.append(effort)
                        if len(thinking_levels) == _MAX_REASONING_LEVELS:
                            break
            advertised_default = cls._bounded_text(
                raw_model.get("default_reasoning_level"),
                max_chars=_MAX_REASONING_LEVEL_CHARS,
            )
            if advertised_default not in thinking_levels:
                advertised_default = None
            default_thinking_level = (
                advertised_default
                or ("medium" if "medium" in thinking_levels else None)
                or (thinking_levels[0] if thinking_levels else "medium")
            )
            display_name = cls._bounded_text(
                raw_model.get("display_name"),
                max_chars=_MAX_MODEL_DISPLAY_NAME_CHARS,
                fallback=model,
            )
            description = cls._bounded_text(
                raw_model.get("description"),
                max_chars=_MAX_MODEL_DESCRIPTION_CHARS,
            )
            input_modalities = cls._bounded_text_list(
                raw_model.get("input_modalities"),
                max_items=_MAX_INPUT_MODALITIES,
                max_chars=_MAX_INPUT_MODALITY_CHARS,
            )
            if len(models) == _MAX_MODEL_CATALOG_MODELS:
                raise ModelCatalogError(
                    "Codex bundled catalogue contained too many visible models"
                )
            models.append(
                CodexModelRecord(
                    model=model,
                    display_name=display_name or model,
                    description=description,
                    is_default=not models,
                    default_thinking_level=default_thinking_level,
                    thinking_levels=thinking_levels,
                    input_modalities=input_modalities,
                )
            )
        if not models:
            raise ModelCatalogError(
                "Codex bundled catalogue contained no visible models"
            )
        default = models[0]
        return CodexModelCatalogRecord(
            source="codex-bundled",
            models=models,
            default_model=default.model,
            default_thinking_level=default.default_thinking_level,
            refreshed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            stale=True,
        )

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

        messages: Queue[dict[str, Any] | Exception | None] = Queue(
            maxsize=_MAX_PENDING_APP_SERVER_MESSAGES
        )
        Thread(
            target=self._read_messages, args=(process.stdout, messages), daemon=True
        ).start()
        deadline = monotonic() + self.timeout_seconds
        try:
            self._send(
                process.stdin,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "ha-codex-bridge",
                            "version": __version__,
                        },
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
            aggregate_bytes = 0
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
                aggregate_bytes += self._catalog_page_size(page)
                if (
                    aggregate_bytes > _MAX_MODEL_CATALOG_BYTES
                    or len(raw_models) + len(page_models)
                    > _MAX_MODEL_CATALOG_MODELS
                ):
                    raise ModelCatalogError(
                        "Codex app-server returned an oversized model catalogue"
                    )
                raw_models.extend(page_models)
                next_cursor = page.get("nextCursor")
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or next_cursor in seen_cursors
                ):
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
    def _read_messages(
        stream: TextIO,
        messages: Queue[dict[str, Any] | Exception | None],
    ) -> None:
        try:
            while True:
                line = stream.readline(_MAX_APP_SERVER_MESSAGE_CHARS + 1)
                if not line:
                    break
                if len(line) > _MAX_APP_SERVER_MESSAGE_CHARS:
                    CodexModelCatalogProbe._queue_reader_message(
                        messages,
                        ModelCatalogError(
                            "Codex app-server response exceeded its size limit"
                        ),
                    )
                    return
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and "id" in message:
                    CodexModelCatalogProbe._queue_reader_message(messages, message)
        finally:
            CodexModelCatalogProbe._queue_reader_message(messages, None)

    @staticmethod
    def _queue_reader_message(
        messages: Queue[dict[str, Any] | Exception | None],
        message: dict[str, Any] | Exception | None,
    ) -> None:
        """Append without blocking, evicting the oldest hostile response."""

        while True:
            try:
                messages.put_nowait(message)
                return
            except Full:
                try:
                    messages.get_nowait()
                except Empty:
                    continue

    @staticmethod
    def _send(stream: TextIO, message: dict[str, Any]) -> None:
        stream.write(json.dumps(message, separators=(",", ":")) + "\n")
        stream.flush()

    @staticmethod
    def _wait_for_response(
        messages: Queue[dict[str, Any] | Exception | None],
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
                raise ModelCatalogError(
                    "Codex app-server closed before model discovery completed"
                )
            if isinstance(message, Exception):
                raise message
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
    def _catalog_page_size(page: dict[str, Any]) -> int:
        try:
            return len(
                json.dumps(
                    page,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ModelCatalogError(
                "Codex app-server returned an invalid model catalogue"
            ) from exc

    @staticmethod
    def _bounded_text(
        value: Any,
        *,
        max_chars: int,
        fallback: str | None = None,
    ) -> str | None:
        if not isinstance(value, str):
            return fallback
        normalized = value.strip()
        if not normalized or len(normalized) > max_chars:
            return fallback
        return normalized

    @classmethod
    def _bounded_text_list(
        cls,
        values: Any,
        *,
        max_items: int,
        max_chars: int,
    ) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = cls._bounded_text(value, max_chars=max_chars)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
            if len(result) == max_items:
                break
        return result

    @classmethod
    def _reasoning_efforts(cls, raw_options: Any) -> list[str]:
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
            effort = cls._bounded_text(
                effort,
                max_chars=_MAX_REASONING_LEVEL_CHARS,
            )
            if effort is None or effort in seen_efforts:
                continue
            seen_efforts.add(effort)
            efforts.append(effort)
            if len(efforts) == _MAX_REASONING_LEVELS:
                break
        return efforts

    @staticmethod
    def _build_catalog(
        config_result: dict[str, Any],
        models_result: dict[str, Any],
    ) -> CodexModelCatalogRecord:
        config = config_result.get("config")
        if not isinstance(config, dict):
            config = {}
        configured_model = CodexModelCatalogProbe._bounded_text(
            config.get("model"),
            max_chars=_MAX_MODEL_ID_CHARS,
        )
        configured_thinking = CodexModelCatalogProbe._bounded_text(
            config.get("model_reasoning_effort"),
            max_chars=_MAX_REASONING_LEVEL_CHARS,
        )
        raw_models = CodexModelCatalogProbe._model_list_items(models_result)

        models: list[CodexModelRecord] = []
        seen_models: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or raw_model.get("hidden") is True:
                continue
            model = CodexModelCatalogProbe._bounded_text(
                raw_model.get("model"),
                max_chars=_MAX_MODEL_ID_CHARS,
            )
            if model is None:
                continue
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
                and len(thinking_levels) < _MAX_REASONING_LEVELS
            ):
                thinking_levels.append(configured_thinking)
            advertised_default = CodexModelCatalogProbe._bounded_text(
                raw_model.get("defaultReasoningEffort"),
                max_chars=_MAX_REASONING_LEVEL_CHARS,
            )
            if advertised_default not in thinking_levels:
                advertised_default = None
            default_thinking_level = (
                advertised_default
                or (configured_thinking if model == configured_model else None)
                or ("medium" if "medium" in thinking_levels else None)
                or (thinking_levels[0] if thinking_levels else "medium")
            )
            display_name = CodexModelCatalogProbe._bounded_text(
                raw_model.get("displayName"),
                max_chars=_MAX_MODEL_DISPLAY_NAME_CHARS,
                fallback=model,
            )
            description = CodexModelCatalogProbe._bounded_text(
                raw_model.get("description"),
                max_chars=_MAX_MODEL_DESCRIPTION_CHARS,
            )
            input_modalities = CodexModelCatalogProbe._bounded_text_list(
                raw_model.get("inputModalities"),
                max_items=_MAX_INPUT_MODALITIES,
                max_chars=_MAX_INPUT_MODALITY_CHARS,
            )
            models.append(
                CodexModelRecord(
                    model=model,
                    display_name=display_name or model,
                    description=description,
                    is_default=(
                        model == configured_model
                        if configured_model is not None
                        else bool(raw_model.get("isDefault"))
                    ),
                    default_thinking_level=default_thinking_level,
                    thinking_levels=thinking_levels,
                    input_modalities=input_modalities,
                )
            )
        if not models:
            raise ModelCatalogError("Codex app-server returned an empty model list")

        if configured_model and all(
            model.model != configured_model for model in models
        ):
            models.insert(
                0,
                CodexModelRecord(
                    model=configured_model,
                    display_name=configured_model,
                    description="Model configured in Codex but not present in the current picker catalogue.",
                    is_default=True,
                    default_thinking_level=configured_thinking or "medium",
                    thinking_levels=[configured_thinking]
                    if configured_thinking
                    else [],
                    catalogued=False,
                ),
            )

        default_model = configured_model or next(
            (model.model for model in models if model.is_default),
            models[0].model,
        )
        default_record = next(
            (model for model in models if model.model == default_model), models[0]
        )
        return CodexModelCatalogRecord(
            source="codex-app-server",
            models=models,
            default_model=default_model,
            default_thinking_level=configured_thinking
            or default_record.default_thinking_level,
            configured_model=configured_model,
            configured_thinking_level=configured_thinking,
            refreshed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
