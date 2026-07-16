import json
import asyncio
from pathlib import Path
import traceback

import aiohttp
from aiohttp import web
import pytest

from custom_components.codex_bridge.bridge_api import (
    BridgeApiClient,
    BridgeApiCapabilityError,
    BridgeApiConflictError,
    BridgeApiConnectionError,
    BridgeApiConnectTimeoutError,
    BridgeApiEndpointError,
    BridgeApiGoneError,
    BridgeApiIncompatibleError,
    BridgeApiMcpDisabledError,
    BridgeApiPayloadTooLargeError,
    BridgeApiProblemError,
    BridgeApiRangeNotSatisfiableError,
    BridgeApiReadTimeoutError,
    BridgeApiRedirectError,
    BridgeApiTimeoutError,
    BridgeDownload,
    BridgeStreamResponse,
    REQUEST_TIMEOUT,
)
from custom_components.codex_bridge.protocol import DiscoveryRecord, ReadyRecord


FIXTURES = Path(__file__).parents[2] / "fixtures"
TOKEN = "bridge-token-0123456789abcdef0123456789"
DISCOVERY_UUID = "0123456789abcdef0123456789abcdef"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_malformed_endpoint_suppresses_private_validation_details() -> None:
    private_sentinel = "-".join(("private", "endpoint", "sentinel"))

    with pytest.raises(BridgeApiEndpointError) as error:
        BridgeApiClient(
            object(),
            f"http://localhost:{private_sentinel}",
            TOKEN,
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert private_sentinel not in rendered


async def test_authenticated_ready_negotiates_v1_and_sends_api_header(
    bridge_server_factory,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["X-Codex-Bridge-Api"] == "1"
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        ready = await client.async_ready()
        assert session.closed is False
        assert client.negotiated_api_version == 1
        assert client.supports_api_v1 is True
        client.require_api_v1()

    assert ready == ReadyRecord.from_payload(_fixture("ready_v1.json"))


async def test_start_auth_login_defaults_to_non_destructive_mode(
    bridge_server_factory,
) -> None:
    bodies: list[dict[str, object]] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/auth/device-login":
            bodies.append(await request.json())
            return web.json_response({"state": "login_starting"}, status=202)
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_start_auth_login()

    assert bodies == [{"force_logout": False}]


async def test_feature_client_route_fails_before_request_when_not_advertised(
    bridge_server_factory,
) -> None:
    observed_paths: list[str] = []
    ready = _fixture("ready_v1.json")
    ready["capabilities"] = ["api_v1", "legacy_v0"]

    async def handler(request: web.Request) -> web.Response:
        observed_paths.append(request.path)
        return web.json_response(ready)

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        with pytest.raises(BridgeApiCapabilityError):
            await client.async_list_automations()

    assert observed_paths == ["/ready"]


async def test_missing_mcp_capability_reports_the_app_option_instead_of_an_update(
    bridge_server_factory,
) -> None:
    observed_paths: list[str] = []
    ready = _fixture("ready_v1.json")
    ready["capabilities"] = ["api_v1", "legacy_v0"]

    async def handler(request: web.Request) -> web.Response:
        observed_paths.append(request.path)
        return web.json_response(ready)

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        with pytest.raises(BridgeApiMcpDisabledError) as error:
            await client.async_list_mcp()

    assert error.value.code == "mcp_disabled"
    assert observed_paths == ["/ready"]


async def test_automation_and_mcp_client_routes_preserve_boundaries(
    bridge_server_factory,
) -> None:
    observed: list[tuple[str, str, object]] = []

    async def handler(request: web.Request) -> web.Response:
        body = (
            await request.json()
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.content_length
            else None
        )
        observed.append((request.method, request.path, body))
        if request.path == "/automations/aut_1/runs":
            return web.json_response(
                {"automation_run_id": "autrun_1", "status": "queued"}, status=202
            )
        if request.path == "/automations/aut_1":
            return web.json_response({"automation_id": "aut_1", "revision": 1})
        if request.path == "/mcp/servers/mcp_1/oauth/login":
            return web.json_response(
                {"authorization_url": "https://auth.example.invalid/one-time"}
            )
        if request.path == "/capabilities/skills":
            return web.json_response({"name": "review"}, status=201)
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        await client.async_claim_automation_run(
            "aut_1",
            due_at="2026-07-15T10:00:00Z",
            idempotency_key="automation:aut_1:1:2026-07-15T10:00:00Z",
            expected_revision=1,
        )
        login = await client.async_login_mcp("mcp_1")
        skill = await client.async_create_skill(
            {
                "workspace_path": "C:/work",
                "name": "review",
                "description": "Review changes",
                "instructions": "Be precise.",
            }
        )
        await client.async_get_agents()
        automation = await client.async_get_automation("aut_1")

    assert observed[1] == (
        "POST",
        "/automations/aut_1/runs",
        {
            "source": "scheduled",
            "due_at": "2026-07-15T10:00:00Z",
            "idempotency_key": "automation:aut_1:1:2026-07-15T10:00:00Z",
            "expected_revision": 1,
        },
    )
    assert login == {"authorization_url": "https://auth.example.invalid/one-time"}
    assert skill == {"name": "review"}
    assert automation == {"automation_id": "aut_1", "revision": 1}
    assert observed[-2] == ("GET", "/agents/global", None)
    assert observed[-1] == ("GET", "/automations/aut_1", None)


async def test_plugin_uninstall_uses_the_backend_plugin_id_contract(
    bridge_server_factory,
) -> None:
    observed: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        observed.append((request.method, request.raw_path))
        if request.method == "DELETE":
            return web.Response(status=204)
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        await client.async_uninstall_plugin("plugin.example@marketplace")

    assert observed == [
        ("GET", "/ready"),
        ("DELETE", "/capabilities/plugins/plugin.example@marketplace"),
    ]


@pytest.mark.parametrize("skill_name", ["review.python-3", "a..b"])
async def test_skill_delete_uses_the_backend_skill_name_contract(
    bridge_server_factory, skill_name: str
) -> None:
    observed: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        observed.append((request.method, request.raw_path))
        if request.method == "DELETE":
            return web.Response(status=204)
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        await client.async_delete_skill(skill_name)

    assert observed == [
        ("GET", "/ready"),
        ("DELETE", f"/capabilities/skills/{skill_name}"),
    ]


@pytest.mark.parametrize(
    "skill_name",
    [
        "../review",
        "review/child",
        "review\\child",
        "review\x00name",
        "-review",
        "review!",
        "r\u00e9view",
        "a" * 129,
    ],
)
async def test_skill_delete_rejects_invalid_backend_skill_names_before_network_access(
    skill_name: str,
) -> None:
    class UnexpectedSession:
        async def request(self, *args, **kwargs):
            raise AssertionError("network must not be reached")

    client = BridgeApiClient(UnexpectedSession(), "http://127.0.0.1:8766", TOKEN)
    client._api_version = 1
    client._capabilities = frozenset({"skills_v1"})

    with pytest.raises(BridgeApiEndpointError):
        await client.async_delete_skill(skill_name)


@pytest.mark.parametrize(
    "plugin_id",
    ["../plugin", "plugin/child", "plugin\\child", "plugin\x00name", "a" * 129],
)
async def test_plugin_uninstall_rejects_unsafe_plugin_ids_before_network_access(
    plugin_id: str,
) -> None:
    class UnexpectedSession:
        async def request(self, *args, **kwargs):
            raise AssertionError("network must not be reached")

    client = BridgeApiClient(UnexpectedSession(), "http://127.0.0.1:8766", TOKEN)
    client._api_version = 1
    client._capabilities = frozenset({"plugins_v1"})

    with pytest.raises(BridgeApiEndpointError):
        await client.async_uninstall_plugin(plugin_id)


async def test_explicit_legacy_client_uses_v0_header_after_legacy_readiness(
    bridge_server_factory,
) -> None:
    headers: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        headers.append(request.headers["X-Codex-Bridge-Api"])
        if request.path == "/ready":
            return web.json_response(_fixture("ready_legacy_v0.json"))
        return web.json_response({"status": "ok"})

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(
            session,
            str(server.make_url("")),
            "x" * 32,
            allow_legacy_v0=True,
        )
        await client.async_ready()
        await client.async_get_status()
        assert client.negotiated_api_version == 0
        assert client.supports_api_v1 is False
        assert client.supports_legacy_v0 is True
        with pytest.raises(BridgeApiCapabilityError):
            client.require_api_v1()
        client.require_legacy_v0()
        with pytest.raises(BridgeApiEndpointError):
            await client.async_get_events("thr_safe", after=-1)

    assert headers == ["1", "0"]


async def test_ready_rejects_a_discovery_contract_that_does_not_match_the_authenticated_server(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(_fixture("ready_future_incompatible.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiIncompatibleError):
            await client.async_ready(
                discovery=DiscoveryRecord.from_payload(
                    {
                        "source": "hassio",
                        "service": "codex_bridge",
                        "slug": "codex_bridge",
                        "host": "172.30.32.5",
                        "port": 8766,
                        "token": TOKEN,
                        "api": {"minimum": 1, "maximum": 1},
                        "uuid": DISCOVERY_UUID,
                    }
                )
            )


async def test_ready_rejects_discovery_identity_that_differs_from_the_client(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiIncompatibleError):
            await client.async_ready(
                discovery=DiscoveryRecord.from_payload(
                    {
                        "source": "hassio",
                        "service": "codex_bridge",
                        "slug": "codex_bridge",
                        "uuid": DISCOVERY_UUID,
                        "host": "172.30.32.5",
                        "port": 8766,
                        "token": "a" * 32,
                        "api": {"minimum": 1, "maximum": 1},
                    }
                )
            )


async def test_refuses_redirects_instead_of_forwarding_the_bearer_token(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        raise web.HTTPFound("http://redirected.invalid/ready")

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiRedirectError) as error:
            await client.async_ready()

    assert TOKEN not in repr(error.value)
    assert "redirected.invalid" not in repr(error.value)


@pytest.mark.parametrize(
    ("status", "detail", "error_type"),
    [
        (
            409,
            {"code": "runtime_request_conflict", "retryable": False},
            BridgeApiConflictError,
        ),
        (
            410,
            {
                "code": "event_cursor_expired",
                "retryable": False,
                "minimum_cursor": 12,
                "snapshot": {"required": True, "cursor": 11, "scope": "global"},
            },
            BridgeApiGoneError,
        ),
        (
            413,
            {"code": "quota_exceeded", "resource": "upload", "retryable": False},
            BridgeApiPayloadTooLargeError,
        ),
        (416, "secret-token should never escape", BridgeApiRangeNotSatisfiableError),
    ],
)
async def test_maps_v1_problem_statuses_to_safe_typed_errors(
    bridge_server_factory,
    status: int,
    detail: object,
    error_type: type[Exception],
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response({"detail": detail}, status=status)

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(error_type) as error:
            await client.async_get_status()

    assert TOKEN not in repr(error.value)
    assert "secret-token" not in str(error.value)
    assert error.value.problem is not None
    if status == 410:
        assert error.value.problem.minimum_cursor == 12
        assert error.value.problem.snapshot_cursor == 11
        assert error.value.problem.scope == "global"
    if status == 413:
        assert error.value.problem.resource == "upload"


async def test_maps_only_an_explicit_api_problem_to_incompatible(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"detail": {"code": "api_incompatible", "retryable": False}},
            status=409,
        )

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiIncompatibleError) as error:
            await client.async_get_status()

    assert error.value.problem is not None
    assert error.value.problem.code == "api_incompatible"


async def test_unknown_problem_payload_is_redacted_and_the_response_is_released(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text="bridge-token", status=500)

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiProblemError) as error:
            await client.async_get_status()

    assert TOKEN not in repr(error.value)


async def test_successful_malformed_json_maps_to_a_safe_problem(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(text="bridge-token", content_type="text/plain")

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiProblemError) as error:
            await client.async_get_status()

    assert "bridge-token" not in repr(error.value)


@pytest.mark.parametrize(
    ("timeout_error", "error_type"),
    [
        (aiohttp.ConnectionTimeoutError(), BridgeApiConnectTimeoutError),
        (aiohttp.SocketTimeoutError(), BridgeApiReadTimeoutError),
        (asyncio.TimeoutError(), BridgeApiTimeoutError),
    ],
)
async def test_maps_request_timeouts_and_passes_the_bounded_timeout_policy(
    timeout_error: BaseException,
    error_type: type[Exception],
) -> None:
    class TimeoutSession:
        kwargs: dict

        async def request(self, *args, **kwargs):
            self.kwargs = kwargs
            raise timeout_error

    session = TimeoutSession()
    client = BridgeApiClient(session, "http://127.0.0.1:8766", TOKEN)

    with pytest.raises(error_type) as error:
        await client.async_get_status()

    assert TOKEN not in repr(error.value)
    assert session.kwargs["timeout"] == REQUEST_TIMEOUT
    assert session.kwargs["allow_redirects"] is False


async def test_maps_error_body_read_timeout_and_releases_the_response() -> None:
    class TimedOutContent:
        async def read(self, _: int) -> bytes:
            raise aiohttp.SocketTimeoutError("secret-token")

    class TimedOutResponse:
        status = 503
        content = TimedOutContent()
        closed = False

        def close(self) -> None:
            self.closed = True

    class TimedOutSession:
        response = TimedOutResponse()

        async def request(self, *args, **kwargs):
            return self.response

    session = TimedOutSession()
    client = BridgeApiClient(session, "http://127.0.0.1:8766", TOKEN)

    with pytest.raises(BridgeApiReadTimeoutError) as error:
        await client.async_get_status()

    assert session.response.closed is True
    assert error.value.__cause__ is None
    assert "secret-token" not in repr(error.value)


async def test_connection_errors_suppress_private_upstream_details() -> None:
    class FailedSession:
        async def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("secret-token at private.example")

    client = BridgeApiClient(FailedSession(), "http://127.0.0.1:8766", TOKEN)

    with pytest.raises(BridgeApiConnectionError) as error:
        await client.async_get_status()

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    assert "secret-token" not in repr(error.value)
    assert "private.example" not in repr(error.value)


async def test_rejects_path_and_cursor_injection_before_network_access() -> None:
    class UnexpectedSession:
        async def request(self, *args, **kwargs):
            raise AssertionError("network must not be reached")

    client = BridgeApiClient(UnexpectedSession(), "http://127.0.0.1:8766", TOKEN)

    with pytest.raises(BridgeApiEndpointError):
        await client.async_get_thread("../status?token=secret")


async def test_v1_rejects_legacy_buffered_file_and_event_transports(
    bridge_server_factory,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response(_fixture("ready_v1.json"))

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()

        with pytest.raises(BridgeApiCapabilityError):
            await client.async_get_events("thr_safe")
        with pytest.raises(BridgeApiCapabilityError):
            await client.async_upload_attachment(
                "thr_safe",
                "file.txt",
                "text/plain",
                b"content",
            )
        with pytest.raises(BridgeApiCapabilityError):
            await client.async_download_artifact("thr_safe", "art_safe")


async def test_v1_global_events_and_interaction_actions_use_safe_contracts(
    bridge_server_factory,
) -> None:
    paths: list[str] = []
    bodies: dict[str, dict] = {}

    async def handler(request: web.Request) -> web.Response:
        paths.append(f"{request.path}?{request.query_string}")
        if request.can_read_body:
            bodies[request.path] = await request.json()
        if request.path == "/ready":
            return web.json_response(_fixture("ready_v1.json"))
        if request.path in {"/events/replay", "/events/wait"}:
            return web.json_response(
                {
                    "events": [],
                    "next_cursor": 3,
                    "minimum_cursor": 0,
                    "has_more": False,
                    "heartbeat": request.path == "/events/wait",
                }
            )
        if request.path.endswith("/prompts"):
            return web.json_response({"ok": True}, status=202)
        return web.json_response({"ok": True})

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        await client.async_replay_events(
            after=3, scopes={"thread"}, thread_ids={"thr_safe"}
        )
        await client.async_wait_events(after=3)
        await client.async_cancel_auth_login()
        await client.async_list_pending_interactions(thread_id="thr_safe")
        await client.async_send_prompt(
            "thr_safe", "hello", client_request_id="request-1"
        )
        await client.async_decide_interaction(
            "int_safe",
            thread_id="thr_safe",
            run_id="run_safe",
            turn_id="turn_safe",
            item_id="item_safe",
            decision="accept",
            client_request_id="decision-1",
        )
        await client.async_answer_interaction(
            "int_safe",
            thread_id="thr_safe",
            run_id="run_safe",
            turn_id="turn_safe",
            item_id="item_safe",
            answers=[{"question_id": "question_safe", "values": ["yes"]}],
            client_request_id="answer-1",
        )

    assert "/events/replay?after=3&scope=thread&thread_id=thr_safe&limit=256" in paths
    assert "/events/wait?after=3&limit=256&timeout_seconds=15" in paths
    assert "/auth/device-login/cancel?" in paths
    assert "/interactions/pending?thread_id=thr_safe" in paths
    assert "/interactions/int_safe/decision?" in paths
    assert "/interactions/int_safe/answer?" in paths
    assert bodies["/threads/thr_safe/prompts"]["client_request_id"] == "request-1"
    assert bodies["/interactions/int_safe/answer"]["answers"] == [
        {"question_id": "question_safe", "values": ["yes"]}
    ]


async def test_v1_event_body_is_bounded_before_json_decoding(
    bridge_server_factory, monkeypatch
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path == "/ready":
            return web.json_response(_fixture("ready_v1.json"))
        return web.Response(body=b"{" + (b"x" * 64))

    monkeypatch.setattr(
        "custom_components.codex_bridge.bridge_api.BRIDGE_EVENT_BATCH_MAX_BYTES", 64
    )
    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        with pytest.raises(BridgeApiPayloadTooLargeError):
            await client.async_replay_events()


async def test_interaction_answers_are_strict_bounded_and_unique(
    bridge_server_factory,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path == "/ready":
            return web.json_response(_fixture("ready_v1.json"))
        return web.json_response({"ok": True})

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        for answers in (
            [{"question_id": "question_1", "answers": ["wrong key"]}],
            [
                {"question_id": "question_1", "values": ["one"]},
                {"question_id": "question_1", "values": ["two"]},
            ],
            [{"question_id": "question_1", "values": [""]}],
        ):
            with pytest.raises(BridgeApiEndpointError):
                await client.async_answer_interaction(
                    "int_safe",
                    thread_id="thr_safe",
                    run_id="run_safe",
                    turn_id="turn_safe",
                    item_id="item_safe",
                    answers=answers,
                    client_request_id="answer-1",
                )


def test_uses_bounded_connect_read_total_and_pool_timeouts() -> None:
    assert REQUEST_TIMEOUT.total is not None and REQUEST_TIMEOUT.total <= 30
    assert REQUEST_TIMEOUT.connect is not None and REQUEST_TIMEOUT.connect <= 10
    assert (
        REQUEST_TIMEOUT.sock_connect is not None and REQUEST_TIMEOUT.sock_connect <= 10
    )
    assert REQUEST_TIMEOUT.sock_read is not None and REQUEST_TIMEOUT.sock_read <= 20


async def test_stream_reader_maps_read_failures_without_exposing_upstream_details() -> (
    None
):
    class FailingContent:
        async def read(self, _: int) -> bytes:
            raise aiohttp.SocketTimeoutError("secret-token")

    class FailedResponse:
        status = 200
        headers: dict[str, str] = {}
        closed = False
        content = FailingContent()

    stream = BridgeStreamResponse(FailedResponse())

    with pytest.raises(BridgeApiReadTimeoutError) as error:
        await stream.read_chunk(1024)

    assert error.value.__cause__ is None
    assert "secret-token" not in repr(error.value)


async def test_chunk_iterator_maps_incomplete_reads_without_exposing_details() -> None:
    class IncompleteContent:
        async def iter_chunked(self, _: int):
            yield b"first"
            raise asyncio.IncompleteReadError(b"secret-token", 99)

    class IncompleteResponse:
        status = 200
        headers: dict[str, str] = {}
        closed = False
        content = IncompleteContent()

    stream = BridgeStreamResponse(IncompleteResponse())
    chunks: list[bytes] = []

    with pytest.raises(BridgeApiConnectionError) as error:
        async for chunk in stream.iter_chunked(1024):
            chunks.append(chunk)

    assert chunks == [b"first"]
    assert error.value.__cause__ is None
    assert "secret-token" not in repr(error.value)


async def test_failed_readiness_clears_a_previous_negotiated_version(
    bridge_server_factory,
) -> None:
    call_count = 0

    async def handler(_: web.Request) -> web.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return web.json_response(_fixture("ready_v1.json"))
        return web.json_response({"ready": True, "api": {"current": 99}})

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)

        await client.async_ready()
        assert client.negotiated_api_version == 1

        with pytest.raises(BridgeApiIncompatibleError):
            await client.async_ready()

        assert client.negotiated_api_version is None


def test_legacy_buffered_download_repr_never_contains_payload_or_content_type() -> None:
    download = BridgeDownload(
        content=b"secret artifact bytes",
        content_type="secret/content-type",
    )

    assert "secret" not in repr(download)


async def test_streaming_response_is_owned_by_the_caller_until_its_context_exits(
    bridge_server_factory,
) -> None:
    stream_started = asyncio.Event()
    finish_stream = asyncio.Event()

    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"first chunk")
        stream_started.set()
        await finish_stream.wait()
        await response.write_eof()
        return response

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        async with client.async_stream("GET", "/stream") as response:
            await stream_started.wait()
            assert response.closed is False
            assert await response.read_chunk(11) == b"first chunk"

        assert response.closed is True
        finish_stream.set()


async def test_streaming_response_closes_when_the_consumer_is_cancelled(
    bridge_server_factory,
) -> None:
    stream_started = asyncio.Event()
    finish_stream = asyncio.Event()

    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"first chunk")
        stream_started.set()
        await finish_stream.wait()
        return response

    server = await bridge_server_factory(handler)
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(asyncio.CancelledError):
            async with client.async_stream("GET", "/stream") as response:
                await stream_started.wait()
                raise asyncio.CancelledError

        assert response.closed is True
        finish_stream.set()
