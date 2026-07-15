import io
import json
from threading import Event, Thread, current_thread

import pytest


class ScriptedProcess:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            "".join(f"{json.dumps(response)}\n" for response in responses)
        )
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.terminated = True
        return 0


class BundledProcess:
    def __init__(self, output: bytes, *, returncode: int = 0) -> None:
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode if self.terminated else None

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.terminated = True
        return self.returncode


def _load_model_catalog_module():
    try:
        from codex_bridge_service import model_catalog
    except ImportError as exc:
        pytest.fail(f"model catalogue module is missing: {exc}")
    return model_catalog


def _model_payload(
    model: str, *, hidden: bool = False, is_default: bool = False
) -> dict[str, object]:
    return {
        "id": model,
        "model": model,
        "displayName": model.upper(),
        "description": f"{model} description",
        "hidden": hidden,
        "isDefault": is_default,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "medium", "description": "Balanced"},
        ],
        "inputModalities": ["text"],
    }


def _bundled_payload() -> dict[str, object]:
    return {
        "models": [
            {
                "slug": "hidden-model",
                "display_name": "Hidden",
                "visibility": "hide",
                "default_reasoning_level": "max",
                "supported_reasoning_levels": [{"effort": "max"}],
            },
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6 Sol",
                "description": "Frontier coding model",
                "visibility": "list",
                "default_reasoning_level": "low",
                "supported_reasoning_levels": [
                    {"effort": effort}
                    for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
                ],
                "input_modalities": ["text", "image", 42],
            },
            {
                "slug": "gpt-5.6-terra",
                "display_name": "GPT-5.6 Terra",
                "visibility": "list",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": effort} for effort in ("medium", "high", "max", "ultra")
                ],
            },
            {
                "slug": "gpt-5.6-luna",
                "display_name": "GPT-5.6 Luna",
                "visibility": "list",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": effort} for effort in ("medium", "high", "max")
                ],
            },
            {"slug": "", "visibility": "list"},
            {"slug": 123, "visibility": "list"},
        ]
    }


@pytest.mark.parametrize("invalid_timeout", [0, -1, float("nan"), float("inf"), True])
def test_model_catalog_probes_reject_invalid_timeouts(invalid_timeout) -> None:
    model_catalog = _load_model_catalog_module()

    with pytest.raises(ValueError, match="timeout must be positive"):
        model_catalog.CodexModelCatalogProbe(timeout_seconds=invalid_timeout)
    with pytest.raises(ValueError, match="timeout must be positive"):
        model_catalog.AppServerModelCatalogProbe(
            object(), timeout_seconds=invalid_timeout
        )


@pytest.mark.parametrize("invalid_ttl", [-1, float("nan"), float("inf"), True])
def test_model_catalog_probes_reject_invalid_cache_ttls(invalid_ttl) -> None:
    model_catalog = _load_model_catalog_module()

    with pytest.raises(ValueError, match="cache TTL must be non-negative"):
        model_catalog.CodexModelCatalogProbe(cache_ttl_seconds=invalid_ttl)
    with pytest.raises(ValueError, match="cache TTL must be non-negative"):
        model_catalog.AppServerModelCatalogProbe(
            object(), cache_ttl_seconds=invalid_ttl
        )


