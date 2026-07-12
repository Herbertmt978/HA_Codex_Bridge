from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from codex_bridge_service.api_contract import (
    API_CONTRACT,
    API_CURRENT,
    API_MAXIMUM,
    API_MINIMUM,
    LEGACY_API_VERSION,
    ApiIncompatibleError,
    negotiate_api,
)
from codex_bridge_service.build_info import BuildInfo


EXPECTED_BUILD_ENVIRONMENT_KEYS = {
    "CODEX_BRIDGE_APP_VERSION",
    "CODEX_BRIDGE_VERSION",
    "CODEX_BRIDGE_CODEX_VERSION",
    "CODEX_BRIDGE_IMAGE_REVISION",
    "CODEX_BRIDGE_ARCH",
    "CODEX_BRIDGE_RELEASE_LOCK_DIGEST",
}


class TrackingEnvironment(Mapping[str, str]):
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.requested_keys: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.requested_keys.append(key)
        return self._values[key]

    def __iter__(self):
        raise AssertionError("BuildInfo must not iterate over the environment")

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default=None):
        self.requested_keys.append(key)
        return self._values.get(key, default)


def test_api_contract_advertises_v1_and_explicit_legacy_support() -> None:
    assert API_CURRENT == API_MINIMUM == API_MAXIMUM == 1
    assert LEGACY_API_VERSION == 0
    assert API_CONTRACT.model_dump() == {
        "current": 1,
        "minimum": 1,
        "maximum": 1,
        "legacy_version": 0,
        "legacy_supported": True,
    }

    with pytest.raises(ValidationError):
        API_CONTRACT.current = 2


@pytest.mark.parametrize(
    ("client_minimum", "client_maximum"),
    [
        (0, 1),
        (1, 1),
        (1, 2),
        (0, 99),
    ],
)
def test_negotiate_api_returns_highest_overlapping_version(
    client_minimum: int,
    client_maximum: int,
) -> None:
    assert negotiate_api(client_minimum, client_maximum) == 1


@pytest.mark.parametrize(
    ("client_minimum", "client_maximum"),
    [
        (0, 0),
        (2, 3),
        (2, 1),
        (-1, 1),
        (True, 1),
    ],
)
def test_negotiate_api_rejects_non_overlapping_or_invalid_ranges(
    client_minimum: int,
    client_maximum: int,
) -> None:
    with pytest.raises(ApiIncompatibleError) as raised:
        negotiate_api(client_minimum, client_maximum)

    error = raised.value
    assert error.code == "api_incompatible"
    assert error.status_code == 409
    assert error.problem.code == "api_incompatible"
    assert error.problem.status == 409
    assert error.problem.server_minimum == 1
    assert error.problem.server_maximum == 1


def test_api_incompatible_error_exposes_safe_immutable_problem_details() -> None:
    secret = "Bearer request-token-must-not-escape"

    with pytest.raises(ApiIncompatibleError) as raised:
        negotiate_api(secret, 1)  # type: ignore[arg-type]

    error = raised.value
    assert error.problem.client_minimum is None
    assert error.problem.client_maximum == 1
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in repr(error.problem)

    with pytest.raises(ValidationError):
        error.problem.status = 500


def test_build_info_reads_only_explicit_environment_fields() -> None:
    environment = TrackingEnvironment(
        {
            "CODEX_BRIDGE_APP_VERSION": "0.6.0",
            "CODEX_BRIDGE_VERSION": "0.6.1",
            "CODEX_BRIDGE_CODEX_VERSION": "0.144.1",
            "CODEX_BRIDGE_IMAGE_REVISION": "sha256:abc123",
            "CODEX_BRIDGE_ARCH": "aarch64",
            "CODEX_BRIDGE_RELEASE_LOCK_DIGEST": "d" * 64,
            "SUPERVISOR_TOKEN": "supervisor-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    build = BuildInfo.from_environment(environment)

    assert set(environment.requested_keys) == EXPECTED_BUILD_ENVIRONMENT_KEYS
    assert build.model_dump() == {
        "app_version": "0.6.0",
        "bridge_version": "0.6.1",
        "codex_version": "0.144.1",
        "image_revision": "sha256:abc123",
        "architecture": "aarch64",
        "release_lock_digest": "d" * 64,
    }
    assert "supervisor-secret" not in repr(build)
    assert "openai-secret" not in repr(build)


def test_build_info_normalizes_missing_or_blank_values_without_mutation() -> None:
    build = BuildInfo.from_environment(
        {
            "CODEX_BRIDGE_APP_VERSION": " ",
            "CODEX_BRIDGE_ARCH": "\t",
        }
    )

    assert build.model_dump() == {
        "app_version": None,
        "bridge_version": None,
        "codex_version": None,
        "image_revision": None,
        "architecture": "unknown",
        "release_lock_digest": None,
    }

    with pytest.raises(ValidationError):
        build.architecture = "amd64"
