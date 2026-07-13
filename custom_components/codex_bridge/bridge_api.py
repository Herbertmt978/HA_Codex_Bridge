import asyncio
import hmac
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from .const import (
    API_CURRENT,
    BRIDGE_API_HEADER,
    BRIDGE_EVENT_BATCH_LIMIT,
    BRIDGE_EVENT_BATCH_MAX_BYTES,
    BRIDGE_EVENT_CURSOR_MAX,
    BRIDGE_EVENT_WAIT_SECONDS,
    BRIDGE_PROBLEM_BODY_MAX_BYTES,
    BRIDGE_TIMEOUT_CONNECT_SECONDS,
    BRIDGE_TIMEOUT_POOL_SECONDS,
    BRIDGE_TIMEOUT_READ_SECONDS,
    BRIDGE_TIMEOUT_TOTAL_SECONDS,
    BRIDGE_TIMEOUT_WRITE_SECONDS,
)
from .protocol import (
    ApiIncompatibleError,
    ApiRange,
    DiscoveryRecord,
    EndpointError,
    ProblemRecord,
    ReadyRecord,
    validate_bridge_identifier,
    validate_bridge_token,
    validate_bridge_url,
)


# ``connect`` covers time spent waiting for a pooled connection as well as the
# TCP handshake. aiohttp has no separate write timer; the bounded total timer
# therefore also bounds request-body writes.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(
    total=BRIDGE_TIMEOUT_TOTAL_SECONDS,
    connect=BRIDGE_TIMEOUT_POOL_SECONDS,
    sock_connect=BRIDGE_TIMEOUT_CONNECT_SECONDS,
    sock_read=BRIDGE_TIMEOUT_READ_SECONDS,
)
WRITE_TIMEOUT_SECONDS = BRIDGE_TIMEOUT_WRITE_SECONDS


def _path_segment(value: object) -> str:
    try:
        return quote(validate_bridge_identifier(value), safe="")
    except EndpointError:
        raise BridgeApiEndpointError() from None


def _request_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "#" in value
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BridgeApiEndpointError()
    return value


def _nonnegative_cursor(value: object) -> int:
    if type(value) is not int or not 0 <= value <= BRIDGE_EVENT_CURSOR_MAX:
        raise BridgeApiEndpointError("cursor_invalid")
    return value


def _client_request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BridgeApiEndpointError("client_request_id_invalid")
    return value


def _interaction_answers(value: object) -> list[dict[str, Any]]:
    """Validate and copy the bounded public user-input answer contract."""

    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise BridgeApiEndpointError("answers_invalid")
    answers: list[dict[str, Any]] = []
    question_ids: set[str] = set()
    for answer in value:
        if not isinstance(answer, Mapping) or set(answer) != {"question_id", "values"}:
            raise BridgeApiEndpointError("answers_invalid")
        try:
            question_id = validate_bridge_identifier(answer["question_id"])
        except EndpointError:
            raise BridgeApiEndpointError("answers_invalid") from None
        values = answer["values"]
        if (
            question_id in question_ids
            or not isinstance(values, list)
            or not 1 <= len(values) <= 32
            or any(
                not isinstance(item, str)
                or not 1 <= len(item) <= 4096
                or "\x00" in item
                for item in values
            )
        ):
            raise BridgeApiEndpointError("answers_invalid")
        question_ids.add(question_id)
        answers.append({"question_id": question_id, "values": list(values)})
    return answers


class BridgeApiError(RuntimeError):
    code = "bridge_error"
    retryable = False

    def __init__(
        self,
        code: str | None = None,
        *,
        status: int | None = None,
        retryable: bool | None = None,
        problem: ProblemRecord | None = None,
    ) -> None:
        if problem is not None:
            self.code = problem.code
            self.retryable = problem.retryable
            self.status = problem.status
        else:
            if code is not None:
                self.code = code
            if retryable is not None:
                self.retryable = retryable
            self.status = status
        self.problem = problem
        super().__init__(self.code.replace("_", " "))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, status={self.status!r}, "
            f"retryable={self.retryable!r}, problem={self.problem!r})"
        )