def test_probe_discovers_visible_models_and_reasoning_levels_from_configured_codex(
    tmp_path,
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {
                "id": 1,
                "result": {
                    "userAgent": "Codex/0.144.0",
                    "codexHome": str(tmp_path),
                    "platformFamily": "windows",
                    "platformOs": "windows",
                },
            },
            {
                "id": 2,
                "result": {
                    "config": {
                        "model": "gpt-5.6-sol",
                        "model_reasoning_effort": "ultra",
                    },
                    "origins": {},
                },
            },
            {
                "id": 3,
                "result": {
                    "data": [
                        {
                            "id": "gpt-5.6-sol",
                            "model": "gpt-5.6-sol",
                            "displayName": "GPT-5.6-Sol",
                            "description": "Frontier model for complex professional work.",
                            "hidden": False,
                            "isDefault": True,
                            "defaultReasoningEffort": "medium",
                            "supportedReasoningEfforts": [
                                {"reasoningEffort": effort, "description": effort}
                                for effort in (
                                    "low",
                                    "medium",
                                    "high",
                                    "xhigh",
                                    "max",
                                    "ultra",
                                )
                            ],
                            "inputModalities": ["text", "image"],
                        }
                    ],
                    "nextCursor": None,
                },
            },
        ]
    )
    popen_calls: list[list[str]] = []
    popen_environments: list[dict[str, str]] = []
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "bridge-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_realistic_secret_carrier")
    monkeypatch.setenv("NO_PROXY", "supervisor,homeassistant,metadata")

    def fake_popen(command, **kwargs):
        popen_calls.append(command)
        popen_environments.append(kwargs.get("env"))
        return process

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    codex_path = tmp_path / "codex.exe"
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(codex_path),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    assert popen_calls == [[str(codex_path), "app-server", "--stdio"]]
    assert popen_environments[0] is not None
    assert "CODEX_BRIDGE_AUTH_TOKEN" not in popen_environments[0]
    assert "GITHUB_TOKEN" not in popen_environments[0]
    assert "NO_PROXY" not in popen_environments[0]
    assert "PATH" in popen_environments[0]
    assert popen_environments[0]["CODEX_HOME"] == str(tmp_path)
    assert popen_environments[0]["HOME"] == str(tmp_path)
    assert catalog.source == "codex-app-server"
    assert catalog.default_model == "gpt-5.6-sol"
    assert catalog.default_thinking_level == "ultra"
    assert catalog.configured_model == "gpt-5.6-sol"
    assert catalog.configured_thinking_level == "ultra"
    assert [model.model for model in catalog.models] == ["gpt-5.6-sol"]
    assert catalog.models[0].thinking_levels == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert catalog.models[0].input_modalities == ["text", "image"]
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [request["method"] for request in requests] == [
        "initialize",
        "initialized",
        "config/read",
        "model/list",
    ]
    assert requests[0]["params"]["clientInfo"]["version"] == model_catalog.__version__


def test_probe_follows_model_list_pagination_and_filters_hidden_models(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [
                        _model_payload("gpt-5.6-sol", is_default=True),
                        _model_payload("hidden-review-model", hidden=True),
                    ],
                    "nextCursor": "page-2",
                },
            },
            {
                "id": 4,
                "result": {
                    "data": [_model_payload("gpt-5.6-terra")],
                    "nextCursor": None,
                },
            },
        ]
    )
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    assert [model.model for model in catalog.models] == ["gpt-5.6-sol", "gpt-5.6-terra"]
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[-1] == {
        "id": 4,
        "method": "model/list",
        "params": {"includeHidden": False, "limit": 100, "cursor": "page-2"},
    }


def test_probe_accepts_legacy_items_pages_and_deduplicates_models_and_efforts(
    tmp_path,
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()
    first_model = _model_payload("gpt-5.6-sol", is_default=True)
    first_model["supportedReasoningEfforts"] = [
        "low",
        {"reasoningEffort": "medium", "description": "Balanced"},
        "low",
        {"reasoningEffort": "medium", "description": "Duplicate"},
        "",
        {"reasoningEffort": ""},
    ]
    duplicate_model = _model_payload("gpt-5.6-sol")
    duplicate_model["supportedReasoningEfforts"] = ["high"]
    second_model = _model_payload("gpt-5.6-terra")
    second_model["supportedReasoningEfforts"] = [
        {"reasoningEffort": "medium"},
        "high",
        "high",
    ]
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "items": [first_model, duplicate_model],
                    "nextCursor": "legacy-page-2",
                    "pageSize": 2,
                },
            },
            {
                "id": 4,
                "result": {
                    "items": [second_model],
                    "nextCursor": None,
                    "pageSize": 1,
                },
            },
        ]
    )
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    assert [model.model for model in catalog.models] == ["gpt-5.6-sol", "gpt-5.6-terra"]
    assert catalog.models[0].thinking_levels == ["low", "medium"]
    assert catalog.models[1].thinking_levels == ["medium", "high"]
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[-1] == {
        "id": 4,
        "method": "model/list",
        "params": {"includeHidden": False, "limit": 100, "cursor": "legacy-page-2"},
    }


