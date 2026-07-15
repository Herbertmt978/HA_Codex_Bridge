import json
from pathlib import Path

import pytest

from custom_components.codex_bridge.protocol import (
    ApiIncompatibleError,
    ApiRange,
    DiscoveryRecord,
    EndpointError,
    ProblemRecord,
    ReadyRecord,
    negotiate_api,
    validate_bridge_url,
)


FIXTURES = Path(__file__).parents[2] / "fixtures"
DISCOVERY_TOKEN = "discovery-token-0123456789abcdef0123456789"
DISCOVERY_UUID = "0123456789abcdef0123456789abcdef"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_negotiates_v1_and_keeps_the_explicit_v0_compatibility_path() -> None:
    assert negotiate_api(ApiRange(1, 1)) == 1
    assert negotiate_api(ApiRange(0, 0), allow_legacy_v0=True) == 0


@pytest.mark.parametrize("api_range", [ApiRange(2, 2), ApiRange(0, 0)])
def test_rejects_non_overlapping_api_ranges_without_legacy_opt_in(
    api_range: ApiRange,
) -> None:
    with pytest.raises(ApiIncompatibleError) as error:
        negotiate_api(api_range)

    assert error.value.code == "api_incompatible"
    assert error.value.retryable is False


def test_parses_a_typed_immutable_v1_ready_record() -> None:
    ready = ReadyRecord.from_payload(_fixture("ready_v1.json"))

    assert ready.api == ApiRange(1, 1)
    assert ready.bridge_version == "0.6.0"
    assert ready.app_version == "0.6.0"
    assert ready.codex_version == "0.144.1"
    assert ready.image_revision == "a" * 40
    assert ready.architecture == "amd64"
    assert ready.is_v1 is True
    assert ready.capabilities == ("api_v1", "legacy_v0")
    assert ready.readiness_state == "ready"
    assert ready.readiness_reasons == ()
    with pytest.raises(AttributeError):
        ready.bridge_version = "changed"  # type: ignore[misc]


def test_parses_legacy_v0_ready_only_with_explicit_compatibility() -> None:
    ready = ReadyRecord.from_payload(
        _fixture("ready_legacy_v0.json"), allow_legacy_v0=True
    )

    assert ready.api == ApiRange(0, 0)
    assert ready.is_v1 is False


def test_parses_an_immutable_redacted_discovery_record() -> None:
    discovery = DiscoveryRecord.from_payload(
        {
            "source": "hassio",
            "service": "codex_bridge",
            "slug": "codex_bridge",
            "uuid": DISCOVERY_UUID,
            "host": "172.30.32.5",
            "port": 8766,
            "token": DISCOVERY_TOKEN,
            "api": {"minimum": 1, "maximum": 1},
        }
    )

    assert discovery.base_url == "http://172.30.32.5:8766"
    assert discovery.api == ApiRange(1, 1)
    assert DISCOVERY_TOKEN not in repr(discovery)
    with pytest.raises(AttributeError):
        discovery.port = 9999  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "hassio"},
        {
            "source": "hassio",
            "service": "codex_bridge",
            "slug": "local_codex_bridge",
            "uuid": DISCOVERY_UUID,
            "host": "8.8.8.8",
            "port": 8766,
            "token": DISCOVERY_TOKEN,
            "api": {"minimum": 1, "maximum": 1},
        },
    ],
)
def test_rejects_malformed_or_public_discovery_without_echoing_token(
    payload: dict,
) -> None:
    with pytest.raises(EndpointError) as error:
        DiscoveryRecord.from_payload(payload)

    assert DISCOVERY_TOKEN not in repr(error.value)


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "local_codex_bridge", "169.254.1.1", "192.0.2.1"],
)
def test_rejects_non_app_supervisor_discovery_hosts(host: str) -> None:
    with pytest.raises(EndpointError):
        DiscoveryRecord.from_payload(
            {
                "source": "hassio",
                "service": "codex_bridge",
                "slug": "local_codex_bridge",
                "uuid": DISCOVERY_UUID,
                "host": host,
                "port": 8766,
                "token": DISCOVERY_TOKEN,
                "api": {"minimum": 1, "maximum": 1},
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("uuid", "bridge-instance-1"),
        ("slug", "evilcodex_bridge"),
        ("service", "other_service"),
        ("source", "user"),
    ],
)
def test_rejects_untrusted_discovery_identity(field: str, value: str) -> None:
    payload = {
        "source": "hassio",
        "service": "codex_bridge",
        "slug": "local_codex_bridge",
        "uuid": DISCOVERY_UUID,
        "host": "172.30.32.5",
        "port": 8766,
        "token": DISCOVERY_TOKEN,
        "api": {"minimum": 1, "maximum": 1},
    }
    payload[field] = value

    with pytest.raises(EndpointError):
        DiscoveryRecord.from_payload(payload)


