from fastapi.testclient import TestClient

from codex_bridge_service import __version__
from codex_bridge_service.app import create_app
from codex_bridge_service.build_info import BuildInfo


AUTHORIZATION = {
    "Authorization": "Bearer secret",
    "X-Codex-Bridge-Api": "1",
}


def test_readiness_requires_bridge_bearer_token(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        build_info=BuildInfo(app_version="0.6.0"),
    )

    response = TestClient(app).get("/ready")

    assert response.status_code == 401
    assert "0.6.0" not in response.text


def test_readiness_round_trips_validated_injected_build_information(tmp_path) -> None:
    build_info = BuildInfo(
        app_version="0.6.0",
        bridge_version="0.6.1",
        codex_version="0.144.1",
        image_revision="a" * 40,
        architecture="amd64",
        release_lock_digest="b" * 64,
    )
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        build_info=build_info,
    )

    response = TestClient(app).get("/ready", headers=AUTHORIZATION)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api": {
            "current": 1,
            "minimum": 1,
            "maximum": 1,
            "legacy_version": 0,
            "legacy_supported": True,
        },
        "app": {"version": "0.6.0"},
        "bridge": {"version": "0.6.1"},
        "codex": {"version": "0.144.1"},
        "image": {
            "revision": "a" * 40,
            "release_lock_digest": "b" * 64,
        },
        "architecture": "amd64",
        "capabilities": ["api_v1", "legacy_v0"],
        "sandbox": {"contract_version": None, "attested": False},
        "readiness": {"state": "ready", "reasons": []},
    }
    assert app.state.build_info is build_info


def test_readiness_defaults_are_safe_and_keep_existing_status_field(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        build_info=BuildInfo(),
    )

    payload = TestClient(app).get("/ready", headers=AUTHORIZATION).json()

    assert payload == {
        "status": "ok",
        "api": {
            "current": 1,
            "minimum": 1,
            "maximum": 1,
            "legacy_version": 0,
            "legacy_supported": True,
        },
        "app": {"version": None},
        "bridge": {"version": __version__},
        "codex": {"version": None},
        "image": {"revision": None, "release_lock_digest": None},
        "architecture": "unknown",
        "capabilities": ["api_v1", "legacy_v0"],
        "sandbox": {"contract_version": None, "attested": False},
        "readiness": {"state": "ready", "reasons": []},
    }


def test_readiness_exposes_only_the_configured_optional_capabilities(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    app.state.feature_capabilities = (
        "api_v1",
        "legacy_v0",
        "automations_v1",
        "agents_v1",
    )

    payload = TestClient(app).get("/ready", headers=AUTHORIZATION).json()

    assert payload["capabilities"] == [
        "api_v1",
        "legacy_v0",
        "automations_v1",
        "agents_v1",
    ]


def test_create_app_reads_only_validated_environment_metadata_once(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_BRIDGE_APP_VERSION", "0.6.2")
    monkeypatch.setenv("CODEX_BRIDGE_VERSION", "invalid bridge; supervisor-secret")
    monkeypatch.setenv("CODEX_BRIDGE_CODEX_VERSION", "0.144.1")
    monkeypatch.setenv(
        "CODEX_BRIDGE_IMAGE_REVISION", "invalid revision; supervisor-secret"
    )
    monkeypatch.setenv("CODEX_BRIDGE_ARCH", "amd64; supervisor-secret")
    monkeypatch.setenv("CODEX_BRIDGE_RELEASE_LOCK_DIGEST", "invalid; supervisor-secret")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-secret")
    app = create_app(root_path=tmp_path, auth_token="secret")

    monkeypatch.setenv("CODEX_BRIDGE_APP_VERSION", "9.9.9")
    response = TestClient(app).get("/ready", headers=AUTHORIZATION)

    assert response.status_code == 200
    payload = response.json()
    assert payload["app"] == {"version": "0.6.2"}
    assert payload["bridge"] == {"version": __version__}
    assert payload["codex"] == {"version": "0.144.1"}
    assert payload["image"] == {"revision": None, "release_lock_digest": None}
    assert payload["architecture"] == "unknown"
    assert "supervisor-secret" not in response.text
    assert app.state.build_info.app_version == "0.6.2"