def test_non_configured_model_does_not_inherit_configured_reasoning_effort(
    tmp_path,
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()
    configured_model = _model_payload("gpt-5.6-sol", is_default=True)
    configured_model["supportedReasoningEfforts"] = ["medium", "ultra"]
    other_model = _model_payload("gpt-5.6-luna")
    other_model.pop("defaultReasoningEffort")
    other_model["supportedReasoningEfforts"] = ["medium", "high", "max"]
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "config": {
                        "model": "gpt-5.6-sol",
                        "model_reasoning_effort": "ultra",
                    }
                },
            },
            {
                "id": 3,
                "result": {
                    "data": [configured_model, other_model],
                    "nextCursor": None,
                },
            },
        ]
    )
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    luna = next(model for model in catalog.models if model.model == "gpt-5.6-luna")
    assert luna.default_thinking_level == "medium"


def test_probe_preserves_configured_model_when_catalog_does_not_list_it(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "config": {
                        "model": "gpt-5.6-sol",
                        "model_reasoning_effort": "ultra",
                    },
                    "origins": {},
                },
            },
            {
                "id": 3,
                "result": {
                    "data": [_model_payload("gpt-5.5", is_default=True)],
                    "nextCursor": None,
                },
            },
        ]
    )
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    assert [model.model for model in catalog.models] == ["gpt-5.6-sol", "gpt-5.5"]
    configured = catalog.models[0]
    assert configured.is_default is True
    assert configured.catalogued is False
    assert configured.default_thinking_level == "ultra"
    assert configured.thinking_levels == ["ultra"]


def test_probe_returns_safe_fallback_when_codex_discovery_fails(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()

    def fail_to_start(*args, **kwargs):
        raise OSError("executable unavailable")

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fail_to_start)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "missing-codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=0.1,
    )

    catalog = probe.probe()

    assert catalog.source == "fallback"
    assert catalog.stale is True
    assert catalog.models
    assert catalog.default_model == catalog.models[0].model
    assert "unavailable" in (catalog.error or "")


def test_repeated_discovery_failures_do_not_label_static_fallback_last_known_good(
    tmp_path,
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()
    monkeypatch.setattr(
        model_catalog.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("still unavailable")),
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "missing-codex.exe"),
        timeout_seconds=0.1,
        cache_ttl_seconds=0,
    )

    first = probe.probe()
    second = probe.probe()

    assert first.source == "fallback"
    assert second.source == "fallback"


def test_probe_caches_successful_catalog_between_status_polls(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [_model_payload("gpt-5.6-sol", is_default=True)],
                    "nextCursor": None,
                },
            },
        ]
    )
    launches = 0

    def fake_popen(*args, **kwargs):
        nonlocal launches
        launches += 1
        return process

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
        cache_ttl_seconds=600,
    )

    first = probe.probe()
    second = probe.probe()

    assert launches == 1
    assert second == first


def test_probe_refreshes_stale_cache_before_ttl_expires(tmp_path, monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    recovered_process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [_model_payload("gpt-5.6-sol", is_default=True)],
                    "nextCursor": None,
                },
            },
        ]
    )
    launches = 0

    def fake_popen(*args, **kwargs):
        nonlocal launches
        launches += 1
        if launches == 1:
            raise OSError("temporary startup outage")
        return recovered_process

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
        cache_ttl_seconds=600,
    )

    fallback = probe.probe()
    cached_fallback = probe.probe()
    recovered = probe.probe(refresh_stale=True)
    cached_recovery = probe.probe(refresh_stale=True)

    assert fallback.source == "fallback"
    assert cached_fallback == fallback
    assert recovered.source == "codex-app-server"
    assert recovered.stale is False
    assert [model.model for model in recovered.models] == ["gpt-5.6-sol"]
    assert cached_recovery == recovered
    assert launches == 2