class BridgeApiConnectionError(BridgeApiError):
    code = "connection_failed"
    retryable = True


class BridgeApiAuthError(BridgeApiError):
    code = "authentication_failed"


class BridgeApiTimeoutError(BridgeApiConnectionError):
    code = "request_timed_out"


class BridgeApiConnectTimeoutError(BridgeApiTimeoutError):
    code = "connect_timed_out"


class BridgeApiReadTimeoutError(BridgeApiTimeoutError):
    code = "read_timed_out"


class BridgeApiRedirectError(BridgeApiError):
    code = "redirect_refused"


class BridgeApiIncompatibleError(BridgeApiError):
    code = "api_incompatible"


class BridgeApiCapabilityError(BridgeApiIncompatibleError):
    code = "capability_unavailable"


class BridgeApiConflictError(BridgeApiError):
    code = "conflict"


class BridgeApiGoneError(BridgeApiError):
    code = "resource_gone"


class BridgeApiPayloadTooLargeError(BridgeApiError):
    code = "payload_too_large"


class BridgeApiRangeNotSatisfiableError(BridgeApiError):
    code = "range_not_satisfiable"


class BridgeApiProblemError(BridgeApiError):
    code = "bridge_problem"


class BridgeApiEndpointError(BridgeApiError):
    code = "endpoint_invalid"


@dataclass(slots=True)
class BridgeDownload:
    """Legacy buffered download result; contents are deliberately not repr'd."""

    content: bytes = field(repr=False)
    content_type: str = field(repr=False)


class BridgeStreamResponse:
    """Bounded-read facade that maps stream failures to safe Bridge errors."""

    __slots__ = ("_response",)

    def __init__(self, response: aiohttp.ClientResponse) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    def closed(self) -> bool:
        return self._response.closed

    async def read_chunk(self, maximum_bytes: int) -> bytes:
        if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 1024 * 1024:
            raise BridgeApiEndpointError("chunk_size_invalid")
        try:
            return await self._response.content.read(maximum_bytes)
        except aiohttp.SocketTimeoutError:
            raise BridgeApiReadTimeoutError() from None
        except asyncio.TimeoutError:
            raise BridgeApiTimeoutError() from None
        except (aiohttp.ClientError, asyncio.IncompleteReadError):
            raise BridgeApiConnectionError() from None

    async def iter_chunked(self, chunk_bytes: int) -> AsyncIterator[bytes]:
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1024 * 1024:
            raise BridgeApiEndpointError("chunk_size_invalid")
        try:
            async for chunk in self._response.content.iter_chunked(chunk_bytes):
                yield chunk
        except aiohttp.SocketTimeoutError:
            raise BridgeApiReadTimeoutError() from None
        except asyncio.TimeoutError:
            raise BridgeApiTimeoutError() from None
        except (aiohttp.ClientError, asyncio.IncompleteReadError):
            raise BridgeApiConnectionError() from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status!r}, closed={self.closed!r})"


class BridgeApiClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
        *,
        allow_legacy_v0: bool = False,
    ) -> None:
        self._session = session
        try:
            self._base_url = validate_bridge_url(base_url)
            self._token = validate_bridge_token(token)
        except EndpointError:
            raise BridgeApiEndpointError() from None
        self._allow_legacy_v0 = allow_legacy_v0
        self._api_version: int | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def negotiated_api_version(self) -> int | None:
        return self._api_version

    @property
    def supports_api_v1(self) -> bool:
        return self._api_version == API_CURRENT

    @property
    def supports_legacy_v0(self) -> bool:
        return self._allow_legacy_v0 and self._api_version == 0

    def require_api_v1(self) -> None:
        """Fail before invoking a v1-only capability on a legacy Bridge."""

        if not self.supports_api_v1:
            raise BridgeApiCapabilityError()

    def require_legacy_v0(self) -> None:
        """Fail before invoking a compatibility-only v0 operation."""

        if not self.supports_legacy_v0:
            raise BridgeApiCapabilityError("legacy_transport_unavailable")

    async def async_health(self) -> dict[str, Any]:
        return await self._async_json("GET", "/health")

    async def async_ready(
        self,
        *,
        discovery: DiscoveryRecord | None = None,
        discovery_api: ApiRange | None = None,
    ) -> ReadyRecord:
        self._api_version = None
        payload = await self._async_json("GET", "/ready")
        try:
            ready = ReadyRecord.from_payload(
                payload, allow_legacy_v0=self._allow_legacy_v0
            )
            if discovery is not None and (
                not hmac.compare_digest(self._base_url, discovery.base_url)
                or not hmac.compare_digest(self._token, discovery.token)
            ):
                raise ApiIncompatibleError()
            expected_api = discovery.api if discovery is not None else discovery_api
            if expected_api is not None:
                selected_discovery = self._negotiate(expected_api)
                selected_ready = self._negotiate(ready.api)
                if selected_discovery != selected_ready:
                    raise ApiIncompatibleError()
        except (ApiIncompatibleError, EndpointError) as exc:
            raise BridgeApiIncompatibleError() from exc
        self._api_version = self._negotiate(ready.api)
        return ready

    async def async_get_status(self) -> dict[str, Any]:
        return await self._async_json("GET", "/status")

    async def async_get_auth_status(self) -> dict[str, Any]:
        return await self._async_json("GET", "/auth/status")

    async def async_start_auth_login(self, force_logout: bool = True) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            "/auth/device-login",
            json_body={"force_logout": force_logout},
            expected_status={202},
        )

    async def async_logout_auth(self) -> dict[str, Any]:
        return await self._async_json("POST", "/auth/logout")

    async def async_list_projects(self) -> list[dict[str, Any]]:
        return await self._async_json("GET", "/projects")

    async def async_create_project(
        self,
        name: str,
        default_model: str | None = None,
        default_thinking_level: str | None = None,
        root_path: str | None = None,
    ) -> dict[str, Any]:
        json_body: dict[str, Any] = {"name": name}
        if default_model is not None:
            json_body["default_model"] = default_model
        if default_thinking_level is not None:
            json_body["default_thinking_level"] = default_thinking_level
        if root_path:
            json_body["root_path"] = root_path
        return await self._async_json(
            "POST",
            "/projects",
            json_body=json_body,
            expected_status={201},
        )

    async def async_update_project(
        self,
        project_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._async_json(
            "PATCH",
            f"/projects/{_path_segment(project_id)}",
            json_body=updates,
        )

    async def async_archive_project(self, project_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/projects/{_path_segment(project_id)}/archive",
        )

    async def async_restore_project(self, project_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/projects/{_path_segment(project_id)}/restore",
        )

    async def async_delete_project(self, project_id: str) -> None:
        await self._async_no_content(
            "DELETE",
            f"/projects/{_path_segment(project_id)}",
            expected_status={204},
        )

    async def async_browse_paths(self, path: str | None = None) -> dict[str, Any]:
        query = ""
        if path:
            query = f"?path={quote(path, safe='')}"
        return await self._async_json("GET", f"/projects/browse{query}")

    async def async_create_folder(
        self, parent_path: str, folder_name: str
    ) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            "/projects/folders",
            json_body={
                "parent_path": parent_path,
                "folder_name": folder_name,
            },
            expected_status={201},
        )

    async def async_list_threads(
        self, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        suffix = "?include_archived=true" if include_archived else ""
        return await self._async_json("GET", f"/threads{suffix}")

    async def async_get_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json("GET", f"/threads/{_path_segment(thread_id)}")

    async def async_create_thread(
        self,
        title: str,
        mode: str,
        project_id: str | None = None,
        model_override: str | None = None,
        thinking_override: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "mode": mode,
        }
        if project_id:
            payload["project_id"] = project_id
        if model_override is not None:
            payload["model_override"] = model_override
        if thinking_override is not None:
            payload["thinking_override"] = thinking_override
        return await self._async_json(
            "POST",
            "/threads",
            json_body=payload,
            expected_status={201},
        )

    async def async_update_thread(
        self, thread_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._async_json(
            "PATCH",
            f"/threads/{_path_segment(thread_id)}",
            json_body=updates,
        )

    async def async_archive_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/archive",
        )

    async def async_restore_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/restore",
        )

    async def async_delete_thread(self, thread_id: str) -> None:
        await self._async_no_content(
            "DELETE",
            f"/threads/{_path_segment(thread_id)}",
            expected_status={204},
        )

    async def async_send_prompt(
        self,
        thread_id: str,
        prompt: str,
        *,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": prompt}
        if client_request_id is not None:
            payload["client_request_id"] = _client_request_id(client_request_id)
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/prompts",
            json_body=payload,
            expected_status={202},
        )

    async def async_cancel_run(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/runs/current/cancel",
        )

    async def async_get_events(
        self, thread_id: str, after: int = 0
    ) -> list[dict[str, Any]]:
        self.require_legacy_v0()
        after = _nonnegative_cursor(after)
        return await self._async_json(
            "GET",
            f"/threads/{_path_segment(thread_id)}/events/replay?after={after}",
        )

    async def async_replay_events(
        self,
        *,
        after: int = 0,
        scopes: frozenset[str] | set[str] | None = None,
        thread_ids: frozenset[str] | set[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read a bounded page from the global v1 event journal."""

        self.require_api_v1()
        return await self._async_global_events(
            "/events/replay",
            after=after,
            scopes=scopes,
            thread_ids=thread_ids,
            limit=limit,
        )

    async def async_wait_events(
        self,
        *,
        after: int = 0,
        scopes: frozenset[str] | set[str] | None = None,
        thread_ids: frozenset[str] | set[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Long-poll one globally ordered v1 event page."""

        self.require_api_v1()
        return await self._async_global_events(
            "/events/wait",
            after=after,
            scopes=scopes,
            thread_ids=thread_ids,
            limit=limit,
        )

    async def _async_global_events(
        self,
        endpoint: str,
        *,
        after: int,
        scopes: frozenset[str] | set[str] | None,
        thread_ids: frozenset[str] | set[str] | None,
        limit: int | None,
    ) -> dict[str, Any]:
        after = _nonnegative_cursor(after)
        normalized_scopes = None if scopes is None else tuple(sorted(scopes))
        if normalized_scopes is not None and (
            not normalized_scopes
            or not set(normalized_scopes) <= {"auth", "runtime", "thread"}
        ):
            raise BridgeApiEndpointError("event_filter_invalid")
        normalized_threads = None
        if thread_ids is not None:
            normalized_threads = tuple(
                sorted(_path_segment(value) for value in thread_ids)
            )
            if (
                not normalized_threads
                or len(normalized_threads) > 64
                or (normalized_scopes is not None and "thread" not in normalized_scopes)
            ):
                raise BridgeApiEndpointError("event_filter_invalid")
        if limit is None:
            limit = BRIDGE_EVENT_BATCH_LIMIT
        if type(limit) is not int or not 1 <= limit <= BRIDGE_EVENT_BATCH_LIMIT:
            raise BridgeApiEndpointError("event_limit_invalid")
        query: list[tuple[str, object]] = [("after", after)]
        query.extend(("scope", scope) for scope in normalized_scopes or ())
        query.extend(("thread_id", thread_id) for thread_id in normalized_threads or ())
        query.append(("limit", limit))
        if endpoint == "/events/wait":
            query.append(("timeout_seconds", BRIDGE_EVENT_WAIT_SECONDS))
        return await self._async_json(
            "GET",
            f"{endpoint}?{urlencode(query)}",
            maximum_bytes=BRIDGE_EVENT_BATCH_MAX_BYTES,
        )

    async def async_cancel_auth_login(self) -> dict[str, Any]:
        self.require_api_v1()
        return await self._async_json("POST", "/auth/device-login/cancel")

    async def async_list_pending_interactions(
        self, *, thread_id: str | None = None
    ) -> dict[str, Any]:
        self.require_api_v1()
        suffix = "" if thread_id is None else f"?thread_id={_path_segment(thread_id)}"
        return await self._async_json("GET", f"/interactions/pending{suffix}")

    async def async_decide_interaction(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        run_id: str,
        turn_id: str,
        item_id: str,
        decision: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        self.require_api_v1()
        if decision not in {"accept", "decline", "cancel"}:
            raise BridgeApiEndpointError("decision_invalid")
        return await self._async_json(
            "POST",
            f"/interactions/{_path_segment(interaction_id)}/decision",
            json_body={
                "thread_id": _path_segment(thread_id),
                "run_id": _path_segment(run_id),
                "turn_id": _path_segment(turn_id),
                "item_id": _path_segment(item_id),
                "decision": decision,
                "client_request_id": _client_request_id(client_request_id),
            },
        )

    async def async_answer_interaction(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        run_id: str,
        turn_id: str,
        item_id: str,
        answers: list[dict[str, Any]],
        client_request_id: str,
    ) -> dict[str, Any]:
        self.require_api_v1()
        normalized_answers = _interaction_answers(answers)
        return await self._async_json(
            "POST",
            f"/interactions/{_path_segment(interaction_id)}/answer",
            json_body={
                "thread_id": _path_segment(thread_id),
                "run_id": _path_segment(run_id),
                "turn_id": _path_segment(turn_id),
                "item_id": _path_segment(item_id),
                "answers": normalized_answers,
                "client_request_id": _client_request_id(client_request_id),
            },
        )

    async def async_list_artifacts(self, thread_id: str) -> list[dict[str, Any]]:
        return await self._async_json(
            "GET",
            f"/threads/{_path_segment(thread_id)}/artifacts",
        )

    async def async_create_workspace_archive(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/artifacts/workspace-archive",
            expected_status={201},
        )

    async def async_upload_attachment(
        self,
        thread_id: str,
        filename: str,
        content_type: str,
        content: Any,
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        """Use the legacy multipart transport; API v1 uses resumable chunks."""

        self.require_legacy_v0()
        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            content,
            filename=filename,
            content_type=content_type,
        )
        if relative_path:
            form_data.add_field("relative_path", relative_path)
        return await self._async_json(
            "POST",
            f"/threads/{_path_segment(thread_id)}/attachments",
            data=form_data,
            expected_status={201},
        )

    async def async_download_artifact(
        self, thread_id: str, artifact_id: str
    ) -> BridgeDownload:
        """Use the legacy buffered download; API v1 uses ranged streaming."""

        self.require_legacy_v0()
        return await self._async_download(
            "GET",
            f"/threads/{_path_segment(thread_id)}/artifacts/{_path_segment(artifact_id)}",
        )

    async def _async_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        expected_status: set[int] | None = None,
        maximum_bytes: int | None = None,
    ) -> Any:
        response = await self._async_request(
            method,
            path,
            json_body=json_body,
            data=data,
            expected_status=expected_status,
        )
        async with response:
            try:
                if maximum_bytes is None:
                    return await response.json()
                if (
                    response.content_length is not None
                    and response.content_length > maximum_bytes
                ):
                    raise BridgeApiPayloadTooLargeError(status=response.status)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if len(raw) + len(chunk) > maximum_bytes:
                        raise BridgeApiPayloadTooLargeError(status=response.status)
                    raw.extend(chunk)
                return json.loads(raw)
            except aiohttp.SocketTimeoutError:
                raise BridgeApiReadTimeoutError() from None
            except asyncio.TimeoutError:
                raise BridgeApiTimeoutError() from None
            except asyncio.IncompleteReadError:
                raise BridgeApiConnectionError() from None
            except (aiohttp.ClientError, ValueError):
                raise BridgeApiProblemError(status=response.status) from None

    async def _async_no_content(
        self,
        method: str,
        path: str,
        *,
        expected_status: set[int],
    ) -> None:
        response = await self._async_request(
            method, path, expected_status=expected_status
        )
        async with response:
            return None

    async def _async_download(
        self,
        method: str,
        path: str,
    ) -> BridgeDownload:
        response = await self._async_request(method, path)
        async with response:
            try:
                return BridgeDownload(
                    content=await response.read(),
                    content_type=response.headers.get(
                        "Content-Type",
                        "application/octet-stream",
                    ),
                )
            except aiohttp.SocketTimeoutError:
                raise BridgeApiReadTimeoutError() from None
            except asyncio.TimeoutError:
                raise BridgeApiTimeoutError() from None
            except aiohttp.ClientError:
                raise BridgeApiConnectionError() from None

    @asynccontextmanager
    async def async_stream(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        expected_status: set[int] | None = None,
    ) -> AsyncIterator[BridgeStreamResponse]:
        """Yield a response without buffering it; always release it afterwards."""

        response = await self._async_request(
            method,
            path,
            json_body=json_body,
            data=data,
            expected_status=expected_status,
        )
        try:
            yield BridgeStreamResponse(response)
        finally:
            response.close()

    def _negotiate(self, api: ApiRange) -> int:
        if self._allow_legacy_v0 and api.maximum < API_CURRENT:
            return 0
        if api.minimum > API_CURRENT or api.maximum < API_CURRENT:
            raise ApiIncompatibleError()
        return API_CURRENT

    async def _async_problem(self, response: aiohttp.ClientResponse) -> ProblemRecord:
        payload: object = {}
        try:
            raw = await response.content.read(BRIDGE_PROBLEM_BODY_MAX_BYTES + 1)
            if len(raw) <= BRIDGE_PROBLEM_BODY_MAX_BYTES:
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, ValueError):
                    payload = {}
        except aiohttp.SocketTimeoutError:
            raise BridgeApiReadTimeoutError() from None
        except asyncio.TimeoutError:
            raise BridgeApiTimeoutError() from None
        except (aiohttp.ClientError, asyncio.IncompleteReadError):
            raise BridgeApiConnectionError() from None
        finally:
            response.close()

        try:
            return ProblemRecord.from_payload(response.status, payload)
        except EndpointError:
            return ProblemRecord.from_payload(response.status, {})

    @staticmethod
    def _problem_error(problem: ProblemRecord) -> BridgeApiError:
        if problem.status in {401, 403}:
            return BridgeApiAuthError(problem=problem)
        if problem.status == 409:
            if problem.code == "api_incompatible":
                return BridgeApiIncompatibleError(problem=problem)
            return BridgeApiConflictError(problem=problem)
        if problem.status == 410:
            return BridgeApiGoneError(problem=problem)
        if problem.status == 413:
            return BridgeApiPayloadTooLargeError(problem=problem)
        if problem.status == 416:
            return BridgeApiRangeNotSatisfiableError(problem=problem)
        return BridgeApiProblemError(problem=problem)

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        expected_status: set[int] | None = None,
    ) -> aiohttp.ClientResponse:
        if expected_status is None:
            expected_status = {200}
        if method not in {"DELETE", "GET", "PATCH", "POST", "PUT"}:
            raise BridgeApiEndpointError("method_invalid")
        request_path = _request_path(path)
        try:
            async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
                response = await self._session.request(
                    method,
                    f"{self._base_url}{request_path}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        BRIDGE_API_HEADER: str(
                            self._api_version
                            if self._api_version is not None
                            else API_CURRENT
                        ),
                    },
                    json=json_body,
                    data=data,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
        except aiohttp.ConnectionTimeoutError:
            raise BridgeApiConnectTimeoutError() from None
        except aiohttp.SocketTimeoutError:
            raise BridgeApiReadTimeoutError() from None
        except asyncio.TimeoutError:
            raise BridgeApiTimeoutError() from None
        except aiohttp.ClientError:
            raise BridgeApiConnectionError() from None

        if 300 <= response.status < 400:
            response.close()
            raise BridgeApiRedirectError(status=response.status)

        if response.status in expected_status:
            return response

        problem = await self._async_problem(response)
        raise self._problem_error(problem)