def test_rejects_future_incompatible_ready_contract() -> None:
    with pytest.raises(ApiIncompatibleError):
        ReadyRecord.from_payload(_fixture("ready_future_incompatible.json"))


def test_rejects_malformed_ready_payload_without_echoing_it() -> None:
    with pytest.raises(EndpointError) as error:
        ReadyRecord.from_payload({"status": "broken", "detail": "bridge-token"})

    assert "bridge-token" not in repr(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "https://bridge.example.test/path",
        "http://8.8.8.8:8766",
        "http://0.0.0.0:8766",
        "http://224.0.0.1:8766",
        "https://evil.example.com",
        "http://bad host:8766",
        " http://127.0.0.1:8766",
        "http://user:password@127.0.0.1:8766",
    ],
)
def test_rejects_malformed_or_public_bridge_endpoints(url: str) -> None:
    with pytest.raises(EndpointError):
        validate_bridge_url(url)


def test_accepts_a_private_bridge_endpoint_without_rewriting_it() -> None:
    assert validate_bridge_url("http://127.0.0.1:8766/") == "http://127.0.0.1:8766"
    assert (
        validate_bridge_url("http://local_codex_bridge:8766")
        == "http://local_codex_bridge:8766"
    )
    assert validate_bridge_url("https://bridge.home.arpa") == "https://bridge.home.arpa"


@pytest.mark.parametrize(
    "token",
    [
        "short",
        "x" * 31,
        "x" * 31 + "\n",
        "replace-this-with-a-long-random-token",
        "x" * 513,
    ],
)
def test_rejects_short_or_control_character_discovery_tokens(token: str) -> None:
    with pytest.raises(EndpointError) as error:
        DiscoveryRecord.from_payload(
            {
                "source": "hassio",
                "service": "codex_bridge",
                "slug": "codex_bridge",
                "uuid": DISCOVERY_UUID,
                "host": "172.30.32.5",
                "port": 8766,
                "token": token,
                "api": {"minimum": 1, "maximum": 1},
            }
        )

    assert token not in repr(error.value)


def test_ready_record_rejects_hostile_version_and_reason_text_without_retaining_it() -> (
    None
):
    payload = _fixture("ready_v1.json")
    payload["bridge"]["version"] = "secret-token"
    payload["readiness"]["reasons"] = ["private-prompt"]

    with pytest.raises(EndpointError) as error:
        ReadyRecord.from_payload(payload)

    assert "secret-token" not in repr(error.value)
    assert "private-prompt" not in repr(error.value)


def test_problem_record_keeps_only_safe_cursor_recovery_metadata() -> None:
    problem = ProblemRecord.from_payload(
        410,
        {
            "detail": {
                "code": "event_cursor_expired",
                "retryable": False,
                "minimum_cursor": 42,
                "message": "secret-token",
                "snapshot": {
                    "required": True,
                    "cursor": 41,
                    "scope": "auth",
                    "thread_id": "thread_123",
                    "private": "secret-token",
                },
            }
        },
    )

    assert problem.code == "event_cursor_expired"
    assert problem.minimum_cursor == 42
    assert problem.snapshot_cursor == 41
    assert problem.scope == "auth"
    assert problem.thread_id == "thread_123"
    assert "secret-token" not in repr(problem)


def test_problem_record_redacts_unknown_remote_codes_and_untrusted_fields() -> None:
    problem = ProblemRecord.from_payload(
        409,
        {
            "detail": {
                "code": "secret-token",
                "retryable": "secret-token",
                "resource": "secret-token",
            }
        },
    )

    assert problem.code == "conflict"
    assert problem.retryable is False
    assert problem.resource is None
    assert "secret-token" not in repr(problem)