def test_concurrent_stale_refreshes_share_one_failed_attempt_then_allow_retry(
    tmp_path,
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()
    recovered_process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [_model_payload("gpt-5.6-sol", is_default=True)],
                    "nextCursor": None,
                },
            },
        ]
    )
    retry_started = Event()
    release_retry = Event()
    waiter_timestamped = Event()
    launches = 0

    def fake_popen(*args, **kwargs):
        nonlocal launches
        launches += 1
        if launches == 1:
            raise OSError("startup outage")
        if launches == 2:
            retry_started.set()
            assert release_retry.wait(timeout=2)
            raise OSError("recovery still pending")
        return recovered_process

    real_monotonic = model_catalog.monotonic

    def tracked_monotonic():
        if current_thread().name == "catalog-waiter":
            waiter_timestamped.set()
        return real_monotonic()

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(model_catalog, "monotonic", tracked_monotonic)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        timeout_seconds=1,
        cache_ttl_seconds=600,
    )
    assert probe.probe().source == "fallback"

    results: list[object] = []

    def refresh() -> None:
        results.append(probe.probe(refresh_stale=True))

    leader = Thread(target=refresh, name="catalog-leader")
    waiter = Thread(target=refresh, name="catalog-waiter")
    leader.start()
    assert retry_started.wait(timeout=1)
    waiter.start()
    waiter_observed = waiter_timestamped.wait(timeout=1)
    release_retry.set()
    leader.join(timeout=2)
    waiter.join(timeout=2)

    assert waiter_observed
    assert not leader.is_alive()
    assert not waiter.is_alive()
    assert len(results) == 2
    assert [catalog.source for catalog in results] == ["fallback", "fallback"]
    assert launches == 2

    recovered = probe.probe(refresh_stale=True)
    assert recovered.source == "codex-app-server"
    assert launches == 3


def test_probe_keeps_last_known_catalog_when_refresh_fails(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [_model_payload("gpt-5.6-sol", is_default=True)],
                    "nextCursor": None,
                },
            },
        ]
    )
    launches = 0

    def fake_popen(*args, **kwargs):
        nonlocal launches
        launches += 1
        if launches == 1:
            return process
        raise OSError("temporary outage")

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        codex_home=tmp_path,
        timeout_seconds=1,
        cache_ttl_seconds=0,
    )

    fresh = probe.probe()
    stale = probe.probe()

    assert [model.model for model in stale.models] == [
        model.model for model in fresh.models
    ]
    assert stale.source == "last-known-good"
    assert stale.stale is True
    assert "unavailable" in (stale.error or "")
    assert "temporary outage" not in (stale.error or "")


def test_bundled_catalog_recovers_dynamic_models_and_reasoning_levels(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    codex_path = tmp_path / "codex.exe"
    codex_path.write_bytes(b"signed codex placeholder")
    process = BundledProcess(json.dumps(_bundled_payload()).encode("utf-8"))
    popen_calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        popen_calls.append(command)
        assert kwargs["stdin"] is model_catalog.subprocess.DEVNULL
        assert kwargs["stderr"] is model_catalog.subprocess.DEVNULL
        assert kwargs["text"] is False
        return process

    monkeypatch.setattr(model_catalog.subprocess, "Popen", fake_popen)
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(codex_path),
        codex_home=tmp_path,
        timeout_seconds=1,
    )
    monkeypatch.setattr(
        probe,
        "_discover_from_app_server",
        lambda: (_ for _ in ()).throw(model_catalog.ModelCatalogError("timeout")),
    )

    catalog = probe.probe()

    assert catalog.source == "codex-bundled"
    assert catalog.stale is True
    assert catalog.default_model == "gpt-5.6-sol"
    assert catalog.default_thinking_level == "low"
    assert [model.model for model in catalog.models] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert catalog.models[0].thinking_levels == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert catalog.models[0].input_modalities == ["text", "image"]
    assert "using the bundled catalogue" in (catalog.error or "")
    assert popen_calls == [[str(codex_path), "debug", "models", "--bundled"]]


def test_shared_app_server_probe_uses_bundled_recovery(tmp_path, monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    codex_path = tmp_path / "codex.exe"
    codex_path.write_bytes(b"signed codex placeholder")
    process = BundledProcess(json.dumps(_bundled_payload()).encode("utf-8"))

    class UnavailableClient:
        generation = 1

        def request(self, method, params=None, **kwargs):
            raise model_catalog.ModelCatalogError("model/list timed out")

    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.AppServerModelCatalogProbe(
        UnavailableClient(),
        codex_command=str(codex_path),
        codex_home=tmp_path,
        timeout_seconds=1,
    )

    catalog = probe.probe()

    assert catalog.source == "codex-bundled"
    assert catalog.default_model == "gpt-5.6-sol"
    assert catalog.stale is True


def test_bundled_catalog_rejects_oversized_output_before_json_parse(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    codex_path = tmp_path / "codex.exe"
    codex_path.write_bytes(b"signed codex placeholder")
    process = BundledProcess(b"{" + b"x" * (1_048_576 + 32))
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(codex_path),
        timeout_seconds=1,
    )

    with pytest.raises(model_catalog.ModelCatalogError, match="size limit"):
        probe._discover_from_bundled()

    assert process.terminated is True


def test_bundled_catalog_failure_uses_static_fallback(tmp_path, monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "missing-codex.exe"),
        timeout_seconds=0.1,
    )
    monkeypatch.setattr(
        probe,
        "_discover_from_app_server",
        lambda: (_ for _ in ()).throw(model_catalog.ModelCatalogError("timeout")),
    )
    monkeypatch.setattr(
        probe,
        "_discover_from_bundled",
        lambda: (_ for _ in ()).throw(model_catalog.ModelCatalogError("invalid")),
    )

    catalog = probe.probe()

    assert catalog.source == "fallback"
    assert catalog.stale is True
    assert catalog.default_model == "gpt-5.5"
    assert "unavailable" in (catalog.error or "")


def test_verified_last_known_good_precedes_bundled_recovery(monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    probe = model_catalog.CodexModelCatalogProbe(cache_ttl_seconds=0)
    fresh = model_catalog.CodexModelCatalogProbe._build_catalog(
        {"config": {}},
        {"data": [_model_payload("gpt-5.6-sol", is_default=True)]},
    )
    monkeypatch.setattr(probe, "_discover_from_app_server", lambda: fresh)
    assert probe.probe().source == "codex-app-server"

    monkeypatch.setattr(
        probe,
        "_discover_from_app_server",
        lambda: (_ for _ in ()).throw(model_catalog.ModelCatalogError("timeout")),
    )
    monkeypatch.setattr(
        probe,
        "_discover_from_bundled",
        lambda: (_ for _ in ()).throw(
            AssertionError("bundled recovery should not win")
        ),
    )

    stale = probe.probe()
    repeated = probe.probe(refresh_stale=True)

    assert stale.source == "last-known-good"
    assert stale.stale is True
    assert stale.default_model == "gpt-5.6-sol"
    assert repeated.source == "last-known-good"
    assert repeated.default_model == "gpt-5.6-sol"


def test_shared_probe_retains_last_known_good_across_repeated_failures(
    monkeypatch,
) -> None:
    model_catalog = _load_model_catalog_module()

    class RecoveringClient:
        generation = 1

        def __init__(self) -> None:
            self.available = True

        def request(self, method, params=None, **kwargs):
            if not self.available:
                raise model_catalog.ModelCatalogError("model/list timed out")
            if method == "config/read":
                return {"config": {}, "origins": {}}
            if method == "model/list":
                return {"data": [_model_payload("gpt-5.6-sol", is_default=True)]}
            raise AssertionError(f"Unexpected request: {method}")

    client = RecoveringClient()
    probe = model_catalog.AppServerModelCatalogProbe(
        client,
        cache_ttl_seconds=0,
    )
    monkeypatch.setattr(
        probe._bundled_probe,
        "_discover_from_bundled",
        lambda: (_ for _ in ()).throw(
            AssertionError("bundled recovery should not replace verified live data")
        ),
    )

    fresh = probe.probe()
    client.available = False
    stale = probe.probe()
    repeated = probe.probe(refresh_stale=True)

    assert fresh.source == "codex-app-server"
    assert stale.source == "last-known-good"
    assert repeated.source == "last-known-good"
    assert repeated.default_model == "gpt-5.6-sol"


def test_stale_catalogue_retries_after_short_ttl_without_hammering(monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    clock = [0.0]
    attempts = 0
    monkeypatch.setattr(model_catalog, "monotonic", lambda: clock[0])
    probe = model_catalog.CodexModelCatalogProbe(
        cache_ttl_seconds=600,
        stale_retry_ttl_seconds=5,
    )

    def unavailable():
        nonlocal attempts
        attempts += 1
        raise model_catalog.ModelCatalogError("timeout")

    monkeypatch.setattr(probe, "_discover_from_app_server", unavailable)
    monkeypatch.setattr(probe, "_discover_from_bundled", unavailable)

    first = probe.probe()
    clock[0] = 4.9
    cached = probe.probe()
    clock[0] = 5.1
    retried = probe.probe()

    assert first.source == "fallback"
    assert cached is first
    assert retried.source == "fallback"
    assert attempts == 4


def test_direct_probe_rejects_an_oversized_aggregate_model_count(
    tmp_path, monkeypatch
) -> None:
    model_catalog = _load_model_catalog_module()
    process = ScriptedProcess(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"config": {}, "origins": {}}},
            {
                "id": 3,
                "result": {
                    "data": [
                        _model_payload(f"model-{index}")
                        for index in range(model_catalog._MAX_MODEL_CATALOG_MODELS + 1)
                    ]
                },
            },
        ]
    )
    monkeypatch.setattr(
        model_catalog.subprocess, "Popen", lambda *args, **kwargs: process
    )
    probe = model_catalog.CodexModelCatalogProbe(
        codex_command=str(tmp_path / "codex.exe"),
        timeout_seconds=1,
    )

    with pytest.raises(model_catalog.ModelCatalogError, match="oversized"):
        probe._discover_from_app_server()


def test_direct_reader_bounds_messages_and_ignores_notification_floods() -> None:
    model_catalog = _load_model_catalog_module()
    messages = model_catalog.Queue(
        maxsize=model_catalog._MAX_PENDING_APP_SERVER_MESSAGES
    )
    stream = io.StringIO(
        "".join(
            json.dumps({"method": "turn/progress", "params": {"index": index}})
            + "\n"
            for index in range(model_catalog._MAX_PENDING_APP_SERVER_MESSAGES * 2)
        )
        + json.dumps({"id": 7, "result": {"data": []}})
        + "\n"
    )

    model_catalog.CodexModelCatalogProbe._read_messages(stream, messages)

    assert model_catalog.CodexModelCatalogProbe._wait_for_response(
        messages, 7, model_catalog.monotonic() + 1
    ) == {"data": []}


def test_direct_reader_rejects_an_oversized_json_line() -> None:
    model_catalog = _load_model_catalog_module()
    messages = model_catalog.Queue()
    stream = io.StringIO(
        "{" + "x" * model_catalog._MAX_APP_SERVER_MESSAGE_CHARS + "}\n"
    )

    model_catalog.CodexModelCatalogProbe._read_messages(stream, messages)

    with pytest.raises(model_catalog.ModelCatalogError, match="size limit"):
        model_catalog.CodexModelCatalogProbe._wait_for_response(
            messages, 1, model_catalog.monotonic() + 1
        )


def test_shared_probe_starts_stale_retry_ttl_after_discovery(monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    clock = [0.0]
    attempts = 0
    monkeypatch.setattr(model_catalog, "monotonic", lambda: clock[0])

    class SlowUnavailableClient:
        generation = 1

        def request(self, method, params=None, **kwargs):
            nonlocal attempts
            attempts += 1
            clock[0] = 20.0
            raise model_catalog.ModelCatalogError("model/list timed out")

    probe = model_catalog.AppServerModelCatalogProbe(
        SlowUnavailableClient(),
        timeout_seconds=30,
        stale_retry_ttl_seconds=15,
    )
    bundled = model_catalog.CodexModelCatalogProbe._build_bundled_catalog(
        _bundled_payload()
    )
    monkeypatch.setattr(
        probe._bundled_probe, "_discover_from_bundled", lambda: bundled
    )

    first = probe.probe()
    cached = probe.probe()

    assert first.source == "codex-bundled"
    assert cached is first
    assert attempts == 1


def test_shared_probe_bounds_aggregate_catalogue_bytes(monkeypatch) -> None:
    model_catalog = _load_model_catalog_module()
    model_calls = 0
    large_description = "x" * 900_000

    class OversizedClient:
        generation = 1

        def request(self, method, params=None, **kwargs):
            nonlocal model_calls
            if method == "config/read":
                return {"config": {}, "origins": {}}
            if method == "model/list":
                model_calls += 1
                return {
                    "data": [
                        {
                            **_model_payload(f"model-{model_calls}"),
                            "description": large_description,
                        }
                    ],
                    "nextCursor": f"cursor-{model_calls}",
                }
            raise AssertionError(f"Unexpected request: {method}")

    probe = model_catalog.AppServerModelCatalogProbe(
        OversizedClient(),
        timeout_seconds=10,
    )
    monkeypatch.setattr(
        probe._bundled_probe,
        "_discover_from_bundled",
        lambda: (_ for _ in ()).throw(model_catalog.ModelCatalogError("offline")),
    )

    catalog = probe.probe()

    assert catalog.source == "fallback"
    assert model_calls == 5


def test_catalog_parsers_bound_fields_and_ignore_unsupported_defaults() -> None:
    model_catalog = _load_model_catalog_module()
    oversized_display = "d" * (model_catalog._MAX_MODEL_DISPLAY_NAME_CHARS + 1)
    oversized_description = "x" * (model_catalog._MAX_MODEL_DESCRIPTION_CHARS + 1)
    efforts = [
        {"effort": f"level-{index}"}
        for index in range(model_catalog._MAX_REASONING_LEVELS + 5)
    ]
    modalities = [
        f"modality-{index}"
        for index in range(model_catalog._MAX_INPUT_MODALITIES + 5)
    ]

    bundled = model_catalog.CodexModelCatalogProbe._build_bundled_catalog(
        {
            "models": [
                {
                    "slug": "bounded-model",
                    "display_name": oversized_display,
                    "description": oversized_description,
                    "visibility": "list",
                    "default_reasoning_level": "unsupported",
                    "supported_reasoning_levels": efforts,
                    "input_modalities": modalities,
                }
            ]
        }
    ).models[0]
    live = model_catalog.CodexModelCatalogProbe._build_catalog(
        {"config": {}},
        {
            "data": [
                {
                    "model": "bounded-model",
                    "displayName": oversized_display,
                    "description": oversized_description,
                    "defaultReasoningEffort": "unsupported",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": entry["effort"]} for entry in efforts
                    ],
                    "inputModalities": modalities,
                    "isDefault": True,
                }
            ]
        },
    ).models[0]

    for record in (bundled, live):
        assert record.display_name == "bounded-model"
        assert record.description is None
        assert record.default_thinking_level == "level-0"
        assert len(record.thinking_levels) == model_catalog._MAX_REASONING_LEVELS
        assert len(record.input_modalities) == model_catalog._MAX_INPUT_MODALITIES
